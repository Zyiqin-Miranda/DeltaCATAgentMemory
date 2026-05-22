"""Heuristic candidate extraction from a Claude Code session transcript.

The reviewer (and other agents) sometimes drop one-line learnings or
critical points into chat that never make it to a `dcam tmux lesson` /
`dcam tmux critical add` call. This module scans transcripts for those
sentences, surfaces them as *candidates*, and provides an interactive
prompt to accept/edit/reject and promote them to real records.

Heuristics only — no LLM. Keywords and prefixes are conservative: better
to miss a candidate than to fill the queue with junk.

Output goes to `<root>/extract_candidates.json`. The catalog doesn't
manage this file — it's a working buffer, intentionally separate from
the committed tables until candidates are promoted.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dcam.models import MessageRole
from dcam.store import DeltaStore


# Strong markers — line literally starts with one of these, taken at face value.
STRONG_MARKERS = {
    "lesson": "lesson",
    "decision": "decision",
    "critical": "critical",
    "rule": "critical",          # "rule:" → critical point
    "invariant": "critical",
    "principle": "critical",
    "gotcha": "lesson",
    "watch out": "critical",
}

# Soft markers — phrases inside a sentence that suggest a candidate.
# We extract the sentence containing the phrase.
SOFT_PHRASES = {
    # critical-point indicators
    "from now on": "critical",
    "we'll never": "critical",
    "we will never": "critical",
    "we should never": "critical",
    "always remember to": "critical",
    "always validate": "critical",
    "the rule is": "critical",
    # lesson indicators
    "learned that": "lesson",
    "we learned": "lesson",
    "i learned": "lesson",
    "the lesson is": "lesson",
    "next time": "lesson",
    "burned us": "lesson",
}


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _candidate_from_strong(line: str) -> Optional[Tuple[str, str]]:
    """If the line begins with a strong marker, return (kind, content)."""
    head = line.strip().lower()
    for marker, kind in STRONG_MARKERS.items():
        prefix = marker + ":"
        if head.startswith(prefix):
            content = line.strip()[len(prefix):].strip()
            if content:
                return kind, content
    return None


def _candidates_from_soft(text: str) -> List[Tuple[str, str]]:
    """Return all (kind, sentence) candidates found via soft phrases."""
    out: List[Tuple[str, str]] = []
    for sentence in _split_sentences(text):
        s_low = sentence.lower()
        for phrase, kind in SOFT_PHRASES.items():
            if phrase in s_low:
                out.append((kind, sentence))
                break       # at most one classification per sentence
    return out


def extract_candidates_from_session(store: DeltaStore,
                                    session_id: str,
                                    role: str = "any") -> List[Dict]:
    """Walk a stored session's messages and return candidate records.

    `role` filters which messages to scan: "any", "user", or "assistant".
    """
    msgs = store.read_messages(session_id)
    if not msgs:
        return []
    if role == "user":
        msgs = [m for m in msgs if m.role == MessageRole.USER]
    elif role == "assistant":
        msgs = [m for m in msgs if m.role == MessageRole.ASSISTANT]

    seen: set = set()
    out: List[Dict] = []
    for m in msgs:
        content = m.content or ""

        # 1) line-prefixed strong markers. Track which lines fired so the
        #    soft pass can skip them — a `lesson: ...` line shouldn't
        #    re-fire as a soft critical hit.
        skip_lines: set = set()
        for line in content.splitlines():
            cand = _candidate_from_strong(line)
            if not cand:
                continue
            skip_lines.add(line)
            kind, text = cand
            key = (kind, text.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": kind,
                "content": text,
                "source_role": m.role.value,
                "source_session_id": session_id,
                "source_message_ts": m.timestamp.isoformat(),
                "match_type": "strong",
            })

        # 2) inline soft phrases — only on text not already strong-matched.
        residual = "\n".join(l for l in content.splitlines() if l not in skip_lines)
        for kind, sentence in _candidates_from_soft(residual):
            key = (kind, sentence.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "kind": kind,
                "content": sentence,
                "source_role": m.role.value,
                "source_session_id": session_id,
                "source_message_ts": m.timestamp.isoformat(),
                "match_type": "soft",
            })
    return out


def candidates_path(storage_root: Path) -> Path:
    """Where the candidates file lives.

    For project mode it's `<repo>/.dcam/extract_candidates.json`. For
    global mode it's `~/.dcam/extract_candidates.json`. Either way, this
    is a working buffer, not a committable artifact.
    """
    return Path(storage_root) / "extract_candidates.json"


def write_candidates(storage_root: Path, candidates: List[Dict],
                     append: bool = True):
    """Append new candidates to the buffer (deduped by (kind, content))."""
    path = candidates_path(storage_root)
    existing: List[Dict] = []
    if append and path.exists():
        try:
            existing = json.loads(path.read_text() or "[]")
        except json.JSONDecodeError:
            existing = []

    seen = {(c.get("kind"), (c.get("content") or "").strip().lower())
            for c in existing}
    for c in candidates:
        key = (c.get("kind"), (c.get("content") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        c.setdefault("captured_at", datetime.now().isoformat())
        c.setdefault("status", "candidate")  # candidate | accepted | rejected
        existing.append(c)

    path.write_text(json.dumps(existing, indent=2, default=str) + "\n")
    return path


def load_candidates(storage_root: Path) -> List[Dict]:
    path = candidates_path(storage_root)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text() or "[]")
    except json.JSONDecodeError:
        return []


def save_candidates(storage_root: Path, candidates: List[Dict]):
    path = candidates_path(storage_root)
    path.write_text(json.dumps(candidates, indent=2, default=str) + "\n")


# --- Promotion helpers (called from the interactive review loop) -----------


def promote_candidate(store: DeltaStore, candidate: Dict, *,
                      epic: Optional[str] = None,
                      op: Optional[str] = None,
                      category: Optional[str] = None,
                      rationale: Optional[str] = None,
                      project_path: Optional[str] = None,
                      persist_target: Optional[str] = None) -> Dict:
    """Convert an accepted candidate into a real Lesson / Decision /
    CriticalPoint record. Returns a dict with the new record's kind and id.
    """
    kind = candidate.get("kind")
    content = candidate.get("content", "").strip()
    session_id = candidate.get("source_session_id")

    from dcam import decisions as decmod
    if kind == "lesson":
        l = decmod.add_lesson(
            store, content=content,
            category=category or "review-finding",
            session_id=session_id,
            epic=epic, op=op,
            project_path=project_path,
            persist_target=persist_target,
        )
        return {"kind": "lesson", "id": l.id}
    elif kind == "critical":
        cp = decmod.add_critical_point(
            store, content=content, rationale=rationale,
            epic=epic, op=op, session_id=session_id,
            project_path=project_path,
            persist_target=persist_target,
        )
        return {"kind": "critical", "id": cp.id}
    elif kind == "decision":
        # Decisions are heavier: they need title/options/recommended/etc.
        # Promotion path here just records a decided decision with the
        # candidate content as title + rationale; the user can refine.
        d = decmod.Decision(
            title=content[:80],
            context=content,
            rationale=content,
            chosen=content[:40],
            status=decmod.DecisionStatus.DECIDED,
            decided_by="extract",
            session_id=session_id,
            epic=epic, op=op,
            decided_at=datetime.now(),
        )
        d = store.append_decision(d)
        if persist_target and project_path:
            decmod.persist_to_project(store, project_path, persist_target)
        return {"kind": "decision", "id": d.id}
    raise ValueError(f"Unknown candidate kind: {kind}")
