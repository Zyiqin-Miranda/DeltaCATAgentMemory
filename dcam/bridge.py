"""Beads bridge — syncs sessions to beads issues."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from dcam.models import ChatSession


_DCAM_BD_DEBUG = "DCAM_BD_DEBUG"


def _run_bd(args: list) -> Optional[dict]:
    """Invoke ``bd`` with --json and parse stdout.

    Returns None on any failure (process error, timeout, missing bd, or
    non-JSON output). Prior to 2026-05-27 this swallowed errors silently,
    which masked the wrong-subcommand bug (Bug 12) for weeks. Now it
    optionally surfaces stderr and the exit code on the env-var
    ``DCAM_BD_DEBUG=1`` so a similar bug shows up the first time it
    bites someone.
    """
    import os
    try:
        result = subprocess.run(["bd"] + args + ["--json"],
                                capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                if os.environ.get(_DCAM_BD_DEBUG):
                    print(f"[dcam bd debug] non-JSON stdout from `bd "
                          f"{' '.join(args)}`:\n{result.stdout[:500]}")
                return None
        if os.environ.get(_DCAM_BD_DEBUG):
            print(f"[dcam bd debug] `bd {' '.join(args)}` exited "
                  f"{result.returncode}; stderr:\n{result.stderr[:500]}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
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
        # bd's comment command is `comments add`, not `comment`. The
        # `comment` form silently no-ops on bd >= 0.60 (verified via
        # bug report 2026-05-27); written comments never landed in
        # `bd show --thread` or .beads/backup/comments.jsonl.
        _run_bd(["comments", "add", issue_id, text[:500]])
