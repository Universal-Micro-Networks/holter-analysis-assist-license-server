import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
KEY = "lk_" + "a" * 32
NOW = "2026-09-28T00:00:00.000Z"


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    # D1 enforces foreign keys by default; plain SQLite needs it switched on.
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        connection.executescript(migration.read_text(encoding="utf-8"))
    yield connection
    connection.close()


def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def insert_license(db: sqlite3.Connection, key: str = KEY, monthly_limit: int = 10, status: str = "active") -> None:
    db.execute(
        "INSERT INTO licenses (license_key, monthly_limit, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (key, monthly_limit, status, NOW, NOW),
    )


def test_migration_files_use_wrangler_naming() -> None:
    names = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    assert names[0] == "0001_create_licenses_and_usage_logs.sql"


def test_licenses_table_has_designed_columns(db: sqlite3.Connection) -> None:
    assert columns(db, "licenses") == ["license_key", "monthly_limit", "status", "created_at", "updated_at"]


def test_usage_logs_table_has_only_key_and_timestamp(db: sqlite3.Connection) -> None:
    assert columns(db, "usage_logs") == ["id", "license_key", "used_at"]


def test_status_defaults_to_active(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO licenses (license_key, monthly_limit, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (KEY, 10, NOW, NOW),
    )
    assert db.execute("SELECT status FROM licenses").fetchone() == ("active",)


def test_monthly_limit_zero_is_allowed_and_negative_rejected(db: sqlite3.Connection) -> None:
    insert_license(db, monthly_limit=0)
    with pytest.raises(sqlite3.IntegrityError):
        insert_license(db, key="lk_" + "b" * 32, monthly_limit=-1)


def test_status_only_allows_active_or_suspended(db: sqlite3.Connection) -> None:
    insert_license(db, status="suspended")
    with pytest.raises(sqlite3.IntegrityError):
        insert_license(db, key="lk_" + "b" * 32, status="deleted")


def test_duplicate_license_key_is_rejected(db: sqlite3.Connection) -> None:
    insert_license(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_license(db)


def test_usage_log_ids_are_assigned_automatically(db: sqlite3.Connection) -> None:
    insert_license(db)
    db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, NOW))
    db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, NOW))
    assert [row[0] for row in db.execute("SELECT id FROM usage_logs ORDER BY id")] == [1, 2]


def test_usage_log_requires_existing_license(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, NOW))


def test_license_with_usage_logs_cannot_be_deleted(db: sqlite3.Connection) -> None:
    insert_license(db)
    db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, NOW))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM licenses WHERE license_key = ?", (KEY,))


@pytest.mark.parametrize(
    "used_at",
    [
        "2026-09-28 00:00:00",
        "2026-09-28T00:00:00Z",
        "2026-09-28T09:00:00.000+09:00",
        "2026-13-01T00:00:00.000Z",
        "",
    ],
)
def test_used_at_must_be_fixed_width_utc_text(db: sqlite3.Connection, used_at: str) -> None:
    insert_license(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, used_at))


def test_used_at_accepts_fixed_width_utc_text(db: sqlite3.Connection) -> None:
    insert_license(db)
    db.execute("INSERT INTO usage_logs (license_key, used_at) VALUES (?, ?)", (KEY, "2026-12-31T23:59:59.999Z"))


def test_schema_avoids_like_and_glob_patterns() -> None:
    # D1 rejects LIKE/GLOB patterns longer than 50 bytes at insert time ("pattern too complex"),
    # which plain SQLite does not, so such CHECKs would pass here and break every insert on D1.
    for migration in MIGRATIONS_DIR.glob("*.sql"):
        sql = migration.read_text(encoding="utf-8").upper()
        assert " GLOB " not in sql and " LIKE " not in sql, migration.name


def test_monthly_count_query_uses_composite_index(db: sqlite3.Connection) -> None:
    plan = " ".join(
        row[3]
        for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM usage_logs WHERE license_key = ? AND used_at >= ? AND used_at < ?",
            (KEY, NOW, NOW),
        )
    )
    assert "idx_usage_logs_license_used_at" in plan
