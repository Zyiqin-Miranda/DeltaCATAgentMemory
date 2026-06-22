"""``dcam manager-loop`` — bridge for foreground managers.

Most common deployment for Amazon-internal users: the manager Claude
runs in the user's local Mac terminal, and dev/reviewer Claudes run in
tmux on a remote dev desk. The manager is **foreground-only**: it can't
tick on its own schedule between user turns.

This module exposes ``dcam manager-loop --remote ssh-host:session
[--out FILE]`` — a daemon the user starts in a separate Mac terminal.
It spawns ``dcam tmux watch`` (over SSH if --remote is set) and tees
the NDJSON event stream into a local file. The foreground manager
Claude reads the tail of that file at the start of each user turn and
acts on anything new.

Why not just have the manager call ``dcam tmux watch`` directly? Watch
is a long-running command. A foreground Claude session only runs
during a user turn; it can't keep watch open in the background. The
loop is the missing tick.

Pairs with Bug 15 (local-manager + remote-workers architecture) and
Bug 20 (foreground-poll gap).
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, TextIO


def _build_watch_argv(remote: Optional[str], session: str,
                      root: Optional[str], interval: int) -> List[str]:
    """Same shape `dcam.feed._build_watch_argv` uses; copied to keep
    these two consumers independently importable.
    """
    if remote:
        host, _, remote_session = remote.partition(":")
        if remote_session:
            session = remote_session
        argv = ["ssh", host, "dcam"]
        if root:
            argv += ["--root", root]
        argv += ["tmux", "watch", session, "--interval", str(interval)]
        return argv
    argv = ["dcam"]
    if root:
        argv += ["--root", root]
    argv += ["tmux", "watch", session, "--interval", str(interval)]
    return argv


def run_loop(session: str, *, remote: Optional[str] = None,
             root: Optional[str] = None, interval: int = 5,
             out_path: Optional[str] = None,
             keep_lines: int = 5000) -> int:
    """Run the loop until interrupted.

    Writes the NDJSON event stream from ``dcam tmux watch`` to
    ``out_path`` (default: ``~/.dcam/manager-loop-<session>.ndjson``).
    The file is truncated to ``keep_lines`` from the back periodically
    so it doesn't grow without bound.

    Returns the watch process's exit code (0 on Ctrl-C).
    """
    if out_path is None:
        default_root = Path.home() / ".dcam"
        default_root.mkdir(parents=True, exist_ok=True)
        out_path = str(default_root / f"manager-loop-{session}.ndjson")
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # Truncate on start so the manager doesn't see stale events from a
    # previous run. The user can `tail -f` from this point.
    out_file.write_text("")

    argv = _build_watch_argv(remote, session, root, interval)
    print(f"[manager-loop] spawning: {' '.join(argv)}", file=sys.stderr)
    print(f"[manager-loop] writing  : {out_file}", file=sys.stderr)
    print(f"[manager-loop] foreground manager reads with: "
          f"`tail -n 50 {out_file}` at each user turn", file=sys.stderr)

    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError as e:
        print(f"[manager-loop] failed to spawn watch: {e}", file=sys.stderr)
        return 1

    last_trim = time.monotonic()
    try:
        with open(out_file, "a") as fh:
            assert proc.stdout is not None
            for line in proc.stdout:
                fh.write(line)
                fh.flush()
                # Trim every ~5 minutes so we don't grow without bound.
                now = time.monotonic()
                if now - last_trim > 300:
                    _trim_tail(out_file, keep_lines)
                    last_trim = now
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0
    rc = proc.wait()
    if rc and proc.stderr:
        err_tail = proc.stderr.read()[:500]
        print(f"[manager-loop] watch exited {rc}: {err_tail}", file=sys.stderr)
    return rc


def _trim_tail(path: Path, keep_lines: int):
    """Keep only the last N lines of the loop file.

    Cheap and accurate enough: read all lines, write back the tail.
    The loop file is bounded by event volume, not by time.
    """
    try:
        lines = path.read_text().splitlines(keepends=True)
        if len(lines) <= keep_lines:
            return
        path.write_text("".join(lines[-keep_lines:]))
    except OSError:
        pass
