"""DCAM Agent Orchestrator — coordinates agents via beads task graph.

Polls beads for ready tasks (no open blockers), dispatches them to
kiro-cli agents, tracks progress, and manages dependencies.

Usage:
    dcam orchestrate                    # Start orchestration loop
    dcam task create "Title" -p 0       # Create a task
    dcam task plan SESSION_ID           # Decompose session into subtasks
    dcam task list                      # List all tasks
    dcam task ready                     # Show tasks ready for work
"""

import json
import subprocess
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

from dcam.bridge import _run_bd, bd_available
from dcam.models import ChatMessage, MessageRole
from dcam.store import DeltaStore


class Task:
    """A beads issue representing a unit of work."""
    def __init__(self, id: str, title: str, status: str = "open",
                 priority: int = 1, labels: List[str] = None,
                 session_id: Optional[str] = None):
        self.id = id
        self.title = title
        self.status = status
        self.priority = priority
        self.labels = labels or []
        self.session_id = session_id


def _parse_issues(raw: object) -> List[Task]:
    """Parse bd JSON output into Task objects."""
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    tasks = []
    for item in items:
        labels = item.get("labels", [])
        sid = None
        for l in labels:
            if isinstance(l, str) and l.startswith("session:"):
                sid = l.split(":", 1)[1]
        tasks.append(Task(
            id=item.get("id", ""),
            title=item.get("title", ""),
            status=item.get("status", "open"),
            priority=item.get("priority", 1),
            labels=labels,
            session_id=sid,
        ))
    return tasks


# --- Task CRUD ---

def create_task(title: str, priority: int = 1, labels: List[str] = None,
                session_id: Optional[str] = None, parent_id: Optional[str] = None) -> Optional[Task]:
    """Create a beads issue as a task."""
    args = ["create", title, "-p", str(priority)]
    for l in (labels or []):
        args.extend(["--label", l])
    if session_id:
        args.extend(["--label", f"session:{session_id}"])
    result = _run_bd(args)
    if not result:
        return None
    task = _parse_issues(result)
    if task and parent_id:
        _run_bd(["dep", "add", task[0].id, parent_id])
    return task[0] if task else None


def list_tasks(status: str = "open", labels: List[str] = None) -> List[Task]:
    """List tasks with optional filters."""
    args = ["list", "--status", status]
    for l in (labels or []):
        args.extend(["--label", l])
    result = _run_bd(args)
    return _parse_issues(result) if result else []


def get_ready_tasks() -> List[Task]:
    """Get tasks with no open blockers (ready for work)."""
    result = _run_bd(["ready"])
    return _parse_issues(result) if result else []


def claim_task(task_id: str) -> bool:
    """Claim a task (set in_progress)."""
    result = _run_bd(["update", task_id, "--claim"])
    return result is not None


def complete_task(task_id: str, reason: str = "completed") -> bool:
    """Close a task."""
    result = _run_bd(["close", task_id, "--reason", reason])
    return result is not None


def add_dependency(blocker_id: str, blocked_id: str) -> bool:
    """Add a dependency: blocked_id is blocked by blocker_id."""
    result = _run_bd(["dep", "add", blocked_id, blocker_id])
    return result is not None


def comment_task(task_id: str, text: str):
    """Add a comment to a task."""
    _run_bd(["comment", task_id, text[:500]])


# --- Agent Dispatch ---

def dispatch_to_agent(task: Task, store: DeltaStore) -> Optional[str]:
    """Send a task to kiro-cli and return the response."""
    from dcam.agent_instructions import AGENT_INSTRUCTIONS

    prompt = (
        f"{AGENT_INSTRUCTIONS}\n\n"
        f"---\n\n"
        f"Task: {task.title}\nTask ID: {task.id}\nPriority: {task.priority}\n"
        f"Labels: {', '.join(task.labels)}"
    )

    # Include session context if available
    if task.session_id:
        msgs = store.read_messages(task.session_id)
        if msgs:
            recent = msgs[-10:]
            context_lines = [f"{'User' if m.role == MessageRole.USER else 'Assistant'}: {m.content[:200]}"
                             for m in recent]
            prompt += "\n\nSession context:\n" + "\n".join(context_lines)

    try:
        result = subprocess.run(
            ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", prompt],
            capture_output=True, text=True, timeout=180,
        )
        return result.stdout.strip() if result.stdout else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# --- Orchestration Loop ---

class Orchestrator:
    """Polls beads for ready tasks and dispatches them to agents."""

    def __init__(self, store: DeltaStore, poll_interval: int = 10,
                 on_task_start: Optional[Callable] = None,
                 on_task_done: Optional[Callable] = None):
        self.store = store
        self.poll_interval = poll_interval
        self.on_task_start = on_task_start
        self.on_task_done = on_task_done
        self.running = False

    def run(self):
        """Start the orchestration loop."""
        if not bd_available():
            print("Error: beads (bd) not available. Run: bd init")
            return

        self.running = True
        print(f"Orchestrator started (polling every {self.poll_interval}s)")
        print("Press Ctrl+C to stop\n")

        while self.running:
            try:
                self._process_ready()
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                print("\nOrchestrator stopped.")
                self.running = False

    def _process_ready(self):
        """Process all ready tasks."""
        tasks = get_ready_tasks()
        if not tasks:
            return

        for task in sorted(tasks, key=lambda t: t.priority):
            # Skip non-agent tasks
            if any(l.startswith("type:chat-session") for l in task.labels):
                continue

            print(f"→ Processing: [{task.id}] {task.title} (P{task.priority})")

            if self.on_task_start:
                self.on_task_start(task)

            # Claim it
            claim_task(task.id)
            comment_task(task.id, "[agent] Starting work")

            # Dispatch to agent
            response = dispatch_to_agent(task, self.store)

            if response:
                comment_task(task.id, f"[agent] {response[:500]}")
                complete_task(task.id, "Agent completed task")
                print(f"  ✓ Completed: [{task.id}]")

                # Log to session if linked
                if task.session_id:
                    self.store.append_message(ChatMessage(
                        session_id=task.session_id, role=MessageRole.ASSISTANT,
                        content=response, timestamp=datetime.now(),
                    ))
            else:
                comment_task(task.id, "[agent] Failed to process — no response")
                print(f"  ✗ Failed: [{task.id}]")

            if self.on_task_done:
                self.on_task_done(task, response)


# --- Task Planning ---

def plan_session(store: DeltaStore, session_id: str) -> List[Task]:
    """Decompose a session's goal into subtasks using kiro-cli."""
    sessions = store.read_sessions()
    session = next((s for s in sessions if s.session_id == session_id), None)
    if not session:
        return []

    prompt = (
        f"Break down this task into 2-5 concrete subtasks. "
        f"Task: {session.title}\n"
        f"Return ONLY a JSON array of strings, each being a subtask title. No explanation."
    )

    try:
        result = subprocess.run(
            ["kiro-cli", "chat", "--no-interactive", prompt],
            capture_output=True, text=True, timeout=60,
        )
        # Parse JSON array from response
        output = result.stdout.strip()
        # Find JSON array in output
        start = output.find("[")
        end = output.rfind("]") + 1
        if start >= 0 and end > start:
            subtask_titles = json.loads(output[start:end])
        else:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []

    # Create parent task
    parent = create_task(session.title, priority=0, session_id=session_id,
                         labels=["type:plan"])
    if not parent:
        return []

    # Create subtasks as children
    tasks = [parent]
    for i, title in enumerate(subtask_titles):
        if isinstance(title, str):
            child = create_task(title, priority=1, session_id=session_id,
                                labels=[f"step:{i+1}"], parent_id=parent.id)
            if child:
                tasks.append(child)

    return tasks
