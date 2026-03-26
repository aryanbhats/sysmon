"""Tests for process categorization."""

from sysmon.categories import categorize, display_name


class TestCategorize:
    def test_claude_by_name(self):
        assert categorize("claude") == "ai_agent"

    def test_codex_by_name(self):
        assert categorize("codex") == "ai_agent"

    def test_opencode_by_name(self):
        assert categorize("opencode") == "ai_agent"

    def test_claude_by_cmdline(self):
        assert categorize("python3", ["python3", "/path/to/claude", "--arg"]) == "ai_agent"

    def test_chrome_main(self):
        assert categorize("Google Chrome") == "browser"

    def test_chrome_helper(self):
        assert categorize("Google Chrome Helper") == "browser"

    def test_chrome_helper_renderer(self):
        assert categorize("Google Chrome Helper (Renderer)") == "browser"

    def test_webkit_content(self):
        assert categorize("WebKit.WebContent") == "browser"

    def test_vscode(self):
        assert categorize("Code") == "editor"

    def test_vscode_helper(self):
        assert categorize("Code Helper") == "editor"

    def test_vscode_helper_plugin(self):
        assert categorize("Code Helper (Plugin)") == "editor"

    def test_cursor(self):
        assert categorize("Cursor") == "editor"

    def test_orbstack(self):
        assert categorize("OrbStack") == "docker"

    def test_orbstack_helper(self):
        assert categorize("OrbStack Helper") == "docker"

    def test_conductor(self):
        assert categorize("Conductor") == "conductor"

    def test_windowserver(self):
        assert categorize("WindowServer") == "system"

    def test_kernel_task(self):
        assert categorize("kernel_task") == "system"

    def test_unknown_process(self):
        assert categorize("SomeRandomApp") == "other"

    def test_none_name(self):
        assert categorize(None) == "other"

    def test_empty_name(self):
        assert categorize("") == "other"

    def test_none_cmdline_still_matches_name(self):
        assert categorize("claude", None) == "ai_agent"

    def test_codex_cmdline_pattern(self):
        assert categorize("node", ["/usr/bin/node", "codex", "exec"]) == "ai_agent"

    def test_safari(self):
        assert categorize("Safari") == "browser"

    def test_arc_browser(self):
        assert categorize("Arc") == "browser"

    def test_claude_version_name(self):
        """Claude binary process name is the version number."""
        assert categorize("2.1.84", ["claude"]) == "ai_agent"

    def test_claude_version_name_with_path(self):
        assert categorize("2.1.84", ["/Users/foo/.local/share/claude/versions/2.1.84"]) == "ai_agent"


class TestDisplayName:
    def test_normal_name_unchanged(self):
        assert display_name("claude") == "claude"

    def test_version_name_uses_cmdline(self):
        assert display_name("2.1.84", ["claude"]) == "claude"

    def test_version_name_strips_path(self):
        assert display_name("2.1.84", ["/usr/bin/claude"]) == "claude"

    def test_none_returns_unknown(self):
        assert display_name(None) == "unknown"

    def test_normal_name_ignores_cmdline(self):
        assert display_name("Google Chrome", ["chrome"]) == "Google Chrome"
