"""retrieve_schema (code): choose relevant tables and write `schema_context`.

Keyword/fuzzy match of the user text and any SQL against table names, column names and a few
domain words, plus FK neighbours. Falls back to all tables when nothing matches.
"""

from __future__ import annotations

import difflib
import re

from app.nodes.common import schema
from app.state import AgentState

# Domain words that point at a table without naming it.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "Employees": ("staff", "employee", "hire", "hired", "salary", "salaries", "manager",
                  "managers", "rep", "reps", "worker", "workers", "people"),
    "Departments": ("department", "dept", "team", "teams", "location", "office"),
    "Customers": ("customer", "client", "clients", "buyer", "buyers", "state", "states",
                  "city", "cities", "signup", "signed", "california", "texas", "york"),
    "Products": ("product", "item", "items", "category", "categories", "price", "catalog"),
    "Orders": ("order", "orders", "status", "pending", "shipped", "delivered", "cancelled",
               "canceled", "purchase", "purchases", "sales"),
    "OrderItems": ("revenue", "quantity", "line", "lines", "sold", "spent", "spend",
                   "value", "units", "bestselling", "best-selling"),
    "Payments": ("payment", "payments", "paid", "pay", "method", "upi", "card", "cash",
                 "amount"),
}


def _words(text: str) -> set[str]:
    words = set()
    for raw in re.findall(r"[A-Za-z]+", text or ""):
        # split CamelCase identifiers from SQL too: OrderDate -> order, date
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", raw) or [raw]:
            w = part.lower()
            words.add(w)
            if w.endswith("s") and len(w) > 3:
                words.add(w[:-1])
    return words


def select_tables(text: str) -> list[str]:
    s = schema()
    words = _words(text)
    lowered = (text or "").lower()
    hits: set[str] = set()
    for table in s.table_names:
        t = table.lower()
        singular = t[:-1] if t.endswith("s") else t
        if t in lowered or singular in words or difflib.get_close_matches(singular, words, 1, 0.85):
            hits.add(table)
        if any(k in words for k in KEYWORDS.get(table, ())):
            hits.add(table)
        for col in s.columns_of(table):
            c = col.name.lower()
            if c.endswith("id"):
                continue  # IDs appear everywhere; they say nothing about relevance
            if c in lowered.replace(" ", ""):
                hits.add(table)
    if not hits:
        return list(s.table_names)
    for table in list(hits):
        hits |= s.neighbours(table)
    return [t for t in s.table_names if t in hits]


def retrieve_schema(state: AgentState) -> dict:
    sources = [state.get("user_input", ""), state.get("user_sql") or ""]
    if state.get("intent") == "modify" or state.get("refers_to_previous"):
        sources.append(state.get("last_sql") or "")
    tables = select_tables("\n".join(sources))
    return {"schema_context": schema().schema_text(tables)}
