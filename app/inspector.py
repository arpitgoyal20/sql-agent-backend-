"""Describe a validated query for the UI: the validation checklist and Query Inspector data.

Pure code over the sqlglot AST; nothing here decides validity (validator.py does).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.schema_loader import SchemaInfo
from app.validator import _lowercase_identifiers, tables_in

CHECKS = [
    ("syntax", "SQL syntax valid"),
    ("single", "Single statement"),
    ("read_only", "Read-only query"),
    ("tables", "Tables exist"),
    ("columns", "Columns exist"),
    ("joins", "Relationships valid"),
    ("database", "Database accepts query"),
]


def checklist(warnings: list[str], dialect: str) -> list[dict]:
    """The checks a passing query went through. Only called for valid queries."""
    out = []
    join_warnings = [w for w in warnings if w.startswith("NON_FK_JOIN")]
    for check, label in CHECKS:
        item = {"check": check, "label": label, "status": "pass"}
        if check == "joins" and join_warnings:
            item.update(status="warn", detail="; ".join(w.split(": ", 1)[1] for w in join_warnings))
        if check == "database" and dialect != "sqlite":
            item.update(status="skip", detail="Checked by SQLite only when the dialect is SQLite")
        out.append(item)
    return out


def _split_and(condition: exp.Expression) -> list[exp.Expression]:
    return list(condition.flatten()) if isinstance(condition, exp.And) else [condition]


def inspect(sql: str, schema: SchemaInfo, dialect: str = "sqlite") -> dict | None:
    """Tables, columns, joins, filters, aggregations, grouping, ordering and limit of `sql`."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        return None
    if isinstance(tree, exp.Subquery):
        tree = tree.unnest()

    def text(node: exp.Expression) -> str:
        return node.sql(dialect=dialect)

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    joins, filters, grouping, ordering = [], [], [], []
    if select is not None:
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            if on is not None:
                joins.extend(text(c) for c in _split_and(on))
        for clause in ("where", "having"):
            node = select.args.get(clause)
            if node is not None:
                filters.extend(text(c) for c in _split_and(node.this))
        group = select.args.get("group")
        if group is not None:
            grouping = [text(g) for g in group.expressions]
    order = tree.args.get("order")
    if order is not None:
        ordering = [
            f"{text(o.this)} {'DESC' if o.args.get('desc') else 'ASC'}" for o in order.expressions
        ]
    limit = tree.args.get("limit")
    limit_value = None
    if limit is not None and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        limit_value = int(limit.expression.this)

    aggregations = []
    for agg in tree.find_all(exp.AggFunc):
        s = text(agg)
        if s not in aggregations:
            aggregations.append(s)

    return {
        "tables": sorted(tables_in(sql, schema, dialect)),
        "columns": _columns(tree, schema, dialect),
        "joins": joins,
        "filters": filters,
        "aggregations": aggregations,
        "grouping": grouping,
        "ordering": ordering,
        "limit": limit_value,
        "safety": {"read_only": True, "single_statement": True},
    }


def _columns(tree: exp.Expression, schema: SchemaInfo, dialect: str) -> list[str]:
    """Real `Table.Column` names the query reads, resolved through aliases."""
    try:
        qualified = qualify(
            _lowercase_identifiers(tree.copy()),
            schema=schema.schema_dict,
            dialect=dialect,
            quote_identifiers=False,
        )
    except SqlglotError:
        return []
    out: list[str] = []
    for scope in traverse_scope(qualified):
        for col in scope.columns:
            source = scope.sources.get(col.table)
            if not isinstance(source, exp.Table):
                continue  # CTE or subquery column; its base columns appear in that scope
            table = schema.resolve_table(source.name)
            if not table:
                continue
            name = next(
                (c.name for c in schema.columns_of(table) if c.name.lower() == col.name.lower()),
                col.name,
            )
            full = f"{table}.{name}"
            if full not in out:
                out.append(full)
    return out
