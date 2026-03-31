"""Tests for kiro hook installation."""

import json
import tempfile
from pathlib import Path

from dcam.kiro import install_hooks


def test_install_hooks_creates_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        hook_path, config_path = install_hooks(tmpdir)

        assert Path(hook_path).exists()
        assert Path(config_path).exists()

        # Hook should be executable
        assert Path(hook_path).stat().st_mode & 0o111

        # Config should be valid JSON
        config = json.loads(Path(config_path).read_text())
        assert config["name"] == "dcam"
        assert "hooks" in config
