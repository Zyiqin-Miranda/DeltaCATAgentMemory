"""Tests for beads bridge."""

from dcam.bridge import bd_available, _run_bd


def test_bd_available_returns_bool():
    # bd may or may not be installed — just verify it returns a bool
    result = bd_available()
    assert isinstance(result, bool)


def test_run_bd_nonexistent_command():
    result = _run_bd(["nonexistent-subcommand-xyz"])
    assert result is None
