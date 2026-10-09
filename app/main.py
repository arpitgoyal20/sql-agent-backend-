"""FastAPI app (§12): chat over SSE, thread history, schema, health."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

import aiosqlite
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sse_starlette.sse import EventSourceResponse

from app import db as sample_db
from app import workbench
from app.config import get_settings
from app.events import pretty_sql, run_turn
from app.inspector import checklist, inspect
from app.graph import compile_graph
from app.nodes.common import message_text
from app.schema_loader import get_schema
from app.validator import validate

log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Per-request INFO lines from the HTTP client and the Gemini SDK ("AFC is enabled...") are noise.
for noisy in ("httpx", "google_genai", "google_genai.models"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

settings = get_settings()

CLIENT_HEADER = "X-Client-Id"
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Paths that work without a client id (Render's health check cannot send headers).
PUBLIC_PATHS = {"/api/health"}


def client_id(request: Request) -> str:
    """The caller's client id; set by the middleware for every /api request."""
    return request.state.client_id


def rate_limit_key(request: Request) -> str:
    """Rate-limit per client id, falling back to the IP address."""
    value = request.headers.get(CLIENT_HEADER, "")
    return f"client:{value.lower()}" if UUID_RE.match(value) else get_remote_address(request)

THREADS_DDL = """
CREATE TABLE IF NOT EXISTS threads (
  thread_id  TEXT PRIMARY KEY,
  client_id  TEXT,
  title      TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

SAVED_DDL = """
CREATE TABLE IF NOT EXISTS saved_queries (
  id          TEXT PRIMARY KEY,
  client_id   TEXT,
  title       TEXT NOT NULL,
  prompt      TEXT NOT NULL,
  sql         TEXT NOT NULL,
  dialect     TEXT NOT NULL,
  explanation TEXT NOT NULL,
  created_at  TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- API models -----------------------------------------------------------------------------


class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100)
    # Generous transport limit; the 4,000-char rule is enforced by guard_input with a
    # friendly refusal instead of an HTTP error.
    message: str = Field(max_length=20_000)
    dialect: Literal["sqlite", "postgres", "mysql"] = "sqlite"
    execute: bool = True
    current_sql: str | None = Field(default=None, max_length=20_000)  # the editor's query


class ThreadSummary(BaseModel):
    thread_id: str
    title: str
    updated_at: str


class ThreadMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    intent: str | None = None
    sql: str | None = None
    kind: str | None = None  # assistant only: answer | clarify | refusal
    created_at: str | None = None


class ThreadDetail(BaseModel):
    messages: list[ThreadMessage]
    last_sql: str | None


class RunQueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)  # >10,000 gets a TOO_LONG status
    dialect: Literal["sqlite", "postgres", "mysql"] = "sqlite"
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ExecuteRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)
    dialect: Literal["sqlite", "postgres", "mysql"] = "sqlite"


class SavedQueryIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(default="", max_length=4_000)
    sql: str = Field(min_length=1, max_length=20_000)
    dialect: Literal["sqlite", "postgres", "mysql"] = "sqlite"
    explanation: str = Field(default="", max_length=8_000)


class SavedQuery(SavedQueryIn):
    id: str
    created_at: str


# ---- app --------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    path = settings.checkpoint_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as conn:
        await conn.execute(THREADS_DDL)
        await conn.execute(SAVED_DDL)
        # Databases created before client isolation lack the owner column.
        for table in ("threads", "saved_queries"):
            cur = await conn.execute(f"PRAGMA table_info({table})")
            if "client_id" not in {r[1] for r in await cur.fetchall()}:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN client_id TEXT")
        await conn.commit()
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        app.state.db = conn
        app.state.saver = saver
        app.state.graph = compile_graph(saver)
        app.state.turns = set()  # running chat turns (kept referenced until they finish)
        get_schema()  # load once at startup
        sample_db.row_counts()
        yield
        if app.state.turns:  # let in-flight turns finish saving before the DB closes
            await asyncio.wait(app.state.turns, timeout=30)


def create_app() -> FastAPI:
    app = FastAPI(title="SQL Query AI Agent", version="1.0.0", lifespan=lifespan)
    # One limiter per app instance (in-memory, per client id or IP).
    limiter = Limiter(key_func=rate_limit_key)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Registered before CORS so CORS wraps it: a 400 still carries CORS headers and the
    # browser shows the real error instead of a CORS failure.
    @app.middleware("http")
    async def require_client_id(request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or not path.startswith("/api") or path in PUBLIC_PATHS:
            return await call_next(request)
        value = request.headers.get(CLIENT_HEADER, "")
        if not UUID_RE.match(value):
            return JSONResponse(
                status_code=400, content={"detail": f"Missing or invalid {CLIENT_HEADER} header"}
            )
        request.state.client_id = value.lower()
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_origin_regex=settings.allowed_origin_regex or None,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/tables")
    async def tables() -> dict:
        return workbench.tables_payload()

    @app.get("/api/schema")
    async def schema() -> dict:
        """Kept for older clients; same data as /api/tables."""
        return workbench.tables_payload()

    @app.get("/api/tables/{name}/preview")
    @limiter.limit(settings.query_rate_limit)
    async def preview_table(
        request: Request,
        name: str,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            payload = await run_in_threadpool(workbench.preview, name, limit, offset)
        except sample_db.QueryTimeout:
            return JSONResponse(status_code=200, content={"status": "error", "text": workbench.TIMEOUT_TEXT})
        if payload is None:
            return JSONResponse(
                status_code=404,
                content={"error": "UNKNOWN_TABLE", "available": get_schema().table_names},
            )
        return payload

    @app.post("/api/query/run")
    @limiter.limit(settings.query_rate_limit)
    async def run_query(request: Request, body: RunQueryRequest) -> dict:
        """Run SQL from the editor: no LLM, but validated and read-only like everything else."""
        return await run_in_threadpool(
            workbench.run_query, body.sql, body.dialect, body.limit, body.offset
        )

    @app.get("/api/threads", response_model=list[ThreadSummary])
    async def list_threads(request: Request):
        cur = await request.app.state.db.execute(
            "SELECT thread_id, title, updated_at FROM threads WHERE client_id = ? "
            "ORDER BY updated_at DESC LIMIT 100",
            (client_id(request),),
        )
        rows = await cur.fetchall()
        return [ThreadSummary(thread_id=r[0], title=r[1], updated_at=r[2]) for r in rows]

    @app.get("/api/threads/{thread_id}", response_model=ThreadDetail)
    async def get_thread(thread_id: str, request: Request):
        await _require_owner(request, thread_id)
        snapshot = await request.app.state.graph.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        values = snapshot.values or {}
        if not values.get("messages"):
            raise HTTPException(status_code=404, detail="Thread not found")
        return ThreadDetail(
            messages=[_to_api_message(m) for m in values["messages"]],
            last_sql=values.get("last_sql"),
        )

    @app.delete("/api/threads/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str, request: Request):
        await _require_owner(request, thread_id)
        await request.app.state.saver.adelete_thread(thread_id)
        await request.app.state.db.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
        await request.app.state.db.commit()

    @app.patch("/api/threads/{thread_id}", response_model=ThreadSummary)
    async def rename_thread(thread_id: str, body: RenameRequest, request: Request):
        db = request.app.state.db
        now = _now()
        cur = await db.execute(
            "UPDATE threads SET title = ?, updated_at = ? WHERE thread_id = ? AND client_id = ?",
            (body.title.strip(), now, thread_id, client_id(request)),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Thread not found")
        return ThreadSummary(thread_id=thread_id, title=body.title.strip(), updated_at=now)

    @app.post("/api/threads/{thread_id}/duplicate", response_model=ThreadSummary, status_code=201)
    async def duplicate_thread(thread_id: str, request: Request):
        await _require_owner(request, thread_id)
        graph = request.app.state.graph
        values = (await graph.aget_state({"configurable": {"thread_id": thread_id}})).values or {}
        if not values.get("messages"):
            raise HTTPException(status_code=404, detail="Thread not found")
        db = request.app.state.db
        cur = await db.execute("SELECT title FROM threads WHERE thread_id = ?", (thread_id,))
        row = await cur.fetchone()
        new_id, now = str(uuid.uuid4()), _now()
        title = f"{row[0] if row else 'Chat'} (copy)"[:120]
        # Record the copy as a finished turn so the next message starts a fresh run.
        await graph.aupdate_state(
            {"configurable": {"thread_id": new_id}},
            {"messages": values["messages"], "last_sql": values.get("last_sql")},
            as_node="explain_sql",
        )
        await db.execute(
            "INSERT INTO threads (thread_id, client_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_id, client_id(request), title, now, now),
        )
        await db.commit()
        return ThreadSummary(thread_id=new_id, title=title, updated_at=now)

    @app.post("/api/execute")
    @limiter.limit(settings.rate_limit)
    async def execute_sql(request: Request, body: ExecuteRequest) -> dict:
        """Validate and run SQL directly: deterministic, no LLM, never changes thread state."""
        return await run_in_threadpool(_validate_and_run, body.sql, body.dialect)

    @app.get("/api/saved", response_model=list[SavedQuery])
    async def list_saved(request: Request):
        cur = await request.app.state.db.execute(
            "SELECT id, title, prompt, sql, dialect, explanation, created_at "
            "FROM saved_queries WHERE client_id = ? ORDER BY created_at DESC LIMIT 200",
            (client_id(request),),
        )
        cols = ["id", "title", "prompt", "sql", "dialect", "explanation", "created_at"]
        return [SavedQuery(**dict(zip(cols, r))) for r in await cur.fetchall()]

    @app.post("/api/saved", response_model=SavedQuery, status_code=201)
    async def save_query(body: SavedQueryIn, request: Request):
        # Saved SQL must itself be valid and read-only.
        check = validate(body.sql, get_schema(), body.dialect)
        if not check.ok:
            raise HTTPException(status_code=422, detail=check.errors[0])
        item = SavedQuery(id=str(uuid.uuid4()), created_at=_now(), **body.model_dump())
        await request.app.state.db.execute(
            "INSERT INTO saved_queries "
            "(id, client_id, title, prompt, sql, dialect, explanation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.id, client_id(request), item.title, item.prompt, item.sql, item.dialect,
                item.explanation, item.created_at,
            ),
        )
        await request.app.state.db.commit()
        return item

    @app.delete("/api/saved/{saved_id}", status_code=204)
    async def delete_saved(saved_id: str, request: Request):
        await request.app.state.db.execute(
            "DELETE FROM saved_queries WHERE id = ? AND client_id = ?", (saved_id, client_id(request))
        )
        await request.app.state.db.commit()

    @app.post("/api/chat")
    @limiter.limit(settings.rate_limit)
    async def chat(request: Request, body: ChatRequest):
        db = request.app.state.db
        now = _now()
        title = " ".join(body.message.split())[:60] or "New chat"
        owner = client_id(request)
        cur = await db.execute("SELECT client_id FROM threads WHERE thread_id = ?", (body.thread_id,))
        row = await cur.fetchone()
        if row and row[0] != owner:
            raise HTTPException(status_code=404, detail="Thread not found")
        is_new = row is None
        await db.execute(
            "INSERT INTO threads (thread_id, client_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET updated_at = excluded.updated_at",
            (body.thread_id, owner, title, now, now),
        )
        await db.commit()

        # The turn runs as its own task so it finishes and is saved even if the client
        # disconnects (page reload, closed tab); the SSE response only relays its events.
        queue: asyncio.Queue = asyncio.Queue()

        async def run() -> None:
            try:
                async for event, data in run_turn(
                    request.app.state.graph,
                    thread_id=body.thread_id,
                    message=body.message,
                    dialect=body.dialect,
                    execute=body.execute,
                    current_sql=body.current_sql,
                ):
                    if event == "done":
                        await db.execute(
                            "UPDATE threads SET updated_at = ? WHERE thread_id = ?",
                            (_now(), body.thread_id),
                        )
                        if is_new:
                            await _apply_generated_title(request.app, body.thread_id)
                        await db.commit()
                    await queue.put((event, data))
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        request.app.state.turns.add(task)
        task.add_done_callback(request.app.state.turns.discard)

        async def events():
            while (item := await queue.get()) is not None:
                event, data = item
                yield {"event": event, "data": json.dumps(data, default=str)}

        return EventSourceResponse(events(), ping=15)

    return app


async def _require_owner(request: Request, thread_id: str) -> None:
    """404 unless the thread belongs to the calling client (others' threads look missing)."""
    cur = await request.app.state.db.execute(
        "SELECT 1 FROM threads WHERE thread_id = ? AND client_id = ?",
        (thread_id, client_id(request)),
    )
    if await cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="Thread not found")


async def _apply_generated_title(app: FastAPI, thread_id: str) -> None:
    """Replace the placeholder title with the classifier's title after the first turn."""
    values = (await app.state.graph.aget_state({"configurable": {"thread_id": thread_id}})).values
    title = (values or {}).get("thread_title")
    if title:
        await app.state.db.execute(
            "UPDATE threads SET title = ? WHERE thread_id = ?", (title, thread_id)
        )


def _validate_and_run(sql: str, dialect: str) -> dict:
    schema = get_schema()
    check = validate(sql, schema, dialect)
    response = {
        "ok": False,
        "sql": sql.strip(),
        "errors": check.errors,
        "warnings": check.warnings,
        "validation": [],
        "inspection": None,
        "result": None,
    }
    if not check.ok:
        return response
    response.update(
        sql=pretty_sql(sql.strip().rstrip(";"), dialect),
        validation=checklist(check.warnings, dialect),
        inspection=inspect(sql, schema, dialect),
    )
    if not check.sqlite_sql:
        response["errors"] = ["NOT_EXECUTABLE: this query could not be translated to SQLite"]
        return response
    try:
        result = sample_db.execute(check.sqlite_sql)
    except (sqlite3.Error, sample_db.QueryTimeout) as e:
        response["errors"] = [f"EXECUTION: {e}"]
        return response
    response.update(
        ok=True,
        result={
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
        },
    )
    return response


def _to_api_message(m: BaseMessage) -> ThreadMessage:
    meta = m.additional_kwargs or {}
    if m.type == "human":
        return ThreadMessage(role="user", content=message_text(m), created_at=meta.get("created_at"))
    return ThreadMessage(
        role="assistant",
        content=message_text(m),
        intent=meta.get("intent"),
        sql=meta.get("sql"),
        kind=meta.get("kind"),
        created_at=meta.get("created_at"),
    )


app = create_app()
