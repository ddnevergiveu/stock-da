"""Transactional SQLite import with idempotent overlap handling."""

import sqlite3
from pathlib import Path

import pandas as pd

from .data import COLUMNS, normalize_bars

SCHEMA = """(
    DateTime TEXT PRIMARY KEY NOT NULL,
    Open REAL NOT NULL CHECK(Open > 0),
    High REAL NOT NULL CHECK(High > 0),
    Low REAL NOT NULL CHECK(Low > 0),
    Close REAL NOT NULL CHECK(Close > 0),
    Volume INTEGER NOT NULL CHECK(Volume >= 0),
    TickVolume INTEGER NOT NULL CHECK(TickVolume >= 0),
    CHECK(Low <= Open AND Low <= Close AND High >= Open AND High >= Close AND High >= Low)
)"""


def import_bars(frame: pd.DataFrame, database: str | Path, *, replace: bool = False) -> dict:
    """Append new timestamps; identical overlaps are no-ops, conflicts roll back.

    replace=True explicitly replaces only market_data, transactionally. Existing
    databases without the expected DateTime primary key are rejected otherwise.
    """
    bars = normalize_bars(frame)
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("PRAGMA table_info(market_data)").fetchall()
        if existing and not replace:
            if [column[1] for column in existing] != COLUMNS or existing[0][5] != 1:
                raise ValueError("Existing market_data schema is incompatible; use a new database or explicitly --replace")
        previous = connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0] if existing else 0
        if replace:
            connection.execute("DROP TABLE IF EXISTS market_data")
        connection.execute("CREATE TABLE IF NOT EXISTS market_data " + SCHEMA)
        connection.execute("CREATE TEMP TABLE incoming " + SCHEMA)
        connection.executemany("INSERT INTO incoming VALUES (?, ?, ?, ?, ?, ?, ?)", bars.itertuples(index=False, name=None))
        mismatch = " OR ".join(f"m.{column} != i.{column}" for column in COLUMNS[1:])
        conflict = connection.execute(
            "SELECT i.DateTime FROM incoming i JOIN market_data m ON i.DateTime=m.DateTime "
            f"WHERE {mismatch} LIMIT 1"
        ).fetchone()
        if conflict:
            raise ValueError(f"Conflicting existing bar at {conflict[0]}; no rows were imported")
        connection.execute(
            "INSERT INTO market_data SELECT i.* FROM incoming i "
            "WHERE NOT EXISTS (SELECT 1 FROM market_data m WHERE m.DateTime=i.DateTime)"
        )
        inserted = connection.execute("SELECT changes()").fetchone()[0]
        total = connection.execute("SELECT COUNT(*) FROM market_data").fetchone()[0]
        connection.execute("COMMIT")
        return {"inserted": inserted, "skipped_identical": len(bars) - inserted, "total_rows": total, "replaced_rows": previous if replace else 0}
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
