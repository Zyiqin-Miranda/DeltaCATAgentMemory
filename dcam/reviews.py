"""Pull-based review workflow for the long-running reviewer agent.

The reviewer runs in a persistent tmux window (`dcam tmux review --launch`).
Devs `dcam tmux request-review` when they want feedback; DCAM forwards a
notification to the reviewer's pane via tmux send-keys, plus records the
request as a row in `review_requests.json`. The reviewer drains the queue
with `dcam tmux reviews pending`, claims one, does the work using its
full set of tools (search, scripts, git diff, tests, …), and writes a
`Review` record on completion.

The reviewer's accumulated learning lives in two places:

- Lessons tagged `category=review-finding` — surfaced into the next
  reviewer-session's startup prompt.
- Critical points — surfaced into the rendered `## Critical key points`
  section that every reviewer reads on each run.

This module is the data plumbing. The reviewer's actual brains live in
the agent itself (its prompt + tools).
"""

import subprocess
from datetime import datetime
from typing import List, Optional

from dcam.models import (
    Handoff, HandoffStatus, Review, ReviewRequest, ReviewRequestStatus, Spec,
)
from dcam.store import DeltaStore


# --- Notification (live tmux send-keys) ------------------------------------


def _notify_reviewer_window(session: Optional[str], message: str):
    """Best-effort live notification to the `review` window via tmux.

    Silent if tmux isn't running, the session doesn't exist, or the
    review window hasn't been spawned. The persistent review_request
    row is the durable channel — this is just the live ping.

    Sends two lines:
    1. The notification itself, NOT comment-prefixed, so the reviewer
       Claude treats it as a real prompt and acts on it. (Pre-2026-05-28
       we sent ``# <msg>`` which Claude treated as inert ambient text;
       requests sat unprocessed for tens of minutes.)
    2. An explicit "drain pending" instruction so the agent knows what
       command to run.
    """
    if not session:
        return
    try:
        from dcam import tmux as tmux_mod
        if not tmux_mod.session_exists(session):
            return
        if "review" not in tmux_mod.list_windows(session):
            return
        # Two-line prompt. The first line is the human-readable
        # notification; the second is the explicit instruction the agent
        # acts on. send_keys appends Enter by default, which submits.
        tmux_mod.send_keys(session, "review",
                           f"[review-request] {message}")
        tmux_mod.send_keys(
            session, "review",
            "Drain pending review requests now: "
            "`dcam tmux reviews pending`, then `reviews show <id>`, "
            "`reviews claim <id>`, do the review, and "
            "`reviews complete <id> --summary \"...\" --persist claude`.",
        )
    except Exception:
        # Any failure here should never break the request.
        pass


# --- Git helpers -----------------------------------------------------------


def _git_head(project_path: str) -> Optional[str]:
    """Return the short SHA of HEAD, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_path, capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# --- ReviewRequest lifecycle ----------------------------------------------


def request_review(store: DeltaStore, *, slug: str,
                   notes: str = "",
                   scope_files: str = "",
                   epic: Optional[str] = None,
                   op: Optional[str] = None,
                   ticket: Optional[str] = None,
                   related_decision_ids: str = "",
                   session_id: Optional[str] = None,
                   project_path: Optional[str] = None,
                   tmux_session: Optional[str] = None) -> ReviewRequest:
    """Dev requests a review. Persistent JSON row + live tmux notification."""
    req = ReviewRequest(
        slug=slug, notes=notes.strip(),
        scope_files=scope_files,
        epic=epic, op=op, ticket=ticket,
        git_head=_git_head(project_path) if project_path else None,
        related_decision_ids=related_decision_ids,
        session_id=session_id,
    )
    req = store.append_review_request(req)
    msg_parts = [f"[review-request] REQ-{req.id}", f"slug:{slug}"]
    if epic or op:
        scope_label = f"{epic or '?'}·{op or '?'}"
        msg_parts.append(f"scope:{scope_label}")
    if notes:
        msg_parts.append(f'"{notes[:80]}"')
    _notify_reviewer_window(tmux_session, " ".join(msg_parts))
    return req


def claim_review_request(store: DeltaStore, request_id: int,
                         claimed_by: str = "reviewer") -> ReviewRequest:
    """Reviewer claims a pending request. Idempotent if already theirs."""
    reqs = store.read_review_requests()
    for req in reqs:
        if req.id == request_id:
            if req.status not in (ReviewRequestStatus.PENDING,
                                   ReviewRequestStatus.CLAIMED):
                raise ValueError(
                    f"REQ-{request_id} is {req.status.value}; cannot claim."
                )
            req.status = ReviewRequestStatus.CLAIMED
            req.claimed_by = claimed_by
            req.claimed_at = datetime.now()
            store.update_review_request(req)
            return req
    raise ValueError(f"No review request id={request_id}")


def complete_review(store: DeltaStore, *, request_id: int,
                    summary: str,
                    blocking_findings: int = 0,
                    advisory_findings: int = 0,
                    lessons_added: List[int] = None,
                    critical_added: List[int] = None,
                    reviewer: str = "reviewer",
                    project_path: Optional[str] = None,
                    persist_target: Optional[str] = None) -> Review:
    """Reviewer finishes. Closes the request and records a Review row."""
    reqs = store.read_review_requests()
    target = next((r for r in reqs if r.id == request_id), None)
    if not target:
        raise ValueError(f"No review request id={request_id}")

    now = datetime.now()
    target.status = ReviewRequestStatus.DONE
    target.completed_at = now
    if not target.claimed_by:
        target.claimed_by = reviewer
        target.claimed_at = now
    store.update_review_request(target)

    rv = Review(
        request_id=request_id, reviewer=reviewer,
        summary=summary.strip(),
        blocking_findings=blocking_findings,
        advisory_findings=advisory_findings,
        lessons_added=",".join(str(x) for x in (lessons_added or [])),
        critical_added=",".join(str(x) for x in (critical_added or [])),
        epic=target.epic, op=target.op, ticket=target.ticket,
    )
    rv = store.append_review(rv)

    if persist_target and project_path:
        from dcam.decisions import persist_to_project
        persist_to_project(store, project_path, persist_target)

    return rv


def withdraw_review_request(store: DeltaStore, request_id: int,
                            reason: str = "") -> ReviewRequest:
    reqs = store.read_review_requests()
    for req in reqs:
        if req.id == request_id:
            req.status = ReviewRequestStatus.WITHDRAWN
            if reason:
                req.notes = (req.notes + f"\nWithdrawn: {reason}").strip()
            store.update_review_request(req)
            return req
    raise ValueError(f"No review request id={request_id}")


# --- Reviewer-context bootstrapping ----------------------------------------


def bootstrap_context(store: DeltaStore, recent_reviews: int = 5,
                      recent_findings: int = 10) -> str:
    """Build the snippet that every reviewer-session loads at startup.

    Includes:
    - All active critical points (the team's prescriptive guard rails).
    - The most recent N lessons categorized as `review-finding`.
    - Pending review requests so the reviewer knows what's queued.

    Returned as plain markdown the caller can append to the role prompt.
    """
    from dcam.decisions import (
        render_critical_section, _scope_label,
    )
    lines: List[str] = ["## Reviewer startup context", ""]

    crits = store.read_critical_points(status="active")
    if crits:
        lines.append("### Active critical points (enforce on every review)")
        lines.append("")
        for cp in sorted(crits, key=lambda p: p.id or 0):
            scope = _scope_label(cp.epic, cp.op)
            lines.append(f"- CP-{cp.id} [{scope}]: {cp.content}")
        lines.append("")

    lessons = store.read_lessons()
    findings = [l for l in lessons if (l.category or "").lower() == "review-finding"]
    findings.sort(key=lambda l: l.created_at, reverse=True)
    findings = findings[:recent_findings]
    if findings:
        lines.append("### Recent review findings (lessons you've recorded)")
        lines.append("")
        for l in findings:
            scope = _scope_label(l.epic, l.op)
            lines.append(f"- L-{l.id} [{scope}]: {l.content}")
        lines.append("")

    pending = store.read_review_requests(status="pending")
    claimed = store.read_review_requests(status="claimed")
    if pending or claimed:
        lines.append("### Outstanding review queue")
        lines.append("")
        for r in pending + claimed:
            scope = _scope_label(r.epic, r.op)
            tag = "[claimed]" if r.status == ReviewRequestStatus.CLAIMED else "[pending]"
            head = f"REQ-{r.id} {tag} slug:{r.slug or '?'} [{scope}]"
            if r.notes:
                head += f" — {r.notes[:80]}"
            lines.append(f"- {head}")
        lines.append("")

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)


# --- Handoff render --------------------------------------------------------


def render_handoffs_section(handoffs: List[Handoff]) -> str:
    """Render pending + acknowledged handoffs as markdown."""
    if not handoffs:
        return "## Handoffs\n\n_No handoffs recorded yet._\n"
    pending = [h for h in handoffs if h.status == HandoffStatus.PENDING]
    acked = [h for h in handoffs if h.status == HandoffStatus.ACKNOWLEDGED]
    pending.sort(key=lambda h: h.id or 0)
    acked.sort(key=lambda h: h.id or 0)

    lines = ["## Handoffs", ""]
    if pending:
        lines.append("### Pending")
        lines.append("")
        for h in pending:
            lines.append(_render_handoff(h))
        lines.append("")
    if acked:
        lines.append("### Acknowledged")
        lines.append("")
        for h in acked:
            lines.append(_render_handoff(h))
        lines.append("")
    return "\n".join(lines)


def _render_handoff(h: Handoff) -> str:
    from dcam.decisions import _scope_label, _ticket_link
    date = h.created_at.strftime("%Y-%m-%d")
    scope = _scope_label(h.epic, h.op)
    parts = [f"- **HO-{h.id}** `{h.from_slug}` → `{h.to_slug}` "
             f"({scope}, {date})"]
    if h.ticket:
        parts[0] += f"  ·  {_ticket_link(h.ticket)}"
    if h.files:
        parts.append(f"  - _Files_: {h.files}")
    if h.notes:
        parts.append(f"  - _Notes_: {h.notes}")
    if h.status == HandoffStatus.ACKNOWLEDGED:
        ack_date = (h.acknowledged_at or h.created_at).strftime("%Y-%m-%d")
        ack_str = f"acked {ack_date}"
        if h.ack_notes:
            ack_str += f" — {h.ack_notes}"
        parts.append(f"  - _{ack_str}_")
    return "\n".join(parts)


def create_handoff(store: DeltaStore, *, from_slug: str, to_slug: str,
                   files: str = "", notes: str = "",
                   epic: Optional[str] = None, op: Optional[str] = None,
                   ticket: Optional[str] = None,
                   project_path: Optional[str] = None,
                   persist_target: Optional[str] = None,
                   tmux_session: Optional[str] = None) -> Handoff:
    h = Handoff(
        from_slug=from_slug, to_slug=to_slug,
        files=files, notes=notes.strip(),
        epic=epic, op=op, ticket=ticket,
    )
    h = store.append_handoff(h)
    # Best-effort tmux send-keys to the receiver
    try:
        from dcam import tmux as tmux_mod
        if tmux_session and tmux_mod.session_exists(tmux_session):
            tmux_mod.send_keys(tmux_session, to_slug,
                               f"# [handoff] HO-{h.id} from {from_slug}: {notes[:80]}")
    except Exception:
        pass
    if persist_target and project_path:
        from dcam.decisions import persist_to_project
        persist_to_project(store, project_path, persist_target)
    return h


def acknowledge_handoff(store: DeltaStore, handoff_id: int,
                        ack_notes: Optional[str] = None) -> Handoff:
    handoffs = store.read_handoffs()
    for h in handoffs:
        if h.id == handoff_id:
            h.status = HandoffStatus.ACKNOWLEDGED
            h.ack_notes = ack_notes
            h.acknowledged_at = datetime.now()
            store.update_handoff(h)
            return h
    raise ValueError(f"No handoff id={handoff_id}")
