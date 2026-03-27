"""Tests for the collector module."""

import hashlib
from unittest.mock import patch, MagicMock

import pytest

from sysmon.collector import (
    _hash_cmdline,
    _get_process_context,
    get_memory_pressure,
)


class TestHashCmdline:
    def test_none_returns_none(self):
        assert _hash_cmdline(None) is None

    def test_empty_list_returns_none(self):
        assert _hash_cmdline([]) is None

    def test_returns_16_char_hash(self):
        result = _hash_cmdline(["python3", "script.py"])
        assert result is not None
        assert len(result) == 16

    def test_deterministic(self):
        cmd = ["python3", "/usr/bin/claude", "--model", "opus"]
        assert _hash_cmdline(cmd) == _hash_cmdline(cmd)

    def test_different_cmds_different_hashes(self):
        h1 = _hash_cmdline(["python3", "a.py"])
        h2 = _hash_cmdline(["python3", "b.py"])
        assert h1 != h2


class TestProcessContext:
    @patch("sysmon.collector.psutil.Process")
    def test_conductor_workspace(self, mock_proc_cls):
        mock_proc_cls.return_value.cwd.return_value = (
            "/Users/aryan/conductor/workspaces/the-ab-index/rome"
        )
        result = _get_process_context(1234, "ai_agent")
        assert result == "the-ab-index/rome"

    @patch("sysmon.collector.psutil.Process")
    def test_regular_repo(self, mock_proc_cls):
        mock_proc_cls.return_value.cwd.return_value = (
            "/Users/aryan/Documents/0DevProjects/sysmon"
        )
        result = _get_process_context(1234, "ai_agent")
        assert result == "sysmon"

    @patch("sysmon.collector.psutil.Process")
    def test_root_cwd_returns_background(self, mock_proc_cls):
        mock_proc_cls.return_value.cwd.return_value = "/"
        result = _get_process_context(1234, "ai_agent")
        assert result == "background"

    def test_non_ai_returns_none(self):
        result = _get_process_context(1234, "browser")
        assert result is None

    def test_other_category_returns_none(self):
        result = _get_process_context(1234, "other")
        assert result is None

    @patch("sysmon.collector.psutil.Process")
    def test_access_denied_returns_none(self, mock_proc_cls):
        import psutil
        mock_proc_cls.return_value.cwd.side_effect = psutil.AccessDenied(1234)
        result = _get_process_context(1234, "ai_agent")
        assert result is None

    @patch("sysmon.collector.psutil.Process")
    def test_conductor_category(self, mock_proc_cls):
        mock_proc_cls.return_value.cwd.return_value = (
            "/Users/aryan/conductor/workspaces/project/workspace-name"
        )
        result = _get_process_context(1234, "conductor")
        assert result == "project/workspace-name"

    def test_browser_headless_detected(self):
        cmdline = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "--headless", "--no-startup-window", "--remote-debugging-port=0"]
        result = _get_process_context(1234, "browser", cmdline)
        assert result == "headless (automation)"

    def test_browser_normal_returns_none(self):
        cmdline = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        result = _get_process_context(1234, "browser", cmdline)
        assert result is None

    def test_browser_no_cmdline_returns_none(self):
        result = _get_process_context(1234, "browser", None)
        assert result is None


class TestMemoryPressure:
    @patch("sysmon.collector.subprocess.run")
    def test_normal_pressure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="The system has 17179869184\nSystem-wide memory free percentage: 60%\n",
            returncode=0,
        )
        assert get_memory_pressure() == "normal"

    @patch("sysmon.collector.subprocess.run")
    def test_warn_pressure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="System-wide memory free percentage: 35%\n",
            returncode=0,
        )
        assert get_memory_pressure() == "warn"

    @patch("sysmon.collector.subprocess.run")
    def test_critical_pressure(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="System-wide memory free percentage: 10%\n",
            returncode=0,
        )
        assert get_memory_pressure() == "critical"

    @patch("sysmon.collector.subprocess.run")
    def test_fallback_on_timeout(self, mock_run):
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="memory_pressure", timeout=5)
        # Should fall back to psutil-based estimate, not crash
        result = get_memory_pressure()
        assert result in ("normal", "warn", "critical")

    @patch("sysmon.collector.subprocess.run")
    def test_fallback_on_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        result = get_memory_pressure()
        assert result in ("normal", "warn", "critical")
