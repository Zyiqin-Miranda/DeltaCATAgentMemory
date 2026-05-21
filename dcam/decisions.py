"""Decision tracking and persistence to CLAUDE.md / AGENTS.md.

Workflow:
    1. A dev hits a decision point and calls `request_decision(...)`. The
       row goes in `decisions` with status=open. A bd comment fires on the
       dev's task; the manager sees it via `dcam tmux decisions list`.
    2. Manager calls `decide(...)`. Status flips to `decided`, rationale +
       chosen option are recorded, dev's bd task gets a follow-up comment.
    3. If a decision needs to change, manager calls `decide(..., supersedes=N)`
       which marks N as `superseded` and creates a new row pointing back at
       it via `supersedes_id`. The chain is queryable via
       `store.get_decision_chain(id)`.

Persistence:
    Decisions and lessons are mirrored into managed sections of
    CLAUDE.md / AGENTS.md, delimited by HTML comments. The parquet table
    stays the source of truth; the markdown is a regenerated view.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from dcam.models import Decision, DecisionStatus, Lesson
from dcam.store import DeltaStore

# --- Section markers --------------------------------------------------------

DEC_START = "<!-- DCAM:DECISIONS:START -->"
DEC_END = "<!-- DCAM:DECISIONS:END -->"
LES_START = "<!-- DCAM:LESSONS:START -->"
LES_END = "<!-- DCAM:LESSONS:END -->"


# --- Helpers ----------------------------------------------------------------


def _parse_options(raw: Optional[str]):
    """Accept either `A:summary|B:summary` shorthand or JSON list."""
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("["):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return []
    out = []
    for chunk in s.split("|"):
        if ":" in chunk:
            key, summary = chunk.split(":", 1)
            out.append({"key": key.strip(), "summary": summary.strip()})
        else:
            out.append({"key": chunk.strip(), "summary": ""})
    return out


def _bd_comment_safe(task_id: Optional[str], text: str):
    """Best-effort bd comment — silent if bd or task is missing."""
    if not task_id:
        return
    try:
        from dcam.orchestrator import comment_task
        comment_task(task_id, text)
    except Exception:
        pass


# --- Core operations --------------------------------------------------------


def request_decision(store: DeltaStore, *, slug: str, title: str,
                     context: str, options: str,
                     recommended: Optional[str] = None,
                     task_id: Optional[str] = None,
                     session_id: Optional[str] = None) -> Decision:
    """A dev requests a manager decision. Non-blocking — dev keeps working."""
    d = Decision(
        title=title.strip(),
        context=context.strip(),
        options=json.dumps(_parse_options(options)),
        recommended=recommended,
        status=DecisionStatus.OPEN,
        requested_by=slug,
        task_id=task_id,
        session_id=session_id,
    )
    d = store.append_decision(d)
    _bd_comment_safe(task_id, f"[ask:{d.id}] {title}: requesting manager input "
                              f"(recommended={recommended or '?'})")
    return d


def decide(store: DeltaStore, *, decision_id: Optional[int] = None,
           supersedes: Optional[int] = None,
           chosen: str, rationale: str,
           decided_by: str = "manager",
           persist_target: Optional[str] = None,
           project_path: Optional[str] = None) -> Decision:
    """Resolve an open decision, or create a new revision that supersedes one.

    If `decision_id` is given and `supersedes` is None, that decision is
    marked decided.
    If `supersedes` is given (id of an older decision), the older one is
    marked `superseded` and a new decided row is created pointing back at it.
    """
    now = datetime.now()
    all_ds = store.read_decisions()
    by_id = {d.id: d for d in all_ds}

    if supersedes is not None:
        old = by_id.get(supersedes)
        if not old:
            raise ValueError(f"No decision id={supersedes}")
        old.status = DecisionStatus.SUPERSEDED
        old.updated_at = now
        store.update_decision(old)
        new_d = Decision(
            title=old.title,
            context=old.context,
            options=old.options,
            recommended=old.recommended,
            chosen=chosen,
            rationale=rationale,
            status=DecisionStatus.DECIDED,
            supersedes_id=old.id,
            requested_by=old.requested_by,
            decided_by=decided_by,
            task_id=old.task_id,
            session_id=old.session_id,
            decided_at=now,
        )
        new_d = store.append_decision(new_d)
        _bd_comment_safe(old.task_id, f"[decide:{new_d.id}] supersedes #{old.id}: "
                                      f"chose {chosen}. {rationale[:200]}")
        result = new_d
    else:
        if decision_id is None:
            raise ValueError("Either decision_id or supersedes must be set")
        d = by_id.get(decision_id)
        if not d:
            raise ValueError(f"No decision id={decision_id}")
        d.chosen = chosen
        d.rationale = rationale
        d.status = DecisionStatus.DECIDED
        d.decided_by = decided_by
        d.decided_at = now
        d.updated_at = now
        store.update_decision(d)
        _bd_comment_safe(d.task_id, f"[decide:{d.id}] chose {chosen}. {rationale[:200]}")
        result = d

    if persist_target and project_path:
        result.persist_target = persist_target
        store.update_decision(result)
        persist_to_project(store, project_path, persist_target)

    return result


def withdraw_decision(store: DeltaStore, decision_id: int, reason: str = ""):
    all_ds = store.read_decisions()
    for d in all_ds:
        if d.id == decision_id:
            d.status = DecisionStatus.WITHDRAWN
            d.rationale = (d.rationale or "") + f"\nWithdrawn: {reason}"
            d.updated_at = datetime.now()
            store.update_decision(d)
            _bd_comment_safe(d.task_id, f"[withdraw:{d.id}] {reason[:200]}")
            return d
    raise ValueError(f"No decision id={decision_id}")


def add_lesson(store: DeltaStore, content: str,
               category: Optional[str] = None,
               source_slug: Optional[str] = None,
               session_id: Optional[str] = None,
               persist_target: Optional[str] = None,
               project_path: Optional[str] = None) -> Lesson:
    l = Lesson(
        content=content.strip(), category=category,
        source_slug=source_slug, session_id=session_id,
        persist_target=persist_target,
    )
    l = store.append_lesson(l)
    if persist_target and project_path:
        persist_to_project(store, project_path, persist_target)
    return l


# --- Rendering --------------------------------------------------------------


def render_decisions_section(decisions: List[Decision]) -> str:
    """Render decided + superseded decisions as markdown.

    Open decisions are not persisted — they're transient state. The audit
    trail (chosen/rationale/supersedes) lives only on resolved rows.
    """
    persistable = [d for d in decisions
                   if d.status in (DecisionStatus.DECIDED, DecisionStatus.SUPERSEDED)]
    if not persistable:
        return "## Decisions\n\n_No decisions recorded yet._\n"

    persistable.sort(key=lambda d: d.id or 0)
    lines = ["## Decisions", ""]
    for d in persistable:
        date_str = (d.decided_at or d.updated_at).strftime("%Y-%m-%d")
        marker = "[superseded]" if d.status == DecisionStatus.SUPERSEDED else "[decided]"
        lines.append(f"### [DEC-{d.id}] {d.title}  ·  {date_str}  ·  {marker}")
        if d.context:
            lines.append("")
            lines.append(f"**Context.** {d.context}")
        if d.options:
            try:
                opts = json.loads(d.options)
                if opts:
                    lines.append("")
                    lines.append("**Options considered:**")
                    for o in opts:
                        rec = " (recommended)" if o.get("key") == d.recommended else ""
                        sumr = f" — {o.get('summary')}" if o.get("summary") else ""
                        lines.append(f"- `{o.get('key')}`{rec}{sumr}")
            except json.JSONDecodeError:
                pass
        if d.chosen:
            lines.append("")
            lines.append(f"**Chosen.** `{d.chosen}` (by {d.decided_by or 'unknown'})")
        if d.rationale:
            lines.append("")
            lines.append(f"**Rationale.** {d.rationale}")
        if d.supersedes_id:
            lines.append("")
            lines.append(f"**Supersedes.** [DEC-{d.supersedes_id}]")
        if d.requested_by:
            lines.append("")
            lines.append(f"_Requested by_ `{d.requested_by}`")
        lines.append("")
    return "\n".join(lines)


def render_lessons_section(lessons: List[Lesson]) -> str:
    if not lessons:
        return "## Lessons learnt\n\n_No lessons recorded yet._\n"
    lessons = sorted(lessons, key=lambda l: l.id or 0)
    lines = ["## Lessons learnt", ""]
    by_cat: dict = {}
    for l in lessons:
        by_cat.setdefault(l.category or "general", []).append(l)
    for cat in sorted(by_cat):
        lines.append(f"### {cat}")
        lines.append("")
        for l in by_cat[cat]:
            date = l.created_at.strftime("%Y-%m-%d")
            tail = f" _(via {l.source_slug}, {date})_" if l.source_slug else f" _({date})_"
            lines.append(f"- {l.content}{tail}")
        lines.append("")
    return "\n".join(lines)


# --- Persistence to CLAUDE.md / AGENTS.md ----------------------------------


def _replace_section(text: str, start: str, end: str, new_body: str) -> str:
    """Replace the body between start/end markers, inserting markers if absent."""
    block = f"{start}\n{new_body.rstrip()}\n{end}"
    pattern = re.escape(start) + r".*?" + re.escape(end)
    if re.search(pattern, text, flags=re.DOTALL):
        return re.sub(pattern, block, text, count=1, flags=re.DOTALL)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block + "\n"


def _persist_to_file(path: Path, decisions_md: str, lessons_md: str):
    text = path.read_text() if path.exists() else ""
    text = _replace_section(text, DEC_START, DEC_END, decisions_md)
    text = _replace_section(text, LES_START, LES_END, lessons_md)
    path.write_text(text)


def discover_persist_targets(project_path: str) -> List[Path]:
    """Find files in the project root that already have DCAM markers.

    Used by `persist_to_project(target="auto")` and the pre-commit hook to
    respect the user's earlier opt-in: a file is updated only if it
    already contains the START/END marker pair from a prior persist.
    """
    root = Path(project_path)
    candidates = [root / "CLAUDE.md", root / "AGENTS.md"]
    found: List[Path] = []
    for p in candidates:
        if not p.exists():
            continue
        text = p.read_text(errors="replace")
        if DEC_START in text and DEC_END in text:
            found.append(p)
        elif LES_START in text and LES_END in text:
            found.append(p)
    return found


def persist_to_project(store: DeltaStore, project_path: str,
                       target: str = "claude") -> List[Path]:
    """Render decisions + lessons into managed sections of CLAUDE.md/AGENTS.md.

    `target` is one of "claude", "agents", "both", or "auto". With "auto",
    we only update files that already have DCAM markers, which is what the
    pre-commit hook uses to respect the operator's earlier opt-in choice.
    Returns the list of paths written (empty if "auto" finds no opted-in
    files).
    """
    decisions = store.read_decisions()
    lessons = store.read_lessons()
    decisions_md = render_decisions_section(decisions)
    lessons_md = render_lessons_section(lessons)

    targets: List[Path] = []
    root = Path(project_path)
    if target == "auto":
        targets = discover_persist_targets(project_path)
    else:
        if target in ("claude", "both"):
            targets.append(root / "CLAUDE.md")
        if target in ("agents", "both"):
            targets.append(root / "AGENTS.md")

    written: List[Path] = []
    now = datetime.now()
    for p in targets:
        _persist_to_file(p, decisions_md, lessons_md)
        written.append(p)

    # Mark decisions/lessons as persisted
    for d in decisions:
        if d.status in (DecisionStatus.DECIDED, DecisionStatus.SUPERSEDED):
            d.persist_target = target
            d.persisted_at = now
            store.update_decision(d)
    for l in lessons:
        l.persist_target = target
        l.persisted_at = now
        # update_lesson would be symmetrical; we just rewrite the table:
    if lessons:
        store.write_lessons(lessons)

    return written
