"""Work around a sqlglot limitation in the SQLite generator.

sqlglot's SQLite generator drops column lists from *every* table alias, including CTE
aliases, so `WITH RECURSIVE n(i) AS (...)` is written back as `WITH RECURSIVE n AS (...)`,
which SQLite then rejects. SQLite does support CTE column lists, so keep them there;
other table aliases keep sqlglot's default behaviour.
"""

from sqlglot import exp
from sqlglot.dialects.sqlite import SQLite

_original_tablealias_sql = SQLite.Generator.tablealias_sql


def _tablealias_sql(self, expression: exp.TableAlias) -> str:
    if isinstance(expression.parent, exp.CTE) and expression.args.get("columns"):
        columns = self.expressions(expression, key="columns", flat=True)
        return f"{self.sql(expression, 'this')}({columns})"
    return _original_tablealias_sql(self, expression)


SQLite.Generator.tablealias_sql = _tablealias_sql
