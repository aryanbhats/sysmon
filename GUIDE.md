# Using sysmon with AI Coding Agents

A practical guide for developers running Claude, Codex, Copilot, or other AI coding agents who want to keep their Mac running smoothly.

## Quick health check before starting work

Run `sysmon status` to see where your system stands:

```bash
sysmon status
```

Focus on two things:
- **Memory Pressure**: NORMAL is fine, WARN means you're getting tight, CRITICAL means things are about to slow down
- **Swap %**: Below 50% is healthy. Above 70% with several days of uptime means a reboot will help.

## Using sysmon inside Claude Code

The most powerful way to use sysmon is inside a Claude Code session. Claude can read the output and give you tailored advice.

**In your Claude Code prompt, type:**

```
! sysmon analyze
```

The `!` prefix runs a shell command in your session. Claude sees the structured output and can interpret it — telling you which workspaces to close, whether your browser tabs or AI agents are the bottleneck, and when to reboot.

**Other commands you can run in Claude:**

```
! sysmon status                  # Visual gauges and color-coded panels
! sysmon analyze --json-output   # Raw JSON — Claude can parse specific fields
! sysmon report --hours 4        # What happened over the last 4 hours
```

## Three levels of detail

sysmon gives you different levels of detail depending on what you need:

### `sysmon status` — the overview

Shows gauges and category totals. AI agents are broken out individually with workspace names. Everything else (browsers, editors, etc.) is grouped by category. Use this for a quick "am I OK?" check.

### `sysmon analyze` — the breakdown

Shows everything `status` shows, plus:
- **Top Consumers table** — the 10 biggest individual processes across ALL categories. This is where you'll see specific apps like Spotify (322 MB), WhatsApp (198 MB), iTerm2 (263 MB) that are hidden inside "Other: 3.5 GB" in the status view.
- Structured markdown that Claude can read and interpret in-session.

Use this when you want to know "what's actually inside that 3.5 GB of Other?"

### `sysmon analyze --json-output` — the raw data

Dumps every process, every metric as JSON. Use this when you want Claude to answer specific questions like:
- "Which processes are using more than 200 MB?"
- "How much total memory is Spotify using across all its processes?"
- "What's running in the-ab-index workspace?"

Run `! sysmon analyze --json-output` and then ask your question — Claude will parse the JSON directly.

## Understanding the Workspace column

`sysmon status` shows which project or workspace each AI agent is working in:

```
Process    PID    Memory  CPU %  Workspace
claude    29334  206 MB   0.5%   the-ab-index/rome
claude    39455  214 MB   0.4%   the-ab-index/indianapolis
claude     9045  413 MB  18.6%   0DevProjects
codex (x14)   -   23 MB   0.0%   background
```

What these mean:
- **`the-ab-index/rome`** — a Conductor workspace named "rome" in the "the-ab-index" project
- **`0DevProjects`** — a Claude session running directly in this directory
- **`sysmon`** — working in the sysmon repo
- **`background`** — a daemon process (like codex servers) with no meaningful working directory

This is inferred from the process's working directory (`cwd`). It's a best guess, not a guarantee — but it's right most of the time.

## How many agents can your Mac handle?

This depends on YOUR machine and YOUR workload. Don't trust fixed numbers — measure it.

**How to find your limit:**

1. Run `sysmon status` and note the current memory pressure
2. Open one more AI workspace
3. Run `sysmon status` again
4. When pressure hits **WARN** and you notice lag switching between workspaces, you've found your limit

**General patterns we've observed:**
- Each Claude instance uses 100–500 MB depending on context window usage
- Each codex server uses ~1.4 MB (negligible)
- Conductor adds ~100 MB overhead per app
- The workspace-switching lag you feel is macOS decompressing/swapping in dormant pages — it's a RAM limit, not a CPU limit

## When to reboot

macOS accumulates stale data in swap over time. After several days, this can slow everything down even if you've closed the apps that caused it.

**Reboot when:**
- Swap is above 70% AND uptime is 4+ days
- `sysmon status` keeps showing WARN/CRITICAL pressure even after closing apps
- Workspace switching feels sluggish

**Check your uptime and swap trend:**
```bash
sysmon status              # shows current swap %
sysmon report --hours 24   # shows the trend
```

Note: `sysmon report --daily` shows multi-day patterns but requires 48 hours of collected data before rollups appear.

## Watch mode

Leave sysmon running in a terminal pane to monitor in real-time:

```bash
sysmon status --watch 10   # refresh every 10 seconds
```

Minimum interval is 5 seconds. Press Ctrl-C to stop.

## The headless browser gotcha

Some tools (like gstack's `/browse` and `/qa`) spawn a **headless Chrome** for browser automation. This shows up in sysmon as browser memory, but you can't see any tabs — because there aren't any.

sysmon detects this and shows `headless (automation)` in the context. It may also recommend stopping it if it's using significant memory.

**How to check manually:**
```bash
ps aux | grep "Chrome.*headless"
```

**How to safely stop it:**
1. Find the PID in `sysmon status` — look for processes with `headless (automation)` context
2. `kill <PID>` (not `kill -9`, and not `pkill` — you might kill your real browser)
3. It will restart automatically next time you run `/browse`

## Safe process cleanup

When you need to free memory by stopping AI agents:

1. **Never kill by name** — you have multiple Claude instances serving different workspaces
2. Run `sysmon status` to see which PID is which workspace
3. Decide which workspace you don't need right now
4. Verify before killing: `ps -p <PID> -o pid,command`
5. Stop it cleanly: `kill <PID>`

## Claude Code skill (optional)

sysmon includes a Claude Code skill that teaches Claude when and how to use sysmon commands automatically. When installed, Claude will reach for sysmon when you mention system performance, slowness, or memory — without you needing to remember the commands.

**Install via symlink** (stays updated with git pulls):

```bash
mkdir -p ~/.claude/skills
ln -sf "$(pwd)/skill" ~/.claude/skills/sysmon
```

**Or copy manually:**

```bash
mkdir -p ~/.claude/skills/sysmon
cp skill/SKILL.md ~/.claude/skills/sysmon/SKILL.md
```

After installing, start a new Claude Code session. Try saying "my mac is slow" or "what's eating my RAM" — Claude will automatically run the right sysmon command and interpret the results.

## Privacy

**What sysmon stores** (in `~/.local/share/sysmon/sysmon.db`):
- System metrics: CPU %, memory %, swap, disk, load averages
- Process names, PIDs, memory usage, category
- Command lines are stored as **SHA256 hashes** — not the actual text

**What `sysmon analyze` shows in real-time** (not stored):
- Process names, PIDs, workspace paths
- If your workspace paths contain sensitive project names, don't share the raw output publicly
- Use `sysmon analyze --json-output` with care in shared environments
