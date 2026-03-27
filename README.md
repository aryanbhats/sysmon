# sysmon

Lightweight macOS system resource monitor with AI-workload-aware process grouping.

Built for developers running multiple AI coding agents (Claude, Codex, Copilot) alongside browsers, editors, and Docker — who want to understand where their RAM is actually going.

## What it does

- **Collects** CPU, memory, swap, disk, and per-process data every 60 seconds to a local SQLite database
- **Categorizes** processes into meaningful groups: AI agents, browsers, editors, Docker/VMs, system
- **Shows** instant system health with `sysmon status` — color-coded gauges, grouped memory usage, workspace context for each AI agent, and actionable recommendations
- **Analyzes** your system for AI tools with `sysmon analyze` — structured output that Claude Code can read and interpret directly
- **Reports** historical patterns with `sysmon report` — peak pressure hours, category breakdowns, swap trends
- **Watches** in real-time with `sysmon status --watch 10` — auto-refreshing dashboard
- **Runs silently** via a macOS launchd agent — zero interaction needed after setup

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/aryanbhats/sysmon.git
cd sysmon
uv venv && source .venv/bin/activate
uv pip install -e .

# Try it out
sysmon status

# Install background collection (every 60s)
sysmon install
```

To make `sysmon` available from any terminal tab:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/.venv/bin/sysmon" ~/.local/bin/sysmon
```

Make sure `~/.local/bin` is in your `PATH`. Add this to `~/.zshrc` if it isn't:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Usage

### `sysmon status`

Live snapshot of system health with AI agent grouping:

```
╭─────────────────────── System Status ────────────────────────╮
│  CPU:   18.4% ███░░░░░░░░░░░░░░░░░  Load: 5.2 / 6.2 / 5.8  │
│  RAM:   76.6% ███████████████░░░░░  6.2 GB / 16.0 GB        │
│  Swap:  88.7% █████████████████░░░  7.1 GB / 8.0 GB         │
│  Disk:   6.7% █░░░░░░░░░░░░░░░░░░░  231.9 GB free           │
│  Pressure: WARN                                              │
╰──────────────────────────────────────────────────────────────╯
╭──────────────── Resource Usage by Category ───────────────────╮
│ Category        Processes      Memory     CPU %               │
│ Browsers               45      3.2 GB      0.0%               │
│ AI Agents              28      1.8 GB      0.0%               │
│ Docker/VMs              2    522.3 MB      0.0%               │
│ Editors                 5    133.4 MB      0.0%               │
╰──────────────────────────────────────────────────────────────╯
╭────────────────────────── AI Agents ─────────────────────────╮
│ Process    PID    Memory  CPU %  Workspace                    │
│ claude    29334  206 MB   0.5%   the-ab-index/rome            │
│ claude    39455  214 MB   0.4%   the-ab-index/indianapolis    │
│ claude     9045  413 MB  18.6%   0DevProjects                 │
│ codex (x14)   -   23 MB  0.0%   background                   │
│ TOTAL        19  1.8 GB  19.5%                                │
╰──────────────────────────────────────────────────────────────╯
╭────────────────────── Recommendations ───────────────────────╮
│  Reduce AI workspaces — 19 agents active, suggest max 2      │
│  Reboot recommended — 6 days uptime, swap at 89%             │
╰──────────────────────────────────────────────────────────────╯
```

### `sysmon report`

Historical resource usage patterns from collected data:

```bash
sysmon report              # Last 24 hours
sysmon report --hours 4    # Last 4 hours
sysmon report --daily      # Daily summary for the last 7 days
```

Shows hourly breakdowns of CPU, RAM, swap, memory pressure, and per-category memory usage.

### `sysmon analyze`

Structured output designed for AI tools to read. Run inside a Claude Code session:

```
! sysmon analyze             # Markdown output Claude can interpret
! sysmon analyze --json-output   # Raw JSON for programmatic use
```

### `sysmon status --watch`

Auto-refreshing dashboard:

```bash
sysmon status --watch 10   # Refresh every 10 seconds (min 5)
```

Press Ctrl-C to stop.

### `sysmon collect`

Manually trigger a single data collection snapshot.

### `sysmon install` / `sysmon uninstall`

Install or remove the launchd agent that collects data every 60 seconds in the background.

Data is stored at `~/.local/share/sysmon/sysmon.db`. Logs go to `~/Library/Logs/sysmon/`.

## Process categories

sysmon groups processes so you can see where memory is actually going:

| Category | Processes matched |
|----------|-------------------|
| **AI Agents** | claude, codex, opencode, copilot, aider |
| **Conductor** | Conductor app and its child processes |
| **Browsers** | Chrome, Safari, Arc, Firefox + all helper/renderer processes |
| **Editors** | VS Code, Cursor, Zed + helper processes |
| **Docker/VMs** | OrbStack, Docker Desktop |
| **System** | WindowServer, kernel_task, mds, launchd, etc. |
| **Other** | Everything else (filtered to >10 MB) |

## Claude Code skill

sysmon includes a Claude Code skill that teaches Claude when and how to use sysmon automatically. Once installed, just say "my mac is slow" or "what's eating my RAM" — Claude will run the right command and interpret the results without you needing to remember the syntax.

```bash
# Install via symlink (stays updated with git pulls)
mkdir -p ~/.claude/skills
ln -sf "$(pwd)/skill" ~/.claude/skills/sysmon
```

See [GUIDE.md](GUIDE.md) for more details on using sysmon with AI coding agents.

## Architecture

```
sysmon/
├── cli.py           # Click CLI — status, analyze, report, collect, install
├── collector.py     # psutil data collection + macOS-specific APIs
├── categories.py    # Process categorization rules
└── db.py            # SQLite with WAL mode, migrations, rollup
skill/
└── SKILL.md         # Claude Code skill for automatic sysmon integration
```

**Data flow:** `launchd` runs `sysmon collect` every 60s. The collector uses [psutil](https://github.com/giampaolo/psutil) for CPU/memory/swap/process data, parses macOS `memory_pressure` for pressure level, and uses `proc_pid_rusage` via ctypes for true memory footprint. Data goes to SQLite in WAL mode for safe concurrent reads.

**Retention:** Raw snapshots kept for 7 days, then rolled up to hourly summaries (preserving min/max/p95) kept for 90 days.

**Overhead:** ~25-35 MB for 2-3 seconds every 60s. 0 MB between collections. ~1.5 MB/day of SQLite storage.

## Data storage

All data stays local on your machine:

| What | Where |
|------|-------|
| SQLite database | `~/.local/share/sysmon/sysmon.db` |
| Daemon logs | `~/Library/Logs/sysmon/` |
| launchd plist | `~/Library/LaunchAgents/com.sysmon.collector.plist` |

Process command lines are stored as SHA256 hashes (not raw text) for privacy.

## Requirements

- macOS (uses macOS-specific APIs: `memory_pressure`, `proc_pid_rusage`, `launchd`)
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
```

66 tests covering process categorization, database operations (WAL mode, concurrency, rollup, pruning), collector logic (memory pressure parsing, cmdline hashing, workspace context, headless browser detection).

See [GUIDE.md](GUIDE.md) for a practical guide on using sysmon with AI coding agents.

## License

MIT
