"""Process categorization for AI-workload-aware monitoring."""

import re

# Category definitions: names are matched against process.name(),
# child_names catch helper/renderer processes, cmdline_patterns are regex fallbacks.
CATEGORIES: dict[str, dict] = {
    "ai_agent": {
        "names": {"claude", "codex", "opencode", "copilot", "aider"},
        "cmdline_patterns": [r"\bclaude\b", r"\bcodex\b", r"copilot-agent"],
    },
    "conductor": {
        "names": {"Conductor"},
        "child_names": set(),
        "cmdline_patterns": [r"conductor"],
    },
    "browser": {
        "names": {"Google Chrome", "Safari", "Arc", "Firefox", "Chromium", "Brave Browser"},
        "child_names": {
            "Google Chrome Helper",
            "Google Chrome Helper (GPU)",
            "Google Chrome Helper (Renderer)",
            "Google Chrome Helper (Plugin)",
            "WebKit.WebContent",
            "com.apple.WebKit.WebContent",
            "com.apple.WebKit.Networking",
            "com.apple.WebKit.GPU",
            "Safari Networking",
        },
    },
    "editor": {
        "names": {"Code", "Cursor", "Zed", "Sublime Text"},
        "child_names": {
            "Code Helper",
            "Code Helper (GPU)",
            "Code Helper (Plugin)",
            "Code Helper (Renderer)",
            "Code Helper (Extension)",
            "Cursor Helper",
            "Cursor Helper (GPU)",
            "Cursor Helper (Plugin)",
            "Cursor Helper (Renderer)",
        },
    },
    "docker": {
        "names": {
            "OrbStack",
            "OrbStack Helper",
            "Docker",
            "docker",
            "com.docker.vmnetd",
            "Docker Desktop",
        },
    },
    "system": {
        "names": {
            "WindowServer",
            "kernel_task",
            "mds",
            "mds_stores",
            "opendirectoryd",
            "launchd",
            "loginwindow",
            "coreaudiod",
            "bluetoothd",
            "sysmond",
            "powerd",
            "hidd",
            "logd",
            "notifyd",
            "diskarbitrationd",
            "coreduetd",
            "securityd",
        },
    },
}

# Display-friendly category labels
CATEGORY_LABELS: dict[str, str] = {
    "ai_agent": "AI Agents",
    "conductor": "Conductor",
    "browser": "Browsers",
    "editor": "Editors",
    "docker": "Docker/VMs",
    "system": "System",
    "other": "Other",
}

# Colors for rich output
CATEGORY_COLORS: dict[str, str] = {
    "ai_agent": "bright_magenta",
    "conductor": "bright_cyan",
    "browser": "bright_yellow",
    "editor": "bright_blue",
    "docker": "bright_green",
    "system": "dim",
    "other": "white",
}


def categorize(name: str | None, cmdline: list[str] | None = None) -> str:
    """Categorize a process by name and optionally cmdline.

    Three-pass approach:
    1. Direct name match against known process names and child names
    2. Cmdline argv[0] match (handles cases like claude binary named by version)
    3. Cmdline regex pattern match as fallback
    """
    if not name:
        return "other"

    # Pass 1: Direct name match
    for category, rules in CATEGORIES.items():
        if name in rules.get("names", set()):
            return category
        if name in rules.get("child_names", set()):
            return category

    # Pass 2: Check cmdline argv[0] against known names
    # Handles cases like claude binary where process name is "2.1.84" but cmdline[0] is "claude"
    if cmdline and cmdline[0]:
        argv0 = cmdline[0].rsplit("/", 1)[-1]  # basename
        for category, rules in CATEGORIES.items():
            if argv0 in rules.get("names", set()):
                return category
            if argv0 in rules.get("child_names", set()):
                return category

    # Pass 3: Cmdline regex pattern match
    if cmdline:
        cmdline_str = " ".join(cmdline)
        for category, rules in CATEGORIES.items():
            for pattern in rules.get("cmdline_patterns", []):
                if re.search(pattern, cmdline_str, re.IGNORECASE):
                    return category

    return "other"


def display_name(name: str | None, cmdline: list[str] | None = None) -> str:
    """Get a human-readable display name for a process.

    If the process name looks like a version number (e.g., '2.1.84' for claude),
    prefer cmdline[0] instead.
    """
    if not name:
        return "unknown"

    # If name looks like a version string (digits and dots only), use cmdline[0]
    if re.match(r"^[\d.]+$", name) and cmdline and cmdline[0]:
        return cmdline[0].rsplit("/", 1)[-1]

    return name
