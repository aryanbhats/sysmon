"""Tests for the database layer."""

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sysmon.db import Database


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_sysmon.db"
    database = Database(db_path=db_path)
    database.initialize()
    yield database
    database.close()


def _make_snapshot(ts: str | None = None, cpu: float = 10.0, mem_pct: float = 50.0,
                   pressure: str = "normal", swap_used: int = 1_000_000_000,
                   swap_total: int = 5_000_000_000) -> dict:
    """Create a test snapshot dict."""
    return {
        "ts": ts or datetime.now().isoformat(),
        "cpu_percent": cpu,
        "cpu_per_core": [cpu / 2, cpu / 2],
        "mem_total": 16_000_000_000,
        "mem_used": int(16_000_000_000 * mem_pct / 100),
        "mem_available": int(16_000_000_000 * (100 - mem_pct) / 100),
        "mem_percent": mem_pct,
        "mem_pressure": pressure,
        "swap_used": swap_used,
        "swap_total": swap_total,
        "load_1": 2.0,
        "load_5": 3.0,
        "load_15": 4.0,
        "disk_used": 100_000_000_000,
        "disk_free": 800_000_000_000,
    }


def _make_process(name: str = "claude", category: str = "ai_agent",
                  rss: int = 400_000_000, pid: int = 1234) -> dict:
    """Create a test process dict."""
    return {
        "pid": pid,
        "name": name,
        "create_time": 1700000000.0,
        "cpu_percent": 5.0,
        "memory_footprint": rss,
        "rss": rss,
        "category": category,
        "cmdline_hash": "abc123",
    }


class TestDatabaseInit:
    def test_wal_mode(self, db):
        result = db.conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0] == "wal"

    def test_schema_version(self, db):
        result = db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert result[0] == 1

    def test_indexes_exist(self, db):
        indexes = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_snapshots_ts" in index_names
        assert "idx_proc_snapshot_id" in index_names
        assert "idx_proc_category" in index_names
        assert "idx_hourly_hour" in index_names

    def test_foreign_keys_enabled(self, db):
        result = db.conn.execute("PRAGMA foreign_keys").fetchone()
        assert result[0] == 1


class TestInsertSnapshot:
    def test_insert_returns_id(self, db):
        snap = _make_snapshot()
        procs = [_make_process()]
        sid = db.insert_snapshot(snap, procs)
        assert sid is not None
        assert sid > 0

    def test_insert_with_no_processes(self, db):
        snap = _make_snapshot()
        sid = db.insert_snapshot(snap, [])
        assert sid > 0

    def test_data_persisted(self, db):
        snap = _make_snapshot(cpu=42.5)
        procs = [_make_process(name="claude", rss=500_000_000)]
        db.insert_snapshot(snap, procs)

        row = db.conn.execute("SELECT cpu_percent FROM snapshots").fetchone()
        assert row[0] == 42.5

        proc_row = db.conn.execute("SELECT name, rss FROM process_snapshots").fetchone()
        assert proc_row[0] == "claude"
        assert proc_row[1] == 500_000_000


class TestGetLatestSnapshot:
    def test_returns_none_when_empty(self, db):
        assert db.get_latest_snapshot() is None

    def test_returns_latest(self, db):
        db.insert_snapshot(_make_snapshot(ts="2026-03-25T10:00:00"), [])
        db.insert_snapshot(_make_snapshot(ts="2026-03-26T10:00:00", cpu=99.0), [])

        latest = db.get_latest_snapshot()
        assert latest is not None
        assert latest["cpu_percent"] == 99.0

    def test_includes_processes(self, db):
        snap = _make_snapshot()
        procs = [_make_process(name="claude"), _make_process(name="codex", pid=5678)]
        db.insert_snapshot(snap, procs)

        latest = db.get_latest_snapshot()
        assert len(latest["processes"]) == 2


class TestCategoryTotals:
    def test_aggregates_by_category(self, db):
        snap = _make_snapshot()
        procs = [
            _make_process(name="claude", category="ai_agent", rss=400_000_000, pid=1),
            _make_process(name="codex", category="ai_agent", rss=100_000_000, pid=2),
            _make_process(name="Chrome", category="browser", rss=600_000_000, pid=3),
        ]
        sid = db.insert_snapshot(snap, procs)

        totals = db.get_category_totals_for_snapshot(sid)
        assert "ai_agent" in totals
        assert totals["ai_agent"]["total_rss"] == 500_000_000
        assert totals["ai_agent"]["count"] == 2
        assert totals["browser"]["total_rss"] == 600_000_000


class TestPrune:
    def test_prune_removes_old_snapshots(self, db):
        old_ts = (datetime.now() - timedelta(days=10)).isoformat()
        recent_ts = datetime.now().isoformat()

        db.insert_snapshot(_make_snapshot(ts=old_ts), [_make_process()])
        db.insert_snapshot(_make_snapshot(ts=recent_ts), [_make_process(pid=5678)])

        db.prune(raw_days=7, rollup_after_hours=48)

        rows = db.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        assert rows[0] == 1  # only recent survives

    def test_prune_preserves_recent(self, db):
        recent_ts = datetime.now().isoformat()
        db.insert_snapshot(_make_snapshot(ts=recent_ts), [_make_process()])

        db.prune(raw_days=7, rollup_after_hours=48)

        rows = db.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        assert rows[0] == 1


class TestRollup:
    def test_rollup_creates_hourly_summary(self, db):
        # Create snapshots 3 days ago
        old_time = datetime.now() - timedelta(days=3)
        for i in range(5):
            ts = (old_time + timedelta(minutes=i * 10)).isoformat()
            db.insert_snapshot(
                _make_snapshot(ts=ts, cpu=20.0 + i * 5, mem_pct=60.0 + i * 2, pressure="warn"),
                [_make_process(rss=100_000_000 * (i + 1))],
            )

        count = db.rollup_to_hourly(older_than_hours=48)
        assert count >= 1

        summaries = db.get_hourly_summaries(days=7)
        assert len(summaries) >= 1
        assert summaries[0]["sample_count"] >= 1
        assert summaries[0]["cpu_max"] > 0


class TestConcurrentAccess:
    def test_wal_allows_concurrent_read_during_write(self, tmp_path):
        """WAL mode should allow reads while a write transaction is open."""
        db_path = tmp_path / "concurrent.db"
        writer = Database(db_path=db_path)
        writer.initialize()

        # Start a write
        writer.conn.execute("BEGIN")
        writer.conn.execute(
            "INSERT INTO snapshots (ts, cpu_percent) VALUES (?, ?)",
            (datetime.now().isoformat(), 50.0),
        )

        # Reader should still work (WAL mode)
        reader = Database(db_path=db_path)
        # This should not raise "database is locked"
        result = reader.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        assert result[0] == 0  # write not committed yet, reader sees old state

        writer.conn.commit()
        reader.close()
        writer.close()
