"""Multi-agent coordination via tmux.

Lays out a tmux session with three roles:
    - manager  : high-level decisions, dispatches work
    - review   : on-demand code/business-logic review
    - dev-<slug>: workers; one per concurrent task

Window naming convention: `manager`, `review`, `dev-<slug>`. The `dev-`
prefix lets the manager scan with `tmux list-windows | grep '^dev-'` and
makes status visible in tmux's status bar at a glance.

Coordination channels:
    - DCAM (async): every agent's SessionEnd hook syncs the transcript.
      Manager calls `dcam claude context` to see structured summaries.
    - Beads (milestone): each dev task has a beads issue tagged
      `role:dev` and `slug:<slug>`. Devs post `[status]` comments on
      milestones; reviewer posts `[review]` comments with findings.
    - tmux send-keys (live): manager can interject directly with
      `dcam tmux send <slug> "..."`.
"""

import re
import shlex
import shutil
import subprocess
from typing import List, Optional


# --- Role prompts -----------------------------------------------------------

MANAGER_PROMPT = """You are the MANAGER agent in a multi-agent team coordinated via tmux + DCAM.

Your responsibilities:
- Hold the high-level project context and make architectural decisions.
- Decompose work into tasks (use `dcam task create`) labeled `role:dev`.
- Spawn dev workers with `dcam tmux dev <slug> "<brief>"` for hands-on work.
- Periodically call `dcam claude context` for a structured view of every dev's
  recent activity (files touched, commands run, last prompt) and `bd list
  --label role:dev` for the queue state.
- Triage decision requests:
    * `dcam tmux decisions list --status open` — see what devs are waiting on.
    * `dcam tmux decisions show <id>` — full context + options + chain history.
    * `dcam tmux decide --id <N> --choice <K> --rationale "..." --persist claude`
      to resolve. Use `--supersedes <old-id>` instead of `--id` when revising
      a prior decision; the old one is marked superseded automatically.
- Persist durable decisions and lessons into the project's CLAUDE.md (and/or
  AGENTS.md). The managed sections are auto-regenerated; do not edit them by
  hand. Use `dcam tmux persist --target claude` to refresh.
- Trigger code review with `dcam tmux review` when a dev's task is in flight
  or done. Read the reviewer's `[review]` comments via `bd show <task-id>`.
- Use `dcam tmux send <session> <window> "<message>"` only for time-critical
  interjections (corrections, scope changes). Prefer decisions and bd comments.

You do NOT write production code yourself. You delegate.
"""

DEV_PROMPT_TEMPLATE = """You are a DEV agent working on task slug `{slug}`.

Task brief:
{brief}

Your responsibilities:
- Implement the task. Write code, run tests, commit when ready.
- Post milestone updates with `dcam tmux update {slug} "<one-line status>"`
  on real progress (started, blocked, ready-for-review, done).
- Sync to DCAM happens automatically at SessionEnd; the manager can read
  your transcript anytime.

When you need a manager decision (architectural choice, scope question,
ambiguous requirement):
- DO NOT silently change scope. Use:
  `dcam tmux ask {slug} "<title>" --context "<background>" \\
       --options "A:<summary>|B:<summary>" --recommend A`
- The decision is non-blocking — keep working with your best guess if you
  can. Re-check `dcam tmux decisions list --status decided` periodically;
  the manager's choice will land there with rationale.
- If the decision contradicts your in-flight work, course-correct.

When your work depends on or affects another dev:
- `dcam tmux dep <their-slug> {slug}` — declare you're blocked by them.
- `dcam tmux msg {slug} <their-slug> "<text>"` — message them directly
  (delivered live via tmux + persisted as a bd comment on their task).
- `dcam tmux deps {slug}` — see open dev tasks at a glance.

When you discover something worth remembering across sessions:
- `dcam tmux lesson "<one-line learning>" --category design|testing|ops`
  records a lesson; manager will persist it to CLAUDE.md/AGENTS.md.

The reviewer (when invoked) will read your DCAM transcript and post
`[review]` comments on your beads task. Address those before closing.
"""

REVIEWER_PROMPT = """You are the REVIEWER agent. You run on-demand and exit when done.

Your job:
1. Read the project's critical points first — these are forward-looking
   invariants that every review must enforce:
       dcam tmux critical list --status active
   The active set also lives in the `## Critical key points` section of
   CLAUDE.md / AGENTS.md.
2. Run `bd list --label role:dev --status open` to find active dev tasks.
3. For each dev task with recent activity:
   - Identify scope via the task labels (slug, plus any `epic:<x>` /
     `op:<y>` labels if present) so you know which critical points apply.
   - Read its DCAM session: `dcam claude context --sessions 1` (focus on the
     dev's session) or `dcam claude recall <session-id>`.
   - Inspect any code changes the dev produced (use git diff, file reads).
   - Check `dcam tmux decisions list` and recent bd comments for context on
     why the dev made specific choices.
4. For each task, post a `[review]` comment via `bd comment <task-id>` with:
   - What looks correct.
   - Concrete issues (cite file:line where possible).
   - Whether the business logic matches the task brief and any relevant
     decisions in `dcam tmux decisions show <id>`.
   - **Any critical-point violations**: cite `CP-<id>` and the offending
     code or behavior. Treat these as blocking.
5. If review surfaces a generalizable insight, record it:
   `dcam tmux lesson "<text>" --category design|testing|ops --epic <slug>`.
   The manager will fold it into CLAUDE.md/AGENTS.md.
6. If you discover a forward-looking invariant the team should enforce
   (not just a one-time fix), record it with:
   `dcam tmux critical add "<text>" --rationale "..." --epic <slug>`.
7. Do NOT modify code yourself unless explicitly asked. You comment, the dev
   acts.

When you're done with all active tasks, summarize your findings and exit.
"""


# --- tmux primitives --------------------------------------------------------


def _tmux_available() -> bool:
    """True if the `tmux` binary is on PATH."""
    return shutil.which("tmux") is not None


def _run(args: List[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run a tmux command, returning the completed process."""
    return subprocess.run(["tmux"] + args, capture_output=True, text=True,
                          check=check)


def _require_tmux():
    if not _tmux_available():
        raise RuntimeError(
            "tmux is not installed. Install with `brew install tmux` or your "
            "package manager."
        )


def session_exists(name: str) -> bool:
    """True if a tmux session with the given name is running."""
    if not _tmux_available():
        return False
    return _run(["has-session", "-t", name]).returncode == 0


def list_windows(session: str) -> List[str]:
    """Return all window names for the session."""
    if not session_exists(session):
        return []
    res = _run(["list-windows", "-t", session, "-F", "#{window_name}"])
    return [line for line in res.stdout.splitlines() if line]


def slugify(text: str) -> str:
    """Convert free text to a tmux-safe slug.

    tmux window names should avoid `:`, `.`, and whitespace because the
    target syntax `<session>:<window>` parses on those characters.
    """
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40] or "task"


# --- Session lifecycle ------------------------------------------------------


def start_session(name: str, project_path: str,
                  manager_cmd: Optional[str] = None) -> str:
    """Create a tmux session with a `manager` window and start the manager
    agent there.

    Returns the session name. Idempotent: if the session already exists,
    returns it unchanged.
    """
    _require_tmux()
    if session_exists(name):
        return name

    # New detached session with the manager window
    _run(["new-session", "-d", "-s", name, "-n", "manager", "-c", project_path],
         check=True)

    if manager_cmd:
        _run(["send-keys", "-t", f"{name}:manager", manager_cmd, "Enter"])

    return name


def spawn_dev_window(session: str, slug: str, project_path: str,
                     dev_cmd: Optional[str] = None) -> str:
    """Create a `dev-<slug>` window and optionally launch a command in it.

    Returns the full tmux window target (e.g. `mysession:dev-auth-flow`).
    """
    _require_tmux()
    if not session_exists(session):
        raise RuntimeError(f"tmux session '{session}' does not exist. Run "
                           f"`dcam tmux start` first.")

    window_name = f"dev-{slug}"
    target = f"{session}:{window_name}"

    if window_name not in list_windows(session):
        _run(["new-window", "-t", session, "-n", window_name, "-c",
              project_path], check=True)

    if dev_cmd:
        _run(["send-keys", "-t", target, dev_cmd, "Enter"])

    return target


def spawn_review_window(session: str, project_path: str,
                        review_cmd: Optional[str] = None) -> str:
    """Create the `review` window if missing and start the reviewer."""
    _require_tmux()
    if not session_exists(session):
        raise RuntimeError(f"tmux session '{session}' does not exist.")

    target = f"{session}:review"
    if "review" not in list_windows(session):
        _run(["new-window", "-t", session, "-n", "review", "-c", project_path],
             check=True)
    if review_cmd:
        _run(["send-keys", "-t", target, review_cmd, "Enter"])
    return target


# --- Communication primitives ----------------------------------------------


def send_keys(session: str, window: str, text: str, press_enter: bool = True):
    """Send text to a window's active pane.

    `window` accepts either a slug (`auth-flow` -> `dev-auth-flow`) or a
    full window name (`manager`, `review`, `dev-foo`).
    """
    _require_tmux()
    if not (window.startswith("dev-") or window in ("manager", "review")):
        window = f"dev-{window}"
    target = f"{session}:{window}"
    args = ["send-keys", "-t", target, text]
    if press_enter:
        args.append("Enter")
    _run(args, check=True)


def capture_pane(session: str, window: str, tail: int = 200) -> str:
    """Capture the visible buffer of a window's active pane."""
    _require_tmux()
    if not (window.startswith("dev-") or window in ("manager", "review")):
        window = f"dev-{window}"
    target = f"{session}:{window}"
    res = _run(["capture-pane", "-t", target, "-p", "-S", f"-{tail}"])
    return res.stdout


# --- Status / dispatch helpers ---------------------------------------------


def list_dev_windows(session: str) -> List[str]:
    """Return slugs of all `dev-*` windows in the session."""
    return [w[4:] for w in list_windows(session) if w.startswith("dev-")]


def build_dev_launch_cmd(slug: str, brief: str, claude_bin: str = "claude") -> str:
    """Build the shell command that a dev window runs to start its agent.

    The agent is launched non-interactively with the dev role prompt as
    `--append-system-prompt`, then keeps the shell open for follow-up.
    """
    prompt = DEV_PROMPT_TEMPLATE.format(slug=slug, brief=brief)
    return f"{claude_bin} --append-system-prompt {shlex.quote(prompt)}"


def build_manager_launch_cmd(claude_bin: str = "claude") -> str:
    return f"{claude_bin} --append-system-prompt {shlex.quote(MANAGER_PROMPT)}"


def build_reviewer_launch_cmd(claude_bin: str = "claude") -> str:
    return f"{claude_bin} --append-system-prompt {shlex.quote(REVIEWER_PROMPT)}"
