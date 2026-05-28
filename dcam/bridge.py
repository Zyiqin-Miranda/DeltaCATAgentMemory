"""Beads bridge — syncs sessions to beads issues."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from dcam.models import ChatSession


def _run_bd(args: list) -> Optional[dict]:
    try:
        result = subprocess.run(["bd"] + args + ["--json"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return None


_bd_ok: Optional[bool] = None


def bd_available() -> bool:
    """True if the `bd` binary is on PATH and responds to `bd version`."""
    global _bd_ok
    if _bd_ok is None:
        _bd_ok = _run_bd(["version"]) is not None
    return _bd_ok


def bd_database_initialized(cwd: Optional[str] = None) -> bool:
    """True if `bd` is reachable AND a beads database is initialized for cwd.

    Distinct from `bd_available()`: bd may be on PATH but the working
    directory may have no `.beads/` (or `BEADS_DIR` may be unset). In that
    state, `bd list` errors with `no beads database found`. We probe by
    walking up for a `.beads/` directory, since bd resolves the database
    that way and the check costs no subprocess.
    """
    if not bd_available():
        return False
    start = Path(cwd) if cwd else Path.cwd()
    cur = start.resolve()
    while True:
        if (cur / ".beads").is_dir():
            return True
        if cur.parent == cur:
            return False
        cur = cur.parent


def create_session_issue(session: ChatSession) -> Optional[str]:
    if not bd_available():
        return None
    result = _run_bd(["create", f"Chat: {session.title}",
                      "--label", "type:chat-session",
                      "--label", f"session:{session.session_id}"])
    return result.get("id") if result else None


def close_session_issue(issue_id: str, reason: str):
    if bd_available() and issue_id:
        _run_bd(["close", issue_id, "--reason", reason])


def comment_issue(issue_id: str, text: str):
    if bd_available() and issue_id:
        _run_bd(["comment", issue_id, text[:500]])
