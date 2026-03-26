"""System data collector using psutil + macOS-specific APIs."""

import ctypes
import ctypes.util
import hashlib
import json
import subprocess
import re
from datetime import datetime

import psutil

from .categories import categorize, display_name
from .db import Database

# Minimum RSS (10 MB) for 'other' category processes to be tracked
OTHER_MIN_RSS = 10_000_000


def collect_snapshot(db: Database) -> int:
    """Collect a single system snapshot and store it. Returns snapshot ID."""
    # CPU: non-blocking. First call after process start returns 0.0 — acceptable
    # since the launchd agent runs continuously.
    cpu_pct = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)

    # Memory
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    pressure = get_memory_pressure()

    # Disk
    disk = psutil.disk_usage("/")

    # Load averages
    load_1, load_5, load_15 = psutil.getloadavg()

    snapshot = {
        "ts": datetime.now().isoformat(),
        "cpu_percent": cpu_pct,
        "cpu_per_core": cpu_per_core,
        "mem_total": mem.total,
        "mem_used": mem.used,
        "mem_available": mem.available,
        "mem_percent": mem.percent,
        "mem_pressure": pressure,
        "swap_used": swap.used,
        "swap_total": swap.total,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "disk_used": disk.used,
        "disk_free": disk.free,
    }

    # Process scan — all processes, filter 'other' by RSS threshold
    processes = _collect_processes()

    return db.insert_snapshot(snapshot, processes)


def collect_live_snapshot() -> dict:
    """Collect a snapshot for live display (not stored to DB).

    Includes process context (workspace/repo) for AI agents.
    """
    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    pressure = get_memory_pressure()
    disk = psutil.disk_usage("/")
    load_1, load_5, load_15 = psutil.getloadavg()

    processes = _collect_processes()

    # Enrich with workspace context for live display
    for proc in processes:
        proc["context"] = _get_process_context(proc["pid"], proc["category"])

    # Uptime
    boot_time = psutil.boot_time()
    uptime_secs = (datetime.now() - datetime.fromtimestamp(boot_time)).total_seconds()

    return {
        "ts": datetime.now().isoformat(),
        "cpu_percent": cpu_pct,
        "cpu_per_core": cpu_per_core,
        "mem_total": mem.total,
        "mem_used": mem.used,
        "mem_available": mem.available,
        "mem_percent": mem.percent,
        "mem_pressure": pressure,
        "swap_used": swap.used,
        "swap_total": swap.total,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "disk_used": disk.used,
        "disk_free": disk.free,
        "uptime_seconds": int(uptime_secs),
        "processes": processes,
    }


def _collect_processes() -> list[dict]:
    """Scan all processes, categorize, and filter."""
    processes = []
    for proc in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_info", "cmdline", "create_time"]
    ):
        try:
            info = proc.info
            if not info.get("name"):
                continue

            mem_info = info.get("memory_info")
            if mem_info is None:
                continue

            category = categorize(info["name"], info.get("cmdline"))

            # Skip small 'other' processes to reduce noise
            if category == "other" and mem_info.rss < OTHER_MIN_RSS:
                continue

            footprint = _get_footprint(info["pid"])

            processes.append(
                {
                    "pid": info["pid"],
                    "name": display_name(info["name"], info.get("cmdline")),
                    "create_time": info.get("create_time"),
                    "cpu_percent": info.get("cpu_percent") or 0.0,
                    "rss": mem_info.rss,
                    "memory_footprint": footprint,
                    "category": category,
                    "cmdline_hash": _hash_cmdline(info.get("cmdline")),
                }
            )
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

    return processes


def get_memory_pressure() -> str:
    """Get macOS memory pressure level by parsing the `memory_pressure` command.

    Returns 'normal', 'warn', or 'critical'.
    """
    try:
        result = subprocess.run(
            ["memory_pressure"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout
        # Look for: "System-wide memory free percentage: 42%"
        match = re.search(r"free percentage:\s*(\d+)%", output)
        if match:
            pct = int(match.group(1))
            if pct >= 50:
                return "normal"
            elif pct >= 25:
                return "warn"
            else:
                return "critical"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Fallback: estimate from psutil
    mem = psutil.virtual_memory()
    if mem.percent < 70:
        return "normal"
    elif mem.percent < 90:
        return "warn"
    return "critical"


# ── macOS memory footprint via proc_pid_rusage ──────────────────────────────

# proc_pid_rusage constants
RUSAGE_INFO_V2 = 2

# rusage_info_v2 struct size (approximate — we only need ri_phys_footprint)
# ri_phys_footprint is at offset 80 in rusage_info_v2
_RUSAGE_STRUCT_SIZE = 240  # safe overestimate


def _get_footprint(pid: int) -> int:
    """Get macOS physical memory footprint for a process via proc_pid_rusage.

    This matches Activity Monitor's 'Memory' column more accurately than RSS.
    Falls back to 0 if the ctypes call fails.
    """
    try:
        libproc_path = ctypes.util.find_library("libproc")
        if not libproc_path:
            return 0

        libproc = ctypes.CDLL(libproc_path)

        buf = ctypes.create_string_buffer(_RUSAGE_STRUCT_SIZE)
        ret = libproc.proc_pid_rusage(pid, RUSAGE_INFO_V2, ctypes.byref(buf))
        if ret != 0:
            return 0

        # ri_phys_footprint is a uint64 at offset 80 in rusage_info_v2
        footprint = int.from_bytes(buf[80:88], byteorder="little")
        return footprint
    except (OSError, ValueError):
        return 0


def _get_process_context(pid: int, category: str) -> str | None:
    """Get human-readable workspace/repo context for a process via cwd.

    Only meaningful for AI agents and conductor processes.
    Returns None for other categories or on failure.
    """
    if category not in ("ai_agent", "conductor"):
        return None

    try:
        cwd = psutil.Process(pid).cwd()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
        return None

    if not cwd or cwd == "/":
        return "background"

    parts = cwd.rstrip("/").split("/")

    # Conductor workspaces: .../conductor/workspaces/<project>/<workspace>
    if "workspaces" in parts:
        ws_idx = parts.index("workspaces")
        remainder = parts[ws_idx + 1 :]
        if remainder:
            return "/".join(remainder)

    # General case: last meaningful path component
    # Filter out generic dirs like "Documents", "home", username
    generic = {"Documents", "home", "Users", ""}
    meaningful = [p for p in parts if p not in generic]
    if meaningful:
        return meaningful[-1]

    return parts[-1] if parts else None


def _hash_cmdline(cmdline: list[str] | None) -> str | None:
    """Hash the command line for privacy — stores identity without secrets."""
    if not cmdline:
        return None
    joined = " ".join(cmdline)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]
