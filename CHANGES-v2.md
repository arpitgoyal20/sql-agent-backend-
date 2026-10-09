# Backend Changes v2 — Workbench Mode

> Apply on top of `REQUIREMENTS.md`. Where this file conflicts with it, this file wins.
> Goal: support a MySQL Workbench-style UI — table browser, editable SQL editor with a Run button, results grid, and the AI chat side by side.

## 1. Summary of changes

| # | Change | Why |
|---|---|---|
| 1 | `GET /api/tables` with row counts | Left-side table browser |
| 2 | `GET /api/tables/{name}/preview` | Click a table → `SELECT * ... LIMIT` + results |
| 3 | `POST /api/query/run` (no LLM) | Run SQL typed or edited in the editor |
| 4 | Pagination (`limit`, `offset`, `total`) | Browse tables bigger than 200 rows |
| 5 | `current_sql` in `POST /api/chat` | Chat follow-ups build on whatever is in the editor |
| 6 | Client isolation via `X-Client-Id` (if not done yet) | Reviewers don't see each other's threads |

**Non-negotiable:** every path that executes SQL calls `validator.validate()` first and uses the read-only connection. No endpoint executes unvalidated SQL.

## 2. New endpoints

### 2.1 `GET /api/tables`

Returns the browser tree data.

```json
{
  "tables": [
    {
      "name": "Customers",
      "row_count": 500,
      "columns": [
        {"name": "CustomerID", "type": "INTEGER", "pk": true, "fk": null, "doc": "..."},
        {"name": "State", "type": "TEXT", "pk": false, "fk": null, "doc": "full US state name"}
      ]
    },
    {
      "name": "Orders",
      "row_count": 2000,
      "columns": [
        {"name": "CustomerID", "type": "INTEGER", "pk": false,
         "fk": {"table": "Customers", "column": "CustomerID"}, "doc": "..."}
      ]
    }
  ]
}
```

- Row counts computed once at startup (the DB is read-only, so they never change) and cached.
- Keep `GET /api/schema` working (alias to the same data) so nothing breaks.

### 2.2 `GET /api/tables/{name}/preview?limit=100&offset=0`

- `name` must match a table in `schema_dict` (case-insensitive). Unknown → 404 `{"error": "UNKNOWN_TABLE", "available": [...]}`. **Never** interpolate the raw path value into SQL; use the canonical name from the schema.
- Builds SQL server-side: `SELECT * FROM "<Table>" LIMIT {limit} OFFSET {offset};`
- `limit` 1–500 (default 100), `offset` ≥ 0.
- Runs through the validator + read-only executor.

Response:

```json
{
  "sql": "SELECT *\nFROM Customers\nLIMIT 100 OFFSET 0;",
  "columns": ["CustomerID", "Name", "Email", "City", "State", "SignupDate"],
  "rows": [[1, "Acme", "...", "Austin", "Texas", "2024-02-11"]],
  "row_count": 100,
  "total": 500,
  "limit": 100,
  "offset": 0,
  "elapsed_ms": 4
}
```

### 2.3 `POST /api/query/run`

Runs SQL from the editor. **No LLM call.**

Request:
```json
{ "sql": "SELECT ...", "dialect": "sqlite", "limit": 100, "offset": 0 }
```

Steps:
1. Reject if `sql` > 10,000 chars.
2. `validator.validate(sql, schema, dialect)`.
   - Any `DESTRUCTIVE` error → HTTP 200 with `{"status": "refused", "text": <same destructive refusal text as the refuse node>}`.
   - Other errors → HTTP 200 with `{"status": "invalid", "errors": [...], "warnings": [...]}`.
3. If dialect ≠ sqlite, transpile to SQLite.
4. Execute with pagination: wrap as `SELECT * FROM (<user sql>) AS q LIMIT :limit OFFSET :offset`, and compute `total` with `SELECT COUNT(*) FROM (<user sql>) AS q`. If the user's query already has its own LIMIT, respect it (the outer wrap still caps at `limit`).
5. Timeout 5 s (existing progress handler). Timeout → `{"status": "error", "text": "Query took longer than 5 seconds and was stopped."}`.

Success response:
```json
{
  "status": "ok",
  "sql": "<pretty-printed>",
  "columns": [...], "rows": [[...]],
  "row_count": 100, "total": 2000, "limit": 100, "offset": 0,
  "elapsed_ms": 12,
  "warnings": []
}
```

Rate limit: 60/minute per client (cheap, no LLM).

### 2.4 Pagination for chat results

The `result` SSE event gains `total`, `limit`, `offset`. To fetch more pages of a chat-generated query, the frontend calls `POST /api/query/run` with the same SQL and a new `offset` — no new LLM call.

## 3. Chat changes

### 3.1 `current_sql` in the chat request

```json
{
  "thread_id": "uuid",
  "message": "only those from California",
  "dialect": "sqlite",
  "execute": true,
  "current_sql": "SELECT * FROM Customers LIMIT 100;"
}
```

- `current_sql` is whatever is in the editor when the user sends the message (optional).
- In `guard_input`: if `current_sql` is present, non-empty, passes the validator, and differs from the thread's `last_sql`, set `last_sql = current_sql` for this turn. Invalid `current_sql` is ignored (log it).
- Effect: user clicks **Customers** in the browser → editor shows `SELECT * FROM Customers LIMIT 100;` → user types "only California" → the agent modifies *that* query.
- Strip the browser's auto-added `LIMIT/OFFSET` from `current_sql` before using it as `last_sql`, so the model doesn't carry a pagination limit into an aggregate query. (Use sqlglot: remove the Limit/Offset nodes only if they equal the preview defaults.)

### 3.2 Editor actions routed through chat

The frontend will send these as normal chat messages; no backend change needed beyond making sure the classifier handles them:

- "Explain this query:\n```sql\n...\n```" → `explain`
- "Optimize this query:\n```sql\n...\n```" → `optimize`
- "Fix this query:\n```sql\n...\n```\nError: <validator error>" → `debug`

Add one few-shot / test for each phrasing.

## 4. Client isolation (skip if already implemented)

- Every request carries `X-Client-Id: <uuid>`. Missing or malformed → 400.
- `threads` table gets a `client_id` column; `/api/threads`, `/api/threads/{id}`, `DELETE /api/threads/{id}` and `/api/chat` only touch threads owned by that client. Another client's thread → 404.
- CORS `allow_headers` must include `X-Client-Id`.
- Rate limits keyed by `client_id` (fallback IP).

## 5. Tests to add

- `test_tables.py`: `/api/tables` lists all 7 tables with correct row counts; preview for each table returns `total` = row count; unknown table → 404; table name with quotes/semicolons → 404 (injection attempt).
- `test_query_run.py`:
  - valid SELECT → ok with rows, pagination works (`offset` beyond `total` → empty rows, still ok)
  - `DELETE FROM Orders` → refused; `SELECT 1; DROP TABLE Orders` → refused/invalid; `PRAGMA table_info(x)` → refused
  - unknown column → invalid with `UNKNOWN_COLUMN`
  - postgres-dialect SQL runs after transpile
  - long-running query (recursive CTE) → timeout status
- `test_graph.py`: chat with `current_sql = SELECT * FROM Customers LIMIT 100` + "only California" → generated SQL filters `State = 'California'` on Customers and has no `LIMIT 100` carried over (fake LLM; assert the prompt received the stripped previous SQL).
- Client isolation: client A cannot list or read client B's thread.

## 6. README updates

- New endpoints in the API section.
- Note that the editor's Run button and table clicks bypass the LLM but **not** the validator.
- Add "Workbench mode" to the features list.

## 7. Acceptance criteria

- [ ] `/api/tables` returns 7 tables with row counts and FK info
- [ ] Preview of Orders returns 100 rows and `total: 2000`; offset 1900 returns the last 100
- [ ] `/api/query/run` runs a hand-written JOIN and paginates it
- [ ] `/api/query/run` refuses DELETE/UPDATE/DROP/PRAGMA with the standard refusal text
- [ ] Chat follow-up modifies the query currently in the editor
- [ ] All existing tests still pass

## Implementation notes (decisions made while building)

- `/api/health` does not require `X-Client-Id` (Render's health check cannot send it).
- Saved queries (`/api/saved`) are isolated per client like threads.
