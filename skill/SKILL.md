---
name: sysmon
description: |
  System resource monitoring for macOS. Diagnoses memory pressure, swap issues,
  AI agent resource usage, and workspace-switching lag. Use when the user mentions
  system performance, slowness, memory, swap, rebooting, or asks how many AI agents
  their machine can handle. Also use when workspace switching feels slow, or the user
  wants to know what's eating their RAM.
---

# sysmon — System Resource Monitor

You have access to `sysmon`, a macOS system monitor that tracks CPU, memory, swap, disk,
and per-process data with AI-workload-aware categorization.

## Command Reference

| Command | What it does |
|---------|-------------|
| `sysmon status` | Quick visual health check — gauges, categories, AI agents with workspace names |
| `sysmon status --watch N` | Auto-refreshing dashboard every N seconds (min 5). Ctrl-C to stop |
| `sysmon analyze` | Structured markdown breakdown — includes Top Consumers showing individual apps |
| `sysmon analyze --json-output` | Raw JSON of all processes and metrics — use for specific data queries |
| `sysmon report --hours N` | Historical patterns over the last N hours |
| `sysmon report --daily` | Multi-day summary (requires 48h of collected data) |
| `sysmon collect` | Manually trigger a data snapshot to the database |

## When to Use Which Command

| User says | Run this |
|-----------|----------|
| "why is my mac slow" / "system is laggy" | `sysmon analyze` |
| "how much memory am I using" | `sysmon status` |
| "how many agents can I run" | `sysmon status` — check pressure + agent count |
| "what's eating my RAM" / "where is memory going" | `sysmon analyze` — Top Consumers table |
| "should I reboot" | `sysmon status` — check swap % and uptime |
| "what happened while I was away" | `sysmon report --hours N` |
| "which workspace is using the most memory" | `sysmon analyze` — AI Workload table |
| Specific data question (e.g., "processes > 200MB") | `sysmon analyze --json-output` — parse the JSON |
| "is Spotify using a lot of memory" / specific app | `sysmon analyze` — check Top Consumers |

## How to Interpret Results

### Memory Pressure
- **NORMAL** — system is healthy, no action needed
- **WARN** — memory is tight, workspace switching may lag. Consider closing unused workspaces or browser tabs
- **CRITICAL** — actively degraded performance. Close apps or reboot

### Swap
- Below 50% — healthy
- 50-70% — elevated but usually fine
- Above 70% with 4+ days uptime — recommend reboot to clear stale swap pages

### Workspace Column
- **`the-ab-index/rome`** — a Conductor workspace (project/workspace name)
- **`0DevProjects`** — a terminal session working directory
- **`background`** — daemon process (codex servers, conductor node) with cwd at `/`
- **`headless (automation)`** — browser automation (gstack /browse), not user tabs

### Process Grouping
- Small identical processes (e.g., 14 codex servers at 1.4 MB each) are grouped as `codex (x14)` — negligible memory
- AI agents are listed individually with workspace context
- Everything else is grouped by category in the status view

## Key Insights

1. **"Other" hides individual apps.** `sysmon status` groups non-AI, non-browser apps into "Other: 3.5 GB". Run `sysmon analyze` to see the Top Consumers table showing Spotify, WhatsApp, iTerm2, etc.

2. **Browser memory includes automation.** Headless Chrome from tools like gstack's `/browse` shows up as browser memory but has no visible tabs. Look for `headless (automation)` in the context column.

3. **Each Claude instance uses 100-500 MB** depending on context window usage. When memory pressure hits WARN with 3+ agents, workspace switching starts lagging — that's a RAM limit, not a CPU limit.

4. **Reboot clears swap.** After several days, macOS accumulates stale swap pages. A reboot wipes them clean and gives you fresh RAM. This is the single most effective fix for accumulated slowness.

5. **Don't kill processes by name.** Multiple Claude instances serve different workspaces. Use `sysmon status` to identify which PID is which workspace, then `kill <PID>` specifically.
