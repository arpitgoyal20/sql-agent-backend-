"""Introspect the sample database into a schema description (§5).

Built once from the database itself so the prompt text and the validator always agree.
Lookups are case-insensitive; prompt text keeps the original casing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import yaml

from app import db
from app.config import ROOT

DOCS_PATH = ROOT / "data" / "schema_docs.yaml"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    not_null: bool
    pk: bool
    doc: str = ""


class ForeignKey(NamedTuple):
    """(table, column, ref_table, ref_column), as specified in §5."""

    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class SchemaInfo:
    tables: dict[str, list[Column]]  # original table name -> columns, in DB order
    foreign_keys: list[ForeignKey]
    indexes: list[tuple[str, list[str]]]  # (table, [columns])
    index_names: dict[tuple[str, tuple[str, ...]], str] = field(default_factory=dict)

    # ---- lookups -------------------------------------------------------
    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    @property
    def schema_dict(self) -> dict[str, dict[str, str]]:
        """`{table_lower: {column_lower: type}}` as sqlglot's qualify expects."""
        return {
            t.lower(): {c.name.lower(): c.type for c in cols} for t, cols in self.tables.items()
        }

    def resolve_table(self, name: str) -> str | None:
        """Return the original-case table name for `name` (any case), or None."""
        lower = name.lower()
        return next((t for t in self.tables if t.lower() == lower), None)

    def columns_of(self, table: str) -> list[Column]:
        resolved = self.resolve_table(table)
        return self.tables[resolved] if resolved else []

    def is_fk_pair(self, t1: str, c1: str, t2: str, c2: str) -> bool:
        """True if (t1.c1, t2.c2) matches a declared foreign key in either direction."""
        a = (t1.lower(), c1.lower())
        b = (t2.lower(), c2.lower())
        for fk in self.foreign_keys:
            src = (fk.table.lower(), fk.column.lower())
            dst = (fk.ref_table.lower(), fk.ref_column.lower())
            if (a, b) in ((src, dst), (dst, src)):
                return True
        return False

    def neighbours(self, table: str) -> set[str]:
        """Tables linked to `table` by a foreign key, in either direction."""
        lower = table.lower()
        out: set[str] = set()
        for fk in self.foreign_keys:
            if fk.table.lower() == lower:
                out.add(fk.ref_table)
            elif fk.ref_table.lower() == lower:
                out.add(fk.table)
        out.discard(self.resolve_table(table) or table)
        return out

    def indexed_columns(self, table: str) -> set[str]:
        """Lower-case columns that lead an index on `table`, plus the primary key."""
        lower = table.lower()
        cols = {c.name.lower() for c in self.columns_of(table) if c.pk}
        cols |= {ic[0].lower() for t, ic in self.indexes if t.lower() == lower and ic}
        return cols

    # ---- prompt text ---------------------------------------------------
    def schema_text(self, tables: list[str] | None = None) -> str:
        """Compact CREATE TABLE-style text with column docs as comments and FK lines."""
        wanted = [self.resolve_table(t) for t in tables] if tables else self.table_names
        selected = [t for t in self.table_names if t in set(wanted)]  # keep DB order
        blocks = []
        for t in selected:
            cols = self.tables[t]
            lines = []
            for i, c in enumerate(cols):
                parts = [c.name, c.type]
                if c.pk:
                    parts.append("PRIMARY KEY")
                elif c.not_null:
                    parts.append("NOT NULL")
                comma = "," if i < len(cols) - 1 else ""
                comment = f"  -- {c.doc}" if c.doc else ""
                lines.append(f"  {' '.join(parts)}{comma}{comment}")
            block = [f"CREATE TABLE {t} (", *lines, ");"]
            for fk in self.foreign_keys:
                if fk.table == t:
                    block.append(f"-- FK: {t}.{fk.column} -> {fk.ref_table}.{fk.ref_column}")
            for it, icols in self.indexes:
                if it == t:
                    name = self.index_names.get((it, tuple(icols)))
                    label = f"INDEX {name}" if name else "INDEX"
                    block.append(f"-- {label} ON {t}({', '.join(icols)})")
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    def describe_indexes(self) -> str:
        """One line per index, for the rewriter prompt."""
        return "\n".join(f"{t}({', '.join(cols)})" for t, cols in self.indexes) or "none"


def _load_docs(path: Path = DOCS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return {str(k).lower(): str(v) for k, v in raw.items()}


def load_schema(db_path: Path | str | None = None, docs_path: Path = DOCS_PATH) -> SchemaInfo:
    docs = _load_docs(docs_path)
    conn = db.connect(db_path)
    try:
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()
        tables: dict[str, list[Column]] = {}
        fks: list[ForeignKey] = []
        indexes: list[tuple[str, list[str]]] = []
        index_names: dict[tuple[str, tuple[str, ...]], str] = {}

        for (table,) in table_rows:
            q = table.replace('"', '""')
            # table_info: cid, name, type, notnull, dflt_value, pk
            tables[table] = [
                Column(
                    name=name,
                    type=(ctype or "TEXT").upper(),
                    not_null=bool(notnull),
                    pk=bool(pk),
                    doc=docs.get(f"{table}.{name}".lower(), ""),
                )
                for _cid, name, ctype, notnull, _dflt, pk in conn.execute(
                    f'PRAGMA table_info("{q}")'
                )
            ]
            # foreign_key_list: id, seq, table, from, to, on_update, on_delete, match
            for row in conn.execute(f'PRAGMA foreign_key_list("{q}")'):
                fks.append(ForeignKey(table, row[3], row[2], row[4]))
            # index_list: seq, name, unique, origin, partial
            for row in conn.execute(f'PRAGMA index_list("{q}")'):
                iname = row[1]
                iq = iname.replace('"', '""')
                cols = [r[2] for r in conn.execute(f'PRAGMA index_info("{iq}")')]
                indexes.append((table, cols))
                index_names[(table, tuple(cols))] = iname
    finally:
        conn.close()

    # FK target tables may be written in a different case in DDL; normalise to real names.
    resolved = []
    for fk in fks:
        ref = next((t for t in tables if t.lower() == fk.ref_table.lower()), fk.ref_table)
        resolved.append(ForeignKey(fk.table, fk.column, ref, fk.ref_column))
    return SchemaInfo(tables=tables, foreign_keys=resolved, indexes=indexes, index_names=index_names)


@lru_cache
def get_schema() -> SchemaInfo:
    """Process-wide schema, loaded once from the configured sample database."""
    return load_schema()
