"""Tests for the collector module."""

import hashlib
from unittest.mock import patch, MagicMock

import pytest

from sysmon.collector import (
    _hash_cmdline,
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
