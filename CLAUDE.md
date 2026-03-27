# sysmon — Development Guide

## Project overview

macOS system resource monitor CLI. Collects CPU/memory/swap/disk/process data to SQLite, categorizes processes (AI agents, browsers, editors, Docker, system), shows workspace context for Claude/Codex instances.

## Tech stack

- Python 3.11+, managed with `uv`
- `psutil` for system data, `rich` for terminal output, `click` for CLI
- SQLite (WAL mode) at `~/.local/share/sysmon/sysmon.db`
- macOS-specific: `memory_pressure` command, `proc_pid_rusage` via ctypes, `launchd`

## Project structure

```
sysmon/
├── cli.py           # Click CLI — status, analyze, report, collect, install
├── collector.py     # psutil + macOS-specific data collection, workspace context
├── categories.py    # Process categorization rules + display_name()
└── db.py            # SQLite schema, WAL mode, migrations, rollup, queries
skill/
└── SKILL.md         # Claude Code skill for automatic sysmon integration
tests/
├── test_categories.py
├── test_collector.py
└── test_db.py
```

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
```

## Key conventions

- Process categorization in `categories.py` — add new categories there, not inline
- Live snapshots (`collect_live_snapshot`) include workspace context; DB snapshots don't (privacy)
- `_print_status()` is the shared renderer for both single-shot and `--watch` mode
- Raw cmdline is never stored — only SHA256 hash (16 chars)
- Use `_get_process_context()` for workspace detection, `_get_footprint()` for macOS memory footprint
- Tests use `tmp_path` fixture for isolated SQLite databases

## Testing

66 tests. Run with `pytest tests/ -v`. Key test areas:
- `test_categories.py` — name matching, cmdline fallback, display_name, version-named binaries
- `test_collector.py` — cmdline hashing, memory pressure parsing, workspace context, headless detection
- `test_db.py` — WAL mode, indexes, insert/query, pruning, rollup, concurrent access
