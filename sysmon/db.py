"""SQLite database layer with WAL mode, migrations, and retention."""

import json
import sqlite3
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "sysmon" / "sysmon.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    cpu_percent REAL,
    cpu_per_core TEXT,
    mem_total INTEGER,
    mem_used INTEGER,
    mem_available INTEGER,
    mem_percent REAL,
    mem_pressure TEXT,
    swap_used INTEGER,
    swap_total INTEGER,
    load_1 REAL,
    load_5 REAL,
    load_15 REAL,
    disk_used INTEGER,
    disk_free INTEGER
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS process_snapshots (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    pid INTEGER NOT NULL,
    name TEXT NOT NULL,
    create_time REAL,
    cpu_percent REAL,
    memory_footprint INTEGER,
    rss INTEGER,
    category TEXT NOT NULL,
    cmdline_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_proc_snapshot_id ON process_snapshots(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_proc_category ON process_snapshots(category);

CREATE TABLE IF NOT EXISTS hourly_summaries (
    id INTEGER PRIMARY KEY,
    hour TEXT NOT NULL UNIQUE,
    cpu_avg REAL,
    cpu_max REAL,
    cpu_p95 REAL,
    mem_avg REAL,
    mem_max REAL,
    swap_avg REAL,
    swap_max REAL,
    pressure_critical_pct REAL,
    ai_agent_rss_total_avg INTEGER,
    ai_agent_rss_total_max INTEGER,
    browser_rss_total_avg INTEGER,
    sample_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_hourly_hour ON hourly_summaries(hour);
"""


class Database:
    """SQLite database for sysmon snapshots."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=10,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """Create tables and apply migrations."""
        self.conn.executescript(SCHEMA_SQL)
        # Set schema version if not present
        cur = self.conn.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        if row[0] is None:
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()

    def insert_snapshot(
        self,
        snapshot: dict[str, Any],
        processes: list[dict[str, Any]],
    ) -> int:
        """Insert a system snapshot and associated process data. Returns snapshot ID."""
        cur = self.conn.execute(
            """INSERT INTO snapshots
               (ts, cpu_percent, cpu_per_core, mem_total, mem_used, mem_available,
                mem_percent, mem_pressure, swap_used, swap_total,
                load_1, load_5, load_15, disk_used, disk_free)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot["ts"],
                snapshot["cpu_percent"],
                json.dumps(snapshot.get("cpu_per_core", [])),
                snapshot["mem_total"],
                snapshot["mem_used"],
                snapshot["mem_available"],
                snapshot["mem_percent"],
                snapshot["mem_pressure"],
                snapshot["swap_used"],
                snapshot["swap_total"],
                snapshot["load_1"],
                snapshot["load_5"],
                snapshot["load_15"],
                snapshot["disk_used"],
                snapshot["disk_free"],
            ),
        )
        snapshot_id = cur.lastrowid

        if processes:
            self.conn.executemany(
                """INSERT INTO process_snapshots
                   (snapshot_id, pid, name, create_time, cpu_percent,
                    memory_footprint, rss, category, cmdline_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        snapshot_id,
                        p["pid"],
                        p["name"],
                        p.get("create_time"),
                        p.get("cpu_percent", 0.0),
                        p.get("memory_footprint", 0),
                        p.get("rss", 0),
                        p["category"],
                        p.get("cmdline_hash"),
                    )
                    for p in processes
                ],
            )

        self.conn.commit()
        return snapshot_id

    def get_latest_snapshot(self) -> dict[str, Any] | None:
        """Get the most recent snapshot with its processes."""
        row = self.conn.execute(
            "SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None

        snapshot = dict(row)
        snapshot["cpu_per_core"] = json.loads(snapshot.get("cpu_per_core") or "[]")

        procs = self.conn.execute(
            "SELECT * FROM process_snapshots WHERE snapshot_id = ? ORDER BY rss DESC",
            (snapshot["id"],),
        ).fetchall()
        snapshot["processes"] = [dict(p) for p in procs]

        return snapshot

    def get_snapshots_since(self, hours: float) -> list[dict[str, Any]]:
        """Get all snapshots from the last N hours."""
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_category_totals_for_snapshot(
        self, snapshot_id: int
    ) -> dict[str, dict[str, Any]]:
        """Get aggregated memory/CPU totals per category for a snapshot."""
        rows = self.conn.execute(
            """SELECT category,
                      COUNT(*) as count,
                      SUM(rss) as total_rss,
                      SUM(memory_footprint) as total_footprint,
                      SUM(cpu_percent) as total_cpu
               FROM process_snapshots
               WHERE snapshot_id = ?
               GROUP BY category
               ORDER BY total_rss DESC""",
            (snapshot_id,),
        ).fetchall()
        return {r["category"]: dict(r) for r in rows}

    def get_processes_by_category(
        self, snapshot_id: int, category: str
    ) -> list[dict[str, Any]]:
        """Get individual processes for a given category in a snapshot."""
        rows = self.conn.execute(
            """SELECT * FROM process_snapshots
               WHERE snapshot_id = ? AND category = ?
               ORDER BY rss DESC""",
            (snapshot_id, category),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_hourly_summaries(self, days: int = 7) -> list[dict[str, Any]]:
        """Get hourly summaries for the last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM hourly_summaries WHERE hour >= ? ORDER BY hour",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def rollup_to_hourly(self, older_than_hours: int = 48) -> int:
        """Roll up raw snapshots older than N hours into hourly summaries.
        Returns count of hours rolled up."""
        cutoff = (datetime.now() - timedelta(hours=older_than_hours)).isoformat()

        # Get distinct hours that haven't been rolled up yet
        rows = self.conn.execute(
            """SELECT DISTINCT substr(ts, 1, 13) as hour
               FROM snapshots
               WHERE ts < ?
               AND substr(ts, 1, 13) NOT IN (SELECT hour FROM hourly_summaries)
               ORDER BY hour""",
            (cutoff,),
        ).fetchall()

        count = 0
        for row in rows:
            hour = row["hour"]
            hour_start = hour + ":00:00"
            hour_end = hour + ":59:59"

            snaps = self.conn.execute(
                """SELECT cpu_percent, mem_percent, swap_used, swap_total, mem_pressure
                   FROM snapshots WHERE ts >= ? AND ts <= ?""",
                (hour_start, hour_end),
            ).fetchall()

            if not snaps:
                continue

            cpu_vals = [s["cpu_percent"] for s in snaps if s["cpu_percent"] is not None]
            mem_vals = [s["mem_percent"] for s in snaps if s["mem_percent"] is not None]
            swap_pcts = []
            for s in snaps:
                if s["swap_total"] and s["swap_total"] > 0:
                    swap_pcts.append(s["swap_used"] / s["swap_total"] * 100)

            pressure_critical = sum(
                1 for s in snaps if s["mem_pressure"] == "critical"
            )

            # Get AI agent and browser RSS totals per snapshot in this hour
            snap_ids = self.conn.execute(
                "SELECT id FROM snapshots WHERE ts >= ? AND ts <= ?",
                (hour_start, hour_end),
            ).fetchall()

            ai_totals = []
            browser_totals = []
            for sid in snap_ids:
                ai_row = self.conn.execute(
                    """SELECT COALESCE(SUM(rss), 0) as total
                       FROM process_snapshots
                       WHERE snapshot_id = ? AND category = 'ai_agent'""",
                    (sid["id"],),
                ).fetchone()
                ai_totals.append(ai_row["total"])

                br_row = self.conn.execute(
                    """SELECT COALESCE(SUM(rss), 0) as total
                       FROM process_snapshots
                       WHERE snapshot_id = ? AND category = 'browser'""",
                    (sid["id"],),
                ).fetchone()
                browser_totals.append(br_row["total"])

            def p95(vals: list[float]) -> float:
                if not vals:
                    return 0.0
                sorted_vals = sorted(vals)
                idx = int(len(sorted_vals) * 0.95)
                return sorted_vals[min(idx, len(sorted_vals) - 1)]

            self.conn.execute(
                """INSERT OR REPLACE INTO hourly_summaries
                   (hour, cpu_avg, cpu_max, cpu_p95,
                    mem_avg, mem_max, swap_avg, swap_max,
                    pressure_critical_pct,
                    ai_agent_rss_total_avg, ai_agent_rss_total_max,
                    browser_rss_total_avg, sample_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    hour,
                    statistics.mean(cpu_vals) if cpu_vals else 0,
                    max(cpu_vals) if cpu_vals else 0,
                    p95(cpu_vals),
                    statistics.mean(mem_vals) if mem_vals else 0,
                    max(mem_vals) if mem_vals else 0,
                    statistics.mean(swap_pcts) if swap_pcts else 0,
                    max(swap_pcts) if swap_pcts else 0,
                    (pressure_critical / len(snaps) * 100) if snaps else 0,
                    int(statistics.mean(ai_totals)) if ai_totals else 0,
                    max(ai_totals) if ai_totals else 0,
                    int(statistics.mean(browser_totals)) if browser_totals else 0,
                    len(snaps),
                ),
            )
            count += 1

        self.conn.commit()
        return count

    def prune(self, raw_days: int = 7, rollup_after_hours: int = 48) -> None:
        """Roll up old data to hourly summaries, then delete old raw data."""
        self.rollup_to_hourly(older_than_hours=rollup_after_hours)

        # Delete raw snapshots older than raw_days
        cutoff = (datetime.now() - timedelta(days=raw_days)).isoformat()
        self.conn.execute(
            "DELETE FROM process_snapshots WHERE snapshot_id IN "
            "(SELECT id FROM snapshots WHERE ts < ?)",
            (cutoff,),
        )
        self.conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))

        # Delete hourly summaries older than 90 days
        old_cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        self.conn.execute("DELETE FROM hourly_summaries WHERE hour < ?", (old_cutoff,))

        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
