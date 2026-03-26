"""CLI interface for sysmon."""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
import psutil
from rich.console import Console
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from .categories import CATEGORY_COLORS, CATEGORY_LABELS
from .collector import collect_live_snapshot, collect_snapshot, get_memory_pressure
from .db import DEFAULT_DB_PATH, Database

console = Console()

PLIST_NAME = "com.sysmon.collector"
PLIST_TEMPLATE = Path(__file__).parent.parent / "com.sysmon.collector.plist"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = Path.home() / "Library" / "Logs" / "sysmon"
SYSMON_DATA_DIR = Path.home() / ".local" / "share" / "sysmon"


def _fmt_bytes(b: int | None) -> str:
    """Format bytes to human readable."""
    if b is None or b == 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _pressure_style(pressure: str) -> str:
    """Get rich style for memory pressure level."""
    return {
        "normal": "green",
        "warn": "yellow",
        "critical": "bold red",
    }.get(pressure, "white")


def _pct_bar(pct: float, width: int = 20) -> Text:
    """Create a colored progress bar text."""
    filled = int(pct / 100 * width)
    empty = width - filled

    if pct >= 90:
        color = "red"
    elif pct >= 70:
        color = "yellow"
    else:
        color = "green"

    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * empty, style="dim")
    return bar


@click.group()
def cli():
    """sysmon — Lightweight macOS system resource monitor."""
    pass


@cli.command()
def status():
    """Show current system status with AI-workload grouping."""
    snap = collect_live_snapshot()

    # ── System gauges ──
    mem_pct = snap["mem_percent"]
    swap_pct = (snap["swap_used"] / snap["swap_total"] * 100) if snap["swap_total"] > 0 else 0
    disk_total = snap["disk_used"] + snap["disk_free"]
    disk_pct = (snap["disk_used"] / disk_total * 100) if disk_total > 0 else 0

    gauge_lines = []
    gauge_lines.append(
        f" CPU:  {snap['cpu_percent']:5.1f}% {_pct_bar(snap['cpu_percent'])}  "
        f"Load: {snap['load_1']:.1f} / {snap['load_5']:.1f} / {snap['load_15']:.1f}"
    )
    gauge_lines.append(
        f" RAM:  {mem_pct:5.1f}% {_pct_bar(mem_pct)}  "
        f"{_fmt_bytes(snap['mem_used'])} / {_fmt_bytes(snap['mem_total'])}"
    )
    gauge_lines.append(
        f" Swap: {swap_pct:5.1f}% {_pct_bar(swap_pct)}  "
        f"{_fmt_bytes(snap['swap_used'])} / {_fmt_bytes(snap['swap_total'])}"
    )
    gauge_lines.append(
        f" Disk: {disk_pct:5.1f}% {_pct_bar(disk_pct)}  "
        f"{_fmt_bytes(snap['disk_free'])} free"
    )

    pressure = snap["mem_pressure"]
    pressure_text = Text(f" Pressure: {pressure.upper()}", style=_pressure_style(pressure))
    gauge_lines.append(pressure_text)

    # Build gauge panel content
    gauge_content = Text()
    for i, line in enumerate(gauge_lines):
        if isinstance(line, Text):
            gauge_content.append_text(line)
        else:
            gauge_content.append(line)
        if i < len(gauge_lines) - 1:
            gauge_content.append("\n")

    console.print(Panel(gauge_content, title="System Status", border_style="blue"))

    # ── Category breakdown ──
    categories: dict[str, dict] = {}
    for proc in snap["processes"]:
        cat = proc["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "total_rss": 0, "total_cpu": 0.0, "procs": []}
        categories[cat]["count"] += 1
        categories[cat]["total_rss"] += proc["rss"]
        categories[cat]["total_cpu"] += proc["cpu_percent"]
        categories[cat]["procs"].append(proc)

    cat_table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    cat_table.add_column("Category", min_width=14)
    cat_table.add_column("Processes", justify="right", min_width=5)
    cat_table.add_column("Memory", justify="right", min_width=10)
    cat_table.add_column("CPU %", justify="right", min_width=8)

    # Sort by total_rss descending
    for cat, data in sorted(categories.items(), key=lambda x: x[1]["total_rss"], reverse=True):
        label = CATEGORY_LABELS.get(cat, cat)
        color = CATEGORY_COLORS.get(cat, "white")
        cat_table.add_row(
            Text(label, style=color),
            str(data["count"]),
            _fmt_bytes(data["total_rss"]),
            f"{data['total_cpu']:.1f}%",
        )

    console.print(Panel(cat_table, title="Resource Usage by Category", border_style="cyan"))

    # ── AI Agent detail (with workspace context + grouping) ──
    ai_procs = categories.get("ai_agent", {}).get("procs", [])
    conductor_procs = categories.get("conductor", {}).get("procs", [])
    all_ai = sorted(ai_procs + conductor_procs, key=lambda p: p["rss"], reverse=True)

    if all_ai:
        # Group small processes (>3 with same name, all <5 MB) into summary lines
        SMALL_THRESHOLD = 5_000_000
        GROUP_MIN_COUNT = 3

        name_groups: dict[str, list] = {}
        for proc in all_ai:
            name_groups.setdefault(proc["name"], []).append(proc)

        display_rows = []  # (name, pid_str, rss, cpu, context)
        grouped_names = set()

        for name, procs in name_groups.items():
            if len(procs) >= GROUP_MIN_COUNT and all(p["rss"] < SMALL_THRESHOLD for p in procs):
                total_rss = sum(p["rss"] for p in procs)
                total_cpu = sum(p["cpu_percent"] for p in procs)
                display_rows.append((
                    f"{name} (x{len(procs)})",
                    "-",
                    total_rss,
                    total_cpu,
                    "background",
                ))
                grouped_names.add(name)

        for proc in all_ai:
            if proc["name"] in grouped_names:
                continue
            ctx = proc.get("context") or ""
            display_rows.append((
                proc["name"],
                str(proc["pid"]),
                proc["rss"],
                proc["cpu_percent"],
                ctx,
            ))

        # Sort: individual processes by RSS desc, grouped at bottom
        display_rows.sort(key=lambda r: (r[1] == "-", -r[2]))

        ai_table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        ai_table.add_column("Process", min_width=18)
        ai_table.add_column("PID", justify="right", min_width=6)
        ai_table.add_column("Memory", justify="right", min_width=9)
        ai_table.add_column("CPU %", justify="right", min_width=6)
        ai_table.add_column("Workspace", min_width=20)

        total_ai_rss = sum(r[2] for r in display_rows)
        total_ai_cpu = sum(r[3] for r in display_rows)

        for name, pid_str, rss, cpu, ctx in display_rows:
            ctx_display = ctx if ctx else ""
            ai_table.add_row(
                name,
                pid_str,
                _fmt_bytes(rss),
                f"{cpu:.1f}%",
                Text(ctx_display, style="dim"),
            )

        ai_table.add_row(
            Text("TOTAL", style="bold"),
            str(len(all_ai)),
            Text(_fmt_bytes(total_ai_rss), style="bold"),
            Text(f"{total_ai_cpu:.1f}%", style="bold"),
            "",
        )

        console.print(Panel(ai_table, title="AI Agents", border_style="magenta"))

    # ── Recommendations ──
    recs = _get_recommendations(snap, categories)
    if recs:
        rec_text = Text()
        for i, rec in enumerate(recs):
            rec_text.append(f" {rec}")
            if i < len(recs) - 1:
                rec_text.append("\n")
        console.print(Panel(rec_text, title="Recommendations", border_style="yellow"))


def _get_recommendations(snap: dict, categories: dict) -> list[str]:
    """Generate actionable recommendations based on current state."""
    recs = []
    pressure = snap["mem_pressure"]
    swap_pct = (snap["swap_used"] / snap["swap_total"] * 100) if snap["swap_total"] > 0 else 0

    browser_rss = categories.get("browser", {}).get("total_rss", 0)
    ai_count = categories.get("ai_agent", {}).get("count", 0) + categories.get("conductor", {}).get("count", 0)

    if pressure in ("warn", "critical") and browser_rss > 1_000_000_000:
        recs.append(f"Close browser tabs — browsers using {_fmt_bytes(browser_rss)}")

    if pressure in ("warn", "critical") and ai_count > 2:
        recs.append(f"Reduce AI workspaces — {ai_count} agents active, suggest max 2")

    if swap_pct > 70:
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime_days = (datetime.now() - boot_time).days
            if uptime_days >= 4:
                recs.append(f"Reboot recommended — {uptime_days} days uptime, swap at {swap_pct:.0f}%")
        except (OSError, ValueError):
            pass

    docker_rss = categories.get("docker", {}).get("total_rss", 0)
    if pressure == "critical" and docker_rss > 500_000_000:
        recs.append(f"Docker/OrbStack using {_fmt_bytes(docker_rss)} — stop unused containers")

    if not recs and pressure == "normal":
        recs.append("System healthy — no action needed")

    return recs


@cli.command()
@click.option("--hours", default=24.0, help="Hours of history to show (default: 24)")
@click.option("--daily", is_flag=True, help="Show daily summary for the last 7 days")
def report(hours: float, daily: bool):
    """Show historical resource usage patterns."""
    db = Database()
    db.initialize()

    if daily:
        _show_daily_report(db)
    else:
        _show_hourly_report(db, hours)

    db.close()


def _show_hourly_report(db: Database, hours: float):
    """Show hourly breakdown for the last N hours."""
    snapshots = db.get_snapshots_since(hours)

    if not snapshots:
        console.print("[yellow]No data yet. Run 'sysmon collect' or wait for the daemon to collect data.[/]")
        return

    console.print(f"\n[bold]Resource Usage — Last {hours:.0f} hours[/] ({len(snapshots)} samples)\n")

    # Group by hour
    hourly: dict[str, list] = {}
    for snap in snapshots:
        hour = snap["ts"][:13]  # YYYY-MM-DDTHH
        hourly.setdefault(hour, []).append(snap)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Hour", min_width=6)
    table.add_column("CPU avg", justify="right")
    table.add_column("CPU max", justify="right")
    table.add_column("RAM avg", justify="right")
    table.add_column("RAM max", justify="right")
    table.add_column("Swap", justify="right")
    table.add_column("Pressure", justify="center")
    table.add_column("Samples", justify="right")

    for hour_key in sorted(hourly.keys()):
        snaps = hourly[hour_key]
        hour_label = hour_key[11:13] + ":00"  # HH:00

        cpu_vals = [s["cpu_percent"] for s in snaps if s["cpu_percent"] is not None]
        mem_vals = [s["mem_percent"] for s in snaps if s["mem_percent"] is not None]
        swap_vals = []
        for s in snaps:
            if s["swap_total"] and s["swap_total"] > 0:
                swap_vals.append(s["swap_used"] / s["swap_total"] * 100)

        pressures = [s.get("mem_pressure", "normal") for s in snaps]
        critical_pct = pressures.count("critical") / len(pressures) * 100

        pressure_display = Text()
        if critical_pct > 50:
            pressure_display.append("CRITICAL", style="bold red")
        elif critical_pct > 0 or pressures.count("warn") / len(pressures) > 0.5:
            pressure_display.append("WARN", style="yellow")
        else:
            pressure_display.append("OK", style="green")

        import statistics as stats

        table.add_row(
            hour_label,
            f"{stats.mean(cpu_vals):.0f}%" if cpu_vals else "-",
            f"{max(cpu_vals):.0f}%" if cpu_vals else "-",
            f"{stats.mean(mem_vals):.0f}%" if mem_vals else "-",
            f"{max(mem_vals):.0f}%" if mem_vals else "-",
            f"{stats.mean(swap_vals):.0f}%" if swap_vals else "-",
            pressure_display,
            str(len(snaps)),
        )

    console.print(table)

    # Category summary across the period
    _show_category_summary(db, snapshots)


def _show_category_summary(db: Database, snapshots: list[dict]):
    """Show average memory by category across snapshots."""
    category_totals: dict[str, list[int]] = {}

    for snap in snapshots:
        if "id" not in snap:
            continue
        cats = db.get_category_totals_for_snapshot(snap["id"])
        for cat, data in cats.items():
            category_totals.setdefault(cat, []).append(data["total_rss"])

    if not category_totals:
        return

    console.print("\n[bold]Average Memory by Category[/]\n")

    import statistics as stats

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Category", min_width=14)
    table.add_column("Avg Memory", justify="right")
    table.add_column("Max Memory", justify="right")
    table.add_column("Samples", justify="right")

    for cat, vals in sorted(category_totals.items(), key=lambda x: stats.mean(x[1]), reverse=True):
        label = CATEGORY_LABELS.get(cat, cat)
        color = CATEGORY_COLORS.get(cat, "white")
        table.add_row(
            Text(label, style=color),
            _fmt_bytes(int(stats.mean(vals))),
            _fmt_bytes(max(vals)),
            str(len(vals)),
        )

    console.print(table)


def _show_daily_report(db: Database):
    """Show daily summary for the last 7 days."""
    summaries = db.get_hourly_summaries(days=7)

    if not summaries:
        console.print("[yellow]No hourly summaries yet. Data rolls up after 48 hours.[/]")

        # Fall back to raw data
        snapshots = db.get_snapshots_since(168)  # 7 days
        if snapshots:
            console.print(f"[dim]Showing raw data ({len(snapshots)} samples).[/]")
            _show_hourly_report(db, 168)
        return

    console.print("\n[bold]Daily Summary — Last 7 Days[/]\n")

    # Group summaries by day
    daily: dict[str, list] = {}
    for s in summaries:
        day = s["hour"][:10]  # YYYY-MM-DD
        daily.setdefault(day, []).append(s)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Day")
    table.add_column("CPU avg", justify="right")
    table.add_column("CPU p95", justify="right")
    table.add_column("RAM avg", justify="right")
    table.add_column("RAM max", justify="right")
    table.add_column("Swap max", justify="right")
    table.add_column("Critical %", justify="right")
    table.add_column("AI Agent max", justify="right")

    import statistics as stats

    for day in sorted(daily.keys()):
        hours = daily[day]
        table.add_row(
            day,
            f"{stats.mean(h['cpu_avg'] for h in hours):.0f}%",
            f"{max(h['cpu_p95'] for h in hours):.0f}%",
            f"{stats.mean(h['mem_avg'] for h in hours):.0f}%",
            f"{max(h['mem_max'] for h in hours):.0f}%",
            f"{max(h['swap_max'] for h in hours):.0f}%",
            f"{stats.mean(h['pressure_critical_pct'] for h in hours):.0f}%",
            _fmt_bytes(max(h["ai_agent_rss_total_max"] for h in hours)),
        )

    console.print(table)


@cli.command()
def collect():
    """Collect a single system snapshot (also used by the daemon)."""
    db = Database()
    db.initialize()
    snapshot_id = collect_snapshot(db)
    db.prune()
    db.close()
    click.echo(f"Snapshot #{snapshot_id} collected")


@cli.command()
@click.option("--json-output", "use_json", is_flag=True, help="Output as JSON for piping")
def analyze(use_json: bool):
    """Output structured system analysis for Claude or other AI tools.

    Run this inside a Claude Code session and Claude can interpret the output.
    Use --json-output for machine-readable JSON.
    """
    import json as json_mod

    snap = collect_live_snapshot()

    if use_json:
        # Clean output for JSON serialization
        click.echo(json_mod.dumps(snap, indent=2, default=str))
        return

    # Structured plain-text output designed for AI consumption
    ts = snap["ts"][:19].replace("T", " ")
    mem_pct = snap["mem_percent"]
    swap_pct = (snap["swap_used"] / snap["swap_total"] * 100) if snap["swap_total"] > 0 else 0
    uptime_secs = snap.get("uptime_seconds", 0)
    uptime_days = uptime_secs // 86400
    uptime_hours = (uptime_secs % 86400) // 3600

    lines = []
    lines.append(f"# System Analysis — {ts}")
    lines.append("")
    lines.append("## Health Summary")
    lines.append(f"- Memory Pressure: {snap['mem_pressure'].upper()}")
    lines.append(f"- RAM: {_fmt_bytes(snap['mem_used'])} / {_fmt_bytes(snap['mem_total'])} ({mem_pct:.1f}%)")
    lines.append(f"- Swap: {_fmt_bytes(snap['swap_used'])} / {_fmt_bytes(snap['swap_total'])} ({swap_pct:.1f}%)")
    lines.append(f"- CPU: {snap['cpu_percent']:.1f}% (Load: {snap['load_1']:.1f} / {snap['load_5']:.1f} / {snap['load_15']:.1f})")
    lines.append(f"- Uptime: {uptime_days} days, {uptime_hours} hours")
    lines.append("")

    # Categorize
    categories: dict[str, dict] = {}
    for proc in snap["processes"]:
        cat = proc["category"]
        if cat not in categories:
            categories[cat] = {"count": 0, "total_rss": 0, "total_cpu": 0.0, "procs": []}
        categories[cat]["count"] += 1
        categories[cat]["total_rss"] += proc["rss"]
        categories[cat]["total_cpu"] += proc["cpu_percent"]
        categories[cat]["procs"].append(proc)

    # AI workload detail
    ai_procs = categories.get("ai_agent", {}).get("procs", [])
    conductor_procs = categories.get("conductor", {}).get("procs", [])
    all_ai = sorted(ai_procs + conductor_procs, key=lambda p: p["rss"], reverse=True)

    if all_ai:
        total_ai_rss = sum(p["rss"] for p in all_ai)
        lines.append(f"## AI Workload ({_fmt_bytes(total_ai_rss)} total, {len(all_ai)} processes)")
        lines.append("")
        lines.append("| Process | PID | Memory | CPU | Workspace |")
        lines.append("|---------|-----|--------|-----|-----------|")

        # Group small processes
        name_groups: dict[str, list] = {}
        for proc in all_ai:
            name_groups.setdefault(proc["name"], []).append(proc)

        grouped_names = set()
        grouped_rows = []
        for name, procs in name_groups.items():
            if len(procs) >= 3 and all(p["rss"] < 5_000_000 for p in procs):
                total = sum(p["rss"] for p in procs)
                grouped_rows.append(f"| {name} (x{len(procs)}) | - | {_fmt_bytes(total)} | 0.0% | background |")
                grouped_names.add(name)

        for proc in all_ai:
            if proc["name"] in grouped_names:
                continue
            ctx = proc.get("context") or ""
            lines.append(f"| {proc['name']} | {proc['pid']} | {_fmt_bytes(proc['rss'])} | {proc['cpu_percent']:.1f}% | {ctx} |")

        lines.extend(grouped_rows)
        lines.append("")

    # Memory by category
    lines.append("## Memory by Category")
    lines.append("")
    lines.append("| Category | Memory | Processes |")
    lines.append("|----------|--------|-----------|")
    for cat, data in sorted(categories.items(), key=lambda x: x[1]["total_rss"], reverse=True):
        label = CATEGORY_LABELS.get(cat, cat)
        lines.append(f"| {label} | {_fmt_bytes(data['total_rss'])} | {data['count']} |")
    lines.append("")

    # Top consumers
    top_procs = sorted(snap["processes"], key=lambda p: p["rss"], reverse=True)[:10]
    lines.append("## Top Consumers (by memory)")
    lines.append("")
    lines.append("| Process | Category | Memory | Workspace |")
    lines.append("|---------|----------|--------|-----------|")
    for proc in top_procs:
        cat_label = CATEGORY_LABELS.get(proc["category"], proc["category"])
        ctx = proc.get("context") or ""
        lines.append(f"| {proc['name']} | {cat_label} | {_fmt_bytes(proc['rss'])} | {ctx} |")

    click.echo("\n".join(lines))


@cli.command()
def install():
    """Install the sysmon launchd agent for background collection."""
    # Find the sysmon binary
    sysmon_bin = shutil.which("sysmon")
    if not sysmon_bin:
        # Try the current venv
        venv_bin = Path(sys.executable).parent / "sysmon"
        if venv_bin.exists():
            sysmon_bin = str(venv_bin)
        else:
            console.print("[red]Could not find sysmon binary. Install the package first.[/]")
            return

    # Create log directory
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Read plist template and substitute paths
    if not PLIST_TEMPLATE.exists():
        # Fallback: look relative to this file's installed location
        alt_template = Path(__file__).parent.parent / "com.sysmon.collector.plist"
        if not alt_template.exists():
            console.print("[red]Cannot find plist template.[/]")
            return
        plist_content = alt_template.read_text()
    else:
        plist_content = PLIST_TEMPLATE.read_text()

    plist_content = plist_content.replace("__SYSMON_BIN__", sysmon_bin)
    plist_content = plist_content.replace("__LOG_DIR__", str(LOG_DIR))

    # Write plist
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    plist_path = LAUNCH_AGENTS_DIR / f"{PLIST_NAME}.plist"

    # Unload if already loaded
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )

    plist_path.write_text(plist_content)

    # Load the agent
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        console.print(f"[green]sysmon daemon installed and started.[/]")
        console.print(f"  Plist: {plist_path}")
        console.print(f"  Logs:  {LOG_DIR}/")
        console.print(f"  DB:    {DEFAULT_DB_PATH}")
        console.print(f"  Collecting every 60 seconds.")
    else:
        console.print(f"[red]Failed to load daemon:[/] {result.stderr}")


@cli.command()
def uninstall():
    """Remove the sysmon launchd agent."""
    plist_path = LAUNCH_AGENTS_DIR / f"{PLIST_NAME}.plist"

    if not plist_path.exists():
        console.print("[yellow]sysmon daemon is not installed.[/]")
        return

    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )
    plist_path.unlink(missing_ok=True)
    console.print("[green]sysmon daemon uninstalled.[/]")


if __name__ == "__main__":
    cli()
