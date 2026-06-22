"""Diagnostic probes for DCAM.

``dcam doctor`` runs a fixed list of OK/FAIL checks so when something
breaks, you have one obvious first-step command. Each probe is
independent, side-effect-free, and short-timeout: the whole run finishes
in under a second on a healthy box.

Exit codes:
  0 — every probe passed
  1 — at least one probe failed

Bug 19 in the bug report.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple


def _probe_bd_binary() -> Tuple[str, bool, str]:
    if not shutil.which("bd"):
        return ("bd binary", False,
                "not found on PATH; install bd or skip multi-agent features")
    try:
        out = subprocess.run(
            ["bd", "version"], capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return ("bd binary", False,
                    f"bd version exited {out.returncode}: "
                    f"{out.stderr.strip()[:80]}")
        return ("bd binary", True, f"v{(out.stdout or '').strip()[:40]}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return ("bd binary", False, str(e))


def _probe_bd_database() -> Tuple[str, bool, str]:
    """Walk up looking for a `.beads/` like dcam.bridge does. Without one,
    bd resolves no DB and any list/show errors with circuit-breaker."""
    cur = Path.cwd().resolve()
    while True:
        if (cur / ".beads").is_dir():
            return ("bd database", True, f".beads/ at {cur}")
        if cur.parent == cur:
            break
        cur = cur.parent
    return ("bd database", False,
            "no .beads/ in cwd or any parent — run `bd init` or "
            "`dcam project init`")


def _probe_bd_dolt() -> Tuple[str, bool, str]:
    """`bd list` is the canonical liveness probe — if circuit-breaker is
    open, this trips even when the binary exists and a DB is present."""
    if not shutil.which("bd"):
        return ("bd dolt server", False, "bd binary missing; skipped")
    try:
        out = subprocess.run(
            ["bd", "list", "--json"],
            capture_output=True, text=True, timeout=8,
        )
        # bd writes "Error: no beads database" or
        # "circuit breaker is open" to stdout/stderr on failure.
        combined = (out.stdout or "") + (out.stderr or "")
        if "circuit breaker" in combined.lower():
            return ("bd dolt server", False,
                    "circuit breaker open — Dolt SQL server is down. "
                    "See TMUX.md § Recovering from a corrupted bd database")
        if "no beads database" in combined.lower():
            return ("bd dolt server", False,
                    "no beads database; `bd init` first")
        if out.returncode == 0:
            return ("bd dolt server", True, "responsive")
        return ("bd dolt server", False,
                f"`bd list` exited {out.returncode}: {combined[:80].strip()}")
    except subprocess.TimeoutExpired:
        return ("bd dolt server", False, "bd list timed out after 8s")
    except (FileNotFoundError, OSError) as e:
        return ("bd dolt server", False, str(e))


def _probe_tmux() -> Tuple[str, bool, str]:
    if not shutil.which("tmux"):
        return ("tmux binary", False,
                "not found on PATH; multi-agent commands will fail")
    try:
        out = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, timeout=3,
        )
        return ("tmux binary", out.returncode == 0,
                (out.stdout or out.stderr).strip()[:40])
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return ("tmux binary", False, str(e))


def _probe_claude() -> Tuple[str, bool, str]:
    if not shutil.which("claude"):
        return ("claude CLI", False,
                "not on PATH; `dcam tmux start/dev/review --launch` "
                "won't be able to spawn agents")
    return ("claude CLI", True, shutil.which("claude") or "")


def _probe_claude_projects() -> Tuple[str, bool, str]:
    p = Path.home() / ".claude" / "projects"
    if not p.exists():
        return ("~/.claude/projects/", False,
                "missing — `dcam claude sync/init` will create it lazily, "
                "but a Claude Code session must have run at least once first")
    if not os.access(p, os.W_OK):
        return ("~/.claude/projects/", False, "exists but not writable")
    n = len([d for d in p.iterdir() if d.is_dir()])
    return ("~/.claude/projects/", True, f"{n} project dirs")


def _probe_claude_state() -> Tuple[str, bool, str]:
    """First-run prompts (theme + per-project trust). Bug 11 fix."""
    state = Path.home() / ".claude.json"
    if not state.exists():
        return ("~/.claude.json", False,
                "missing; `dcam claude bootstrap` will create it. "
                "Without it, launched agents block on first-run prompts.")
    try:
        import json as _json
        data = _json.loads(state.read_text() or "{}")
    except _json.JSONDecodeError:
        return ("~/.claude.json", False, "invalid JSON")
    if not data.get("hasCompletedOnboarding"):
        return ("~/.claude.json", False,
                "hasCompletedOnboarding not set; theme picker will block "
                "agent launches. Fix: `dcam claude bootstrap`")
    return ("~/.claude.json", True, "onboarded, theme set")


def _probe_python() -> Tuple[str, bool, str]:
    """Sanity-check the Python the CLI is running under can import dcam."""
    try:
        import dcam  # noqa: F401
        return ("dcam package", True, f"importable in {os.sys.executable}")
    except Exception as e:
        return ("dcam package", False, f"import failed: {e}")


def _probe_dcam_root() -> Tuple[str, bool, str]:
    """Project-mode storage discoverable from cwd?"""
    from dcam.project import discover_root, is_project_root
    root = discover_root(None)
    mode = "project" if is_project_root(root) else "global"
    return ("dcam storage root", True, f"{mode} @ {root}")


def _probe_git_repo() -> Tuple[str, bool, str]:
    """Project mode requires a git repo; report which one cwd is in."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return ("git repo", False,
                    "not inside a git repo (project mode needs one)")
        return ("git repo", True, out.stdout.strip()[:80])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ("git repo", False, "git not available")


_PROBES = [
    _probe_python,
    _probe_git_repo,
    _probe_dcam_root,
    _probe_tmux,
    _probe_claude,
    _probe_claude_projects,
    _probe_claude_state,
    _probe_bd_binary,
    _probe_bd_database,
    _probe_bd_dolt,
]


def run_doctor() -> int:
    """Run every probe, print OK/FAIL per line, return an exit code.

    Returns 0 if everything passed, 1 otherwise.
    """
    print("dcam doctor")
    print("=" * 60)
    failures = 0
    for probe in _PROBES:
        try:
            name, ok, detail = probe()
        except Exception as e:
            name, ok, detail = (probe.__name__, False, f"crashed: {e}")
        marker = "  OK  " if ok else " FAIL "
        if not ok:
            failures += 1
        print(f"[{marker}] {name:<25}  {detail}")
    print("=" * 60)
    if failures:
        print(f"{failures} failure(s). Each row above suggests a remediation.")
        return 1
    print("All probes passed.")
    return 0
