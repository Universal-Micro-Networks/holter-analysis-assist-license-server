"""Stand-in for the D1 binding as seen from Python Workers (results arrive as dicts/lists, not JsProxy).

Backed by in-memory SQLite with the production migrations. Methods return plain values instead of
promises, so tests pass an identity function as `run_sync`.
"""

import sqlite3
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class FakeD1Error(Exception):
    pass


class FakeStatement:
    def __init__(self, db: "FakeD1Binding", query: str, params: tuple[Any, ...] = ()) -> None:
        self._db = db
        self.query = query
        self.params = params

    def bind(self, *params: Any) -> "FakeStatement":
        return FakeStatement(self._db, self.query, params)

    def first(self) -> dict[str, Any] | None:
        rows = self._db.execute(self)["results"]
        return rows[0] if rows else None

    def all(self) -> dict[str, Any]:
        return self._db.execute(self)

    def run(self) -> dict[str, Any]:
        return self._db.execute(self)


class FakeD1Binding:
    def __init__(self) -> None:
        self.fail_with: str | None = None
        self.calls: list[str] = []
        self._conn = sqlite3.connect(":memory:", isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            self._conn.executescript(migration.read_text(encoding="utf-8"))

    def prepare(self, query: str) -> FakeStatement:
        return FakeStatement(self, query)

    def batch(self, statements: list[FakeStatement]) -> list[dict[str, Any]]:
        self.calls.append("batch")
        self._conn.execute("BEGIN")
        try:
            results = [self._execute(statement) for statement in statements]
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return results

    def execute(self, statement: FakeStatement) -> dict[str, Any]:
        self.calls.append("single")
        return self._execute(statement)

    def _execute(self, statement: FakeStatement) -> dict[str, Any]:
        if self.fail_with is not None:
            raise FakeD1Error(self.fail_with)
        try:
            cursor = self._conn.execute(statement.query, statement.params)
            rows = [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as error:
            raise FakeD1Error(f"D1_ERROR: {error}: SQLITE_CONSTRAINT") from error
        return {"success": True, "results": rows, "meta": {"changes": max(cursor.rowcount, 0)}}
