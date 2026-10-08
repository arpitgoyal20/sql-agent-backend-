"""Deterministic SQL validation (§8). The LLM never decides validity; this module does.

`validate()` runs the checks in order and stops at the first failing category. Error strings
start with a CATEGORY prefix and are written to be fed straight back to the LLM on retry.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from app import db
from app.schema_loader import SchemaInfo

Dialect = str  # "sqlite" | "postgres" | "mysql"

# Node types that can write, change schema, or reach outside the database.
_WRITE_NODES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, name, None)
        for name in (
            "Insert", "Update", "Delete", "Drop", "Alter", "TruncateTable", "Create", "Merge",
            "Command", "Pragma", "Attach", "Detach", "Into", "Copy", "LoadData", "Grant",
            "Revoke", "Use", "Set", "Analyze",
        )
    )
    if t is not None
)

# Functions that touch the filesystem or load code. Not "writes", but never acceptable.
_FORBIDDEN_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}

_SELECT_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

_ALLOWED_SCHEMAS = {"", "main"}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The query as it will run on SQLite: the original text for SQLite input, a transpiled
    # version for other dialects. None when the query is invalid or could not be transpiled.
    sqlite_sql: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def destructive(self) -> bool:
        return any(e.startswith("DESTRUCTIVE") for e in self.errors)


def validate(sql: str, schema: SchemaInfo, dialect: Dialect = "sqlite") -> ValidationResult:
    result = ValidationResult()
    text = (sql or "").strip()

    # 1. Parse -------------------------------------------------------------------------
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except SqlglotError as e:
        result.errors.append(f"SYNTAX: {_first_line(e)}")
        return result
    if not statements:
        result.errors.append("SYNTAX: the query is empty")
        return result

    # 2. Single statement ------------------------------------------------------------------
    if len(statements) > 1:
        result.errors.append("MULTI: exactly one statement allowed")
        # Still flag a hidden write (`SELECT 1; DROP TABLE x`) so routing refuses outright.
        if any(_write_reason(s) for s in statements):
            result.errors.append("DESTRUCTIVE: only SELECT queries are allowed")
        return result
    tree = statements[0]

    # 3. Read-only ---------------------------------------------------------------------
    reason = _write_reason(tree)
    if reason:
        result.errors.append(f"DESTRUCTIVE: only SELECT queries are allowed ({reason})")
        return result
    root = tree.unnest() if isinstance(tree, exp.Subquery) else tree
    if not isinstance(root, _SELECT_ROOTS):
        result.errors.append(
            f"NOT_SELECT: the statement must be a SELECT query, got {root.key.upper()}"
        )
        return result

    # 4. Tables exist ------------------------------------------------------------------
    table_errors = _check_tables(root, schema)
    if table_errors:
        result.errors.extend(table_errors)
        return result

    # 5. Columns exist -----------------------------------------------------------------
    try:
        qualified = qualify(
            _lowercase_identifiers(root.copy()),
            schema=schema.schema_dict,
            dialect=dialect,
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except (OptimizeError, SqlglotError) as e:
        result.errors.append(_column_error(e, root, schema))
        return result

    # 6. Joins against declared foreign keys (warnings only) ---------------------------
    result.warnings.extend(_check_joins(qualified, schema))

    # Prepare the SQLite text used for step 7 and for execution.
    if dialect == "sqlite" and root is tree:
        result.sqlite_sql = text.rstrip(";").strip()
    elif dialect == "sqlite":
        result.sqlite_sql = root.sql(dialect="sqlite")  # SQLite rejects `(SELECT ...)`
    else:
        try:
            result.sqlite_sql = sqlglot.transpile(
                text, read=dialect, write="sqlite", unsupported_level=ErrorLevel.RAISE
            )[0]
        except SqlglotError as e:
            result.warnings.append(
                f"NOT_EXECUTABLE: could not translate to SQLite for execution: {_first_line(e)}"
            )

    # 7. Database check (SQLite only) --------------------------------------------------
    if dialect == "sqlite":
        try:
            db.explain_query_plan(result.sqlite_sql)
        except sqlite3.Error as e:
            result.errors.append(f"DB: {e}")
            result.sqlite_sql = None

    return result


def transpile(sql: str, read: Dialect, write: Dialect, pretty: bool = False) -> str:
    """Convenience wrapper used for display formatting and dialect conversion."""
    return sqlglot.transpile(sql, read=read, write=write, pretty=pretty)[0]


# ---- helpers ------------------------------------------------------------------------


def _first_line(e: Exception) -> str:
    # sqlglot errors include ANSI-highlighted context on later lines; keep the summary.
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()
    return msg.splitlines()[0] if msg else type(e).__name__


def _write_reason(tree: exp.Expression) -> str | None:
    """Name of the first write/unsafe construct anywhere in the tree, or None."""
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            if isinstance(node, exp.Command):
                return str(node.this).upper()
            if isinstance(node, exp.Into):
                return "SELECT INTO"
            return node.key.upper()
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCTIONS:
                return f"function {name}()"
    return None


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}


def _check_tables(tree: exp.Expression, schema: SchemaInfo) -> list[str]:
    ctes = _cte_names(tree)
    known = schema.schema_dict
    unknown: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:  # table-valued function or similar; columns check will catch misuse
            continue
        qualifier = (table.db or "").lower()
        if qualifier not in _ALLOWED_SCHEMAS:
            unknown.append(f"{table.db}.{name}")
            continue
        if name.lower() in ctes and not qualifier:
            continue
        if name.lower() not in known and name not in unknown:
            unknown.append(name)

    errors = []
    available = ", ".join(schema.table_names)
    for name in unknown:
        hint = difflib.get_close_matches(name.lower(), list(known), n=1, cutoff=0.6)
        did_you_mean = f" Did you mean {schema.resolve_table(hint[0])}?" if hint else ""
        errors.append(f"UNKNOWN_TABLE: {name}.{did_you_mean} Available: {available}")
    return errors


def _lowercase_identifiers(tree: exp.Expression) -> exp.Expression:
    """SQLite resolves names case-insensitively; mirror that for every input dialect."""
    for ident in tree.find_all(exp.Identifier):
        ident.set("this", ident.this.lower())
        ident.set("quoted", False)
    return tree


_COLUMN_RE = re.compile(r"""Column '"?([^"']+)"?' could not be resolved|Unknown column: (\S+)""")


def _column_error(e: Exception, tree: exp.Expression, schema: SchemaInfo) -> str:
    """Turn a qualify() failure into an actionable message for the LLM."""
    message = _first_line(e)
    match = _COLUMN_RE.search(message)
    referenced = []
    for t in tree.find_all(exp.Table):
        real = schema.resolve_table(t.name)
        if real and real not in referenced:
            referenced.append(real)
    listing = "; ".join(
        f"{t}({', '.join(c.name for c in schema.columns_of(t))})" for t in referenced
    )
    if not match:
        return f"UNKNOWN_COLUMN: {message}. Columns available: {listing}"

    column = (match.group(1) or match.group(2)).strip('"').lower()
    owners = [
        t for t in referenced if any(c.name.lower() == column for c in schema.columns_of(t))
    ]
    if len(owners) > 1:
        return (
            f"UNKNOWN_COLUMN: column '{column}' is ambiguous; it exists in "
            f"{' and '.join(owners)}. Qualify it with a table name or alias."
        )
    if owners:
        # The column exists but not where it was used (wrong alias, or not in a subquery's
        # select list). Report sqlglot's message with the listing.
        return f"UNKNOWN_COLUMN: {message}. Columns available: {listing}"
    return f"UNKNOWN_COLUMN: column '{column}' does not exist. Columns available: {listing}"


def _source_table(scope: Scope, alias: str) -> str | None:
    source = scope.sources.get(alias)
    return source.name if isinstance(source, exp.Table) else None


def _check_joins(tree: exp.Expression, schema: SchemaInfo) -> list[str]:
    warnings: list[str] = []
    for scope in traverse_scope(tree):
        for join in scope.expression.args.get("joins") or []:
            on = join.args.get("on")
            if on is None:
                continue
            for eq in on.find_all(exp.EQ):
                left, right = eq.left, eq.right
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                lt, rt = _source_table(scope, left.table), _source_table(scope, right.table)
                if not (lt and rt):
                    continue  # one side is a CTE or subquery; nothing declared to compare with
                if not schema.is_fk_pair(lt, left.name, rt, right.name):
                    l_name = f"{schema.resolve_table(lt)}.{_column_case(schema, lt, left.name)}"
                    r_name = f"{schema.resolve_table(rt)}.{_column_case(schema, rt, right.name)}"
                    warnings.append(
                        f"NON_FK_JOIN: {l_name} = {r_name} is not a declared foreign key; "
                        "check the join is intended"
                    )
    return warnings


def _column_case(schema: SchemaInfo, table: str, column: str) -> str:
    return next(
        (c.name for c in schema.columns_of(table) if c.name.lower() == column.lower()), column
    )


# ---- analysis helpers used by the optimize path ---------------------------------------------


def tables_in(sql: str, schema: SchemaInfo, dialect: Dialect = "sqlite") -> set[str]:
    """Real schema tables referenced by `sql` (original case); empty if it does not parse."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return set()
    ctes = _cte_names(tree)
    out = set()
    for t in tree.find_all(exp.Table):
        real = schema.resolve_table(t.name) if t.name.lower() not in ctes else None
        if real:
            out.add(real)
    return out


def index_candidates(sql: str, schema: SchemaInfo, dialect: Dialect = "sqlite") -> list[str]:
    """CREATE INDEX advice for unindexed columns used in WHERE, JOIN ... ON or ORDER BY.

    Advice text only; nothing here is ever executed.
    """
    try:
        tree = qualify(
            _lowercase_identifiers(sqlglot.parse_one(sql, read=dialect)),
            schema=schema.schema_dict,
            dialect=dialect,
            validate_qualify_columns=True,
            quote_identifiers=False,
        )
    except SqlglotError:
        return []

    wanted: list[tuple[str, str]] = []
    for scope in traverse_scope(tree):
        select = scope.expression
        clauses = [select.args.get("where"), select.args.get("order")]
        clauses += [j.args.get("on") for j in select.args.get("joins") or []]
        for clause in filter(None, clauses):
            for col in clause.find_all(exp.Column):
                table = _source_table(scope, col.table)
                if table and (table, col.name) not in wanted:
                    wanted.append((table, col.name))

    advice = []
    for table, column in wanted:
        real = schema.resolve_table(table)
        if not real or column.lower() in schema.indexed_columns(real):
            continue
        col = _column_case(schema, real, column)
        advice.append(f"CREATE INDEX idx_{real.lower()}_{col.lower()} ON {real}({col});")
    return advice


def check_index_suggestion(text: str, schema: SchemaInfo) -> str | None:
    """Normalise an LLM index suggestion; None if it isn't a CREATE INDEX on real,
    currently unindexed columns."""
    try:
        tree = sqlglot.parse_one(text.strip().rstrip(";"), read="sqlite")
    except SqlglotError:
        return None
    if not isinstance(tree, exp.Create) or (tree.args.get("kind") or "").upper() != "INDEX":
        return None
    index = tree.this
    table_node = index.args.get("table") if isinstance(index, exp.Index) else None
    params = index.args.get("params") if isinstance(index, exp.Index) else None
    if table_node is None or params is None:
        return None
    real = schema.resolve_table(table_node.name)
    columns = [c.name for c in params.args.get("columns") or [] if c.name]
    known = {c.name.lower() for c in schema.columns_of(real or "")}
    if not real or not columns or any(c.lower() not in known for c in columns):
        return None
    if columns[0].lower() in schema.indexed_columns(real):
        return None
    cols = ", ".join(_column_case(schema, real, c) for c in columns)
    name = index.name or f"idx_{real.lower()}_{'_'.join(c.lower() for c in columns)}"
    return f"CREATE INDEX {name} ON {real}({cols});"
