"""Terminal control panel for DCAM (`dcam tmux feed`).

A curses-based dashboard that subprocess-spawns ``dcam tmux watch`` and
reads its NDJSON event stream. Three panes:

- **Agent tree (top-left)**: per-slug rows showing the latest `[status]`
  comment, owning bd task, and which review-requests reference the slug.
- **Key context (top-right)**: open decisions (with severity), active
  critical points, registered specs, and pending handoffs.
- **Event log (bottom)**: rolling stream of state-change events from
  `dcam tmux watch`, newest at top.

Blockers are highlighted in reverse video. Q quits, R forces a redraw,
J/K scrolls the event log.

Pipes-from-watch design (rather than direct in-process polling) so that
the same TUI can drive the hybrid local-manager + remote-workers
pattern: the local Mac runs ``dcam tmux feed --remote dev-desk:<session>``
which under the hood spawns ``ssh dev-desk dcam ... tmux watch ...`` and
pipes its stdout to this same renderer.
"""

import json
import os
import shlex
import shutil
import subprocess
import threading
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional


# Sentinels for sentinel-row formatting.
_HEADER_ATTR = 1   # bold heading
_BLOCKER_ATTR = 2  # reverse video
_DIM_ATTR = 3      # dim text


def _build_watch_argv(remote: Optional[str], session: str,
                      root: Optional[str], interval: int) -> List[str]:
    """Construct the argv for the underlying ``dcam tmux watch`` call.

    With ``remote=host:session_name`` the call is wrapped in ``ssh``; the
    session_name in remote takes precedence so the user can write the
    short form ``dcam tmux feed --remote dev-desk:andes-relay``.
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


class _Reader(threading.Thread):
    """Background thread that pumps watch's stdout into a thread-safe state.

    The TUI's main loop reads from `state` (a dict snapshot) and `events`
    (a deque) without doing any IO of its own. This keeps redraws snappy
    even when the SSH link or local watch process is slow.
    """

    def __init__(self, argv: List[str]):
        super().__init__(daemon=True)
        self.argv = argv
        self.state: Dict[str, Dict] = {}
        self.events: deque = deque(maxlen=200)
        self.lock = threading.Lock()
        self.stopped = False
        self.error: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None

    def run(self):
        try:
            self._proc = subprocess.Popen(
                self.argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except FileNotFoundError as e:
            with self.lock:
                self.error = f"failed to spawn watch: {e}"
            return
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self.stopped:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.lock:
                if event.get("kind") == "snapshot":
                    self.state = event.get("state", {})
                else:
                    self._apply_delta(event)
                    self.events.appendleft(event)
        rc = self._proc.wait()
        if rc and not self.stopped:
            err_tail = ""
            if self._proc.stderr:
                err_tail = self._proc.stderr.read()[:200]
            with self.lock:
                self.error = f"watch exited {rc}: {err_tail}"

    def _apply_delta(self, event: Dict):
        """Apply an `<entity>_<change>` delta to in-memory state."""
        kind = event.get("kind", "")
        if "_" not in kind:
            return
        entity, change = kind.rsplit("_", 1)
        bucket = self.state.setdefault(entity, {})
        eid = event.get("id")
        if not eid:
            return
        if change in ("added", "changed"):
            bucket[eid] = event.get("data", {})
        elif change == "removed":
            bucket.pop(eid, None)

    def stop(self):
        self.stopped = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


# --- Rendering -------------------------------------------------------------


def _is_blocker(entry: dict) -> bool:
    return (entry or {}).get("severity") == "blocker"


def _agent_rows(state: Dict[str, Dict]) -> List[Dict]:
    """One row per known dev slug, with derived per-slug signal."""
    by_slug: Dict[str, Dict] = {}

    # bd-task → slug pairing
    for tid, t in state.get("dev_tasks", {}).items():
        slug = t.get("slug") or "(unscoped)"
        row = by_slug.setdefault(slug, {"slug": slug, "task_id": tid})
        row["title"] = t.get("title", "")
        row["status"] = t.get("latest_status", "")

    for rid, r in state.get("review_requests", {}).items():
        slug = r.get("slug") or "(unscoped)"
        row = by_slug.setdefault(slug, {"slug": slug})
        row.setdefault("requests", []).append({
            "id": rid, "status": r.get("status"),
            "severity": r.get("severity"),
            "notes": (r.get("notes") or "")[:60],
        })

    return sorted(by_slug.values(), key=lambda r: r["slug"])


def _draw_agent_pane(stdscr, win, state, attrs):
    win.erase()
    win.box()
    win.addnstr(0, 2, " Agents ", win.getmaxyx()[1] - 4, attrs["header"])
    rows = _agent_rows(state)
    if not rows:
        win.addnstr(2, 2, "no dev tasks yet", win.getmaxyx()[1] - 4,
                    attrs["dim"])
        win.refresh()
        return
    y = 1
    h, w = win.getmaxyx()
    for row in rows:
        if y >= h - 1:
            break
        slug = row["slug"]
        status = row.get("status") or ""
        title = row.get("title") or ""
        line = f"  {slug:<20}  {title[:30]:<30}"
        win.addnstr(y, 1, line, w - 2, attrs["normal"])
        y += 1
        if status and y < h - 1:
            win.addnstr(y, 4, f"→ {status[:w - 8]}", w - 6, attrs["dim"])
            y += 1
        for req in row.get("requests", []):
            if y >= h - 1:
                break
            attr = attrs["blocker"] if _is_blocker(req) else attrs["normal"]
            tag = "BLOCKER " if _is_blocker(req) else ""
            req_line = (f"    {tag}REQ-{req['id'][4:]:<4} {req.get('status') or ''}  "
                        f"{req['notes']}")
            win.addnstr(y, 1, req_line[:w - 2], w - 2, attr)
            y += 1
        if y < h - 1:
            y += 1  # spacer
    win.refresh()


def _draw_context_pane(stdscr, win, state, attrs):
    win.erase()
    win.box()
    win.addnstr(0, 2, " Key context ", win.getmaxyx()[1] - 4, attrs["header"])
    h, w = win.getmaxyx()
    y = 1

    def write(line, attr=None):
        nonlocal y
        if y >= h - 1:
            return
        win.addnstr(y, 1, line[:w - 2], w - 2, attr or attrs["normal"])
        y += 1

    write("Open decisions:", attrs["header"])
    decs = state.get("decisions", {})
    open_decs = {k: v for k, v in decs.items()
                 if (v or {}).get("status") == "open"}
    if not open_decs:
        write("  (none)", attrs["dim"])
    for did, d in sorted(open_decs.items()):
        attr = attrs["blocker"] if _is_blocker(d) else attrs["normal"]
        tag = "BLOCKER " if _is_blocker(d) else ""
        scope = " · ".join(filter(None, [d.get("epic"), d.get("op")])) or "—"
        write(f"  {tag}{did}  [{scope}]  {(d.get('title') or '')[:40]}",
              attr)

    write("")
    write("Active critical points:", attrs["header"])
    cps = state.get("critical_points", {})
    if not cps:
        write("  (none)", attrs["dim"])
    for cid, cp in sorted(cps.items()):
        scope = " · ".join(filter(None, [cp.get("epic"), cp.get("op")])) or "—"
        write(f"  {cid}  [{scope}]  {(cp.get('content') or '')[:40]}")

    write("")
    write("Pending handoffs:", attrs["header"])
    hs = state.get("handoffs", {})
    pending = {k: v for k, v in hs.items()
               if (v or {}).get("status") == "pending"}
    if not pending:
        write("  (none)", attrs["dim"])
    for hid, h_ in sorted(pending.items()):
        write(f"  {hid}  {h_.get('from')} → {h_.get('to')}: "
              f"{(h_.get('notes') or '')[:30]}")

    write("")
    write("Specs:", attrs["header"])
    specs = state.get("specs", {})
    if not specs:
        write("  (none)", attrs["dim"])
    for sid, s in sorted(specs.items()):
        scope = " · ".join(filter(None, [s.get("epic"), s.get("op")])) or "—"
        link = (f" → DEC-{s.get('linked_decision')}"
                if s.get("linked_decision") else "")
        write(f"  {sid}  [{scope}]  {(s.get('path') or '')[:30]}{link}")

    win.refresh()


def _draw_event_pane(stdscr, win, events, scroll_offset, attrs):
    win.erase()
    win.box()
    win.addnstr(0, 2, " Events (j/k to scroll, q to quit) ",
                win.getmaxyx()[1] - 4, attrs["header"])
    h, w = win.getmaxyx()
    visible = list(events)[scroll_offset: scroll_offset + (h - 2)]
    y = 1
    for ev in visible:
        if y >= h - 1:
            break
        ts = ev.get("ts", "")[11:19] or "        "
        kind = ev.get("kind", "?")
        eid = ev.get("id", "")
        data = ev.get("data") or {}
        attr = attrs["blocker"] if _is_blocker(data) else attrs["normal"]
        # Compact one-line render — full data in NDJSON if user wants it.
        summary = data.get("notes") or data.get("title") or data.get("content") or ""
        line = f"  {ts}  {kind:<28}  {eid:<8}  {summary[:w - 50]}"
        win.addnstr(y, 1, line[:w - 2], w - 2, attr)
        y += 1
    win.refresh()


def _draw_status_bar(stdscr, reader, attrs):
    h, w = stdscr.getmaxyx()
    bar = " dcam tmux feed "
    if reader.error:
        bar += f" ⚠ {reader.error[:w - 30]}"
    else:
        bar += f" • {len(reader.events)} events buffered"
    stdscr.addnstr(h - 1, 0, bar.ljust(w), w, attrs["header"])
    stdscr.refresh()


def run_feed(session: str, *, remote: Optional[str] = None,
             root: Optional[str] = None, interval: int = 5):
    """Entry point. Spawns watch in a background thread, runs the curses
    main loop. Returns when the user hits q (or the watch process dies)."""
    import curses
    if not shutil.which("dcam"):
        raise RuntimeError("dcam CLI not found on PATH")
    argv = _build_watch_argv(remote, session, root, interval)
    reader = _Reader(argv)
    reader.start()

    def _curses_main(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(500)  # half-second redraw cadence
        curses.start_color()
        # 1=header, 2=blocker, 3=dim
        try:
            curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK)
        except curses.error:
            pass
        attrs = {
            "header": curses.A_BOLD | curses.color_pair(1),
            "blocker": curses.A_BOLD | curses.A_REVERSE | curses.color_pair(2),
            "dim": curses.A_DIM | curses.color_pair(3),
            "normal": curses.A_NORMAL,
        }

        scroll_offset = 0
        while True:
            try:
                key = stdscr.getch()
            except KeyboardInterrupt:
                break
            if key in (ord("q"), ord("Q")):
                break
            if key in (ord("j"),):
                scroll_offset += 1
            elif key in (ord("k"),):
                scroll_offset = max(0, scroll_offset - 1)
            elif key in (ord("g"),):
                scroll_offset = 0
            elif key in (ord("r"), ord("R")):
                stdscr.clear()
                stdscr.refresh()

            with reader.lock:
                state_copy = dict(reader.state)
                events_copy = list(reader.events)

            scroll_offset = min(scroll_offset, max(0, len(events_copy) - 1))

            h, w = stdscr.getmaxyx()
            top_h = max(8, h // 2)
            bot_h = h - top_h - 1
            mid = w // 2

            agent_win = curses.newwin(top_h, mid, 0, 0)
            ctx_win = curses.newwin(top_h, w - mid, 0, mid)
            evt_win = curses.newwin(bot_h, w, top_h, 0)

            _draw_agent_pane(stdscr, agent_win, state_copy, attrs)
            _draw_context_pane(stdscr, ctx_win, state_copy, attrs)
            _draw_event_pane(stdscr, evt_win, events_copy, scroll_offset, attrs)
            _draw_status_bar(stdscr, reader, attrs)

            if reader.error:
                # Watch process died; let the user read the error then quit on key.
                pass

    try:
        import curses
        curses.wrapper(_curses_main)
    finally:
        reader.stop()
        reader.join(timeout=2)
