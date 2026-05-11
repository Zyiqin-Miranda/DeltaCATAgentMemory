#!/usr/bin/env python3
"""dcam — DeltaCAT Agent Memory CLI.

One command to integrate Kiro + DeltaCAT memory + Beads.

Usage:
    dcam init                          Initialize everything
    dcam status                        Show integration status
    dcam chat start [--title T]        Start tracked session
    dcam chat end SESSION_ID           End session + extract memories
    dcam chat list                     List past sessions
    dcam chat recall SESSION_ID        Replay a session
    dcam chat search QUERY             Search chat history
    dcam compact PATH                  Index file or directory
    dcam compact --list                List indexed files
    dcam lookup SYMBOL                 Find symbol in index
    dcam fetch CHUNK_ID                Fetch chunk source
    dcam resolve MESSAGE               Auto-resolve context for a message
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime

from dcam import bridge, claude_code, compact, kiro, resolver
from dcam.models import ChatMessage, ChatSession, Memory, MemoryType, MessageRole
from dcam.store import DeltaStore
def get_store(ns: str, backend: str = "bm25", catalog: str = "local",
              branch: str = "main") -> DeltaStore:
    return DeltaStore(namespace=ns, search_backend=backend, catalog_backend=catalog, branch=branch)


def cmd_init(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    store.init_tables()
    hook_path, config_path = kiro.install_hooks()
    print(f"✓ DeltaCAT tables initialized (namespace: {args.namespace})")
    print(f"✓ Kiro hook installed: {hook_path}")
    print(f"✓ Kiro agent config: {config_path}")
    if bridge.bd_available():
        # Check if bd is initialized in this directory
        import subprocess
        result = subprocess.run(["bd", "create", "--help"], capture_output=True, text=True)
        check = subprocess.run(["bd", "list", "--json"], capture_output=True, text=True)
        if check.returncode != 0 and "no beads database" in check.stdout + check.stderr:
            print(f"⚠ Beads: bd CLI found but not initialized here. Run: bd init")
        else:
            print(f"✓ Beads: connected")
    else:
        print(f"✓ Beads: not available (install bd for task tracking)")


def cmd_status(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    sessions = store.read_sessions()
    chunks = store.read_chunks()
    mems = [m for m in store.read_memories() if m.active]
    print(f"Namespace:  {args.namespace}")
    print(f"Sessions:   {len(sessions)} ({sum(1 for s in sessions if not s.ended_at)} active)")
    print(f"Messages:   {len(store.read_messages())}")
    print(f"Memories:   {len(mems)} active")
    print(f"Indexed:    {len(set(c.file_path for c in chunks))} files, {len(chunks)} chunks")
    print(f"Beads:      {'connected' if bridge.bd_available() else 'not available'}")


def cmd_chat_start(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    session = ChatSession(
        session_id=str(uuid.uuid4())[:12],
        title=args.title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        started_at=datetime.now(),
    )
    # Create beads issue
    issue_id = bridge.create_session_issue(session)
    if issue_id:
        session.beads_issue_id = issue_id

    sessions = store.read_sessions()
    sessions.append(session)
    store.write_sessions(sessions)

    # Copy messages from a previous session if --from is specified
    if args.from_session:
        old_msgs = store.read_messages(args.from_session)
        for m in old_msgs:
            store.append_message(ChatMessage(
                session_id=session.session_id, role=m.role,
                content=m.content, timestamp=m.timestamp, metadata=m.metadata,
            ))
        print(json.dumps({"session_id": session.session_id, "title": session.title,
                           "beads_issue_id": session.beads_issue_id,
                           "copied_from": args.from_session,
                           "messages_copied": len(old_msgs)}, indent=2))
    else:
        print(json.dumps({"session_id": session.session_id, "title": session.title,
                           "beads_issue_id": session.beads_issue_id}, indent=2))


def cmd_chat_end(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    sessions = store.read_sessions()
    for s in sessions:
        if s.session_id == args.session_id:
            s.ended_at = datetime.now()
            s.summary = args.summary
            s.message_count = len(store.read_messages(args.session_id))
            # Close beads issue
            bridge.close_session_issue(s.beads_issue_id,
                                       args.summary or f"Ended with {s.message_count} messages")
            break
    store.write_sessions(sessions)

    # Extract memories from assistant messages
    msgs = store.read_messages(args.session_id)
    mems = store.read_memories()
    indicators = ["created", "updated", "fixed", "implemented", "completed"]
    extracted = 0
    for msg in msgs:
        if msg.role == MessageRole.ASSISTANT and any(w in msg.content.lower() for w in indicators):
            m = Memory(id=store._next_id("memories"), type=MemoryType.EPISODIC,
                       topic="chat-history", content=msg.content[:500],
                       source_session_id=args.session_id)
            mems.append(m)
            extracted += 1
    if extracted:
        store.write_memories(mems)
    print(json.dumps({"ended": args.session_id, "memories_extracted": extracted}))


def cmd_chat_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    sessions = sorted(store.read_sessions(), key=lambda s: s.started_at, reverse=True)
    all_msgs = store.read_messages()
    for s in sessions[:args.limit]:
        count = sum(1 for m in all_msgs if m.session_id == s.session_id)
        ended = "active" if not s.ended_at else s.ended_at.strftime("%H:%M")
        bd = f" [bd:{s.beads_issue_id}]" if s.beads_issue_id else ""
        print(f"  {s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')} → {ended}  "
              f"{count:>3} msgs  {s.title}{bd}")


def cmd_chat_recall(args):
    import re
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    msgs = sorted(store.read_messages(args.session_id), key=lambda m: m.timestamp)
    if not msgs:
        print(f"No messages for session {args.session_id}")
        return
    for msg in msgs[-args.limit:]:
        content = ansi_escape.sub('', msg.content).strip()
        print(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.role.value}: {content}")


def cmd_chat_search(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    results = store.search_messages(args.query, limit=args.limit)
    if not results:
        print(f"No results for '{args.query}'")
        return
    for msg in results:
        print(f"  [{msg.session_id}] {msg.role.value}: {msg.content[:120]}")


def cmd_chat_enter(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    from dcam.interactive import run_interactive
    run_interactive(store, args.session_id)


def cmd_compact(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    if args.list:
        chunks = store.read_chunks()
        files = {}
        for c in chunks:
            files.setdefault(c.file_path, []).append(c)
        for fp, cs in sorted(files.items()):
            print(f"  {fp}  {len(cs)} chunks")
        return

    from pathlib import Path
    path = Path(args.path)
    if path.is_dir():
        count = compact.compact_directory(store, str(path))
        print(f"✓ Compacted {count} files")
    elif path.is_file():
        n = compact.compact_file(store, str(path))
        print(f"✓ {path}: {n} chunks")
        for c in store.read_chunks(str(path)):
            print(f"    {c.chunk_type.value:8} {c.name:30} L{c.start_line}-{c.end_line}")
    else:
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(1)


def cmd_lookup(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    chunks = compact.lookup(store, args.symbol)
    if not chunks:
        print(f"No matches for '{args.symbol}'")
        return
    for c in chunks:
        print(f"  {c.chunk_type.value:8} {c.name:30} {c.file_path} L{c.start_line}-{c.end_line}")
        print(f"           {c.summary[:100]}")


def cmd_fetch(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    chunks = store.read_chunks()
    chunk = next((c for c in chunks if c.chunk_id == args.chunk_id), None)
    if not chunk:
        print(f"Chunk {args.chunk_id} not found", file=sys.stderr)
        sys.exit(1)
    code = compact.fetch_lines(chunk.file_path, chunk.start_line, chunk.end_line)
    print(f"# {chunk.name} ({chunk.chunk_type.value}) — {chunk.file_path} L{chunk.start_line}-{chunk.end_line}")
    print(code)


def cmd_resolve(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    ctx = resolver.resolve(store, args.message)
    if ctx:
        print(ctx)
    else:
        print("No relevant context found.")


def cmd_memory_add(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    m = store.add_project_memory(args.content, name=args.name, topic=args.topic, category=args.category)
    label = f" ({m.name})" if m.name else ""
    print(f"Stored project memory {m.id}{label}: {m.content[:80]}")


def cmd_memory_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    mems = store.read_project_memories()
    if not mems:
        print("No project memories stored.")
        return
    for m in mems:
        name = f"[{m.name}] " if m.name else ""
        topic = f"({m.topic}) " if m.topic else ""
        print(f"  {m.id:>4}  {name}{topic}{m.content[:100]}")


def cmd_memory_recall(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    m = store.recall_by_name(args.name)
    if m:
        print(m.content)
    else:
        print(f"No memory named '{args.name}'")


def cmd_memory_context(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    ctx = store.get_session_context(args.session_id)
    if ctx:
        print(ctx)
    else:
        print("No memories found.")


def cmd_serve(args):
    from dcam.mcp_server import run_server
    run_server()


def cmd_branch_list(args):
    from dcam.branch_store import BranchStore
    bs = BranchStore(namespace=args.namespace, branch="main")
    for b in bs.meta.list_all():
        current = " ←" if b["name"] == args.branch else ""
        merged = " (merged)" if b.get("merged") else ""
        print(f"  {b['name']}{merged}{current}")


def cmd_branch_merge(args):
    from dcam.branch_store import BranchStore
    bs = BranchStore(namespace=args.namespace, branch=args.name)
    count = bs.merge_to_main()
    print(f"Merged {count} deltas from '{args.name}' → main")


def cmd_branch_delete(args):
    from dcam.branch_store import BranchStore
    bs = BranchStore(namespace=args.namespace, branch=args.name)
    if not bs.meta.branches.get(args.name, {}).get("merged"):
        print(f"Branch '{args.name}' not merged yet. Merge first or use --force")
        if not args.force:
            return
    bs.delete_branch()
    print(f"Deleted branch '{args.name}'")


def cmd_orchestrate(args):
    from dcam.orchestrator import Orchestrator
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    orch = Orchestrator(store, poll_interval=args.interval)
    orch.run()


def cmd_task_create(args):
    from dcam.orchestrator import create_task
    labels = args.labels.split(",") if args.labels else []
    task = create_task(args.title, priority=args.priority, labels=labels,
                       session_id=args.session)
    if task:
        print(json.dumps({"id": task.id, "title": task.title, "priority": task.priority}, indent=2))
    else:
        print("Failed to create task. Is bd initialized?", file=sys.stderr)
        sys.exit(1)


def cmd_task_list(args):
    from dcam.orchestrator import list_tasks
    tasks = list_tasks(status=args.status)
    if not tasks:
        print("No tasks found.")
        return
    for t in tasks:
        labels = " ".join(f"[{l}]" for l in t.labels[:3])
        print(f"  {t.id}  P{t.priority}  {t.status:12} {t.title:50} {labels}")


def cmd_task_ready(args):
    from dcam.orchestrator import get_ready_tasks
    tasks = get_ready_tasks()
    if not tasks:
        print("No tasks ready (all blocked or none open).")
        return
    for t in tasks:
        print(f"  {t.id}  P{t.priority}  {t.title}")


def cmd_task_plan(args):
    from dcam.orchestrator import plan_session
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    tasks = plan_session(store, args.session_id)
    if not tasks:
        print("Failed to plan. Check session ID and kiro-cli availability.")
        return
    print(f"Created {len(tasks)} tasks:")
    for t in tasks:
        dep = " (plan)" if "type:plan" in t.labels else ""
        print(f"  {t.id}  {t.title}{dep}")


def cmd_claude_init(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    store.init_tables()
    project_path = args.project or os.getcwd()
    hook_path, settings_path = claude_code.install_claude_code_hook(
        project_path, args.namespace, args.catalog)
    print(f"✓ DeltaCAT tables initialized (namespace: {args.namespace})")
    print(f"✓ Claude Code hook: {hook_path}")
    print(f"✓ Settings updated: {settings_path}")
    # Do initial sync
    synced = claude_code.sync_all_sessions(store, project_path)
    if synced:
        print(f"✓ Synced {synced} existing sessions")


def cmd_claude_sync(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    store.init_tables()
    project_path = args.project or os.getcwd()
    if args.session_file:
        result = claude_code.sync_session(store, args.session_file, title=args.title)
        if result:
            print(f"✓ Synced session {result.session_id}: {result.title}")
        else:
            print("No new messages to sync.")
    else:
        synced = claude_code.sync_all_sessions(store, project_path)
        print(f"✓ Synced {synced} session(s)")


def cmd_claude_list(args):
    project_path = args.project or os.getcwd()
    sessions = claude_code.list_sessions(project_path)
    if not sessions:
        print("No Claude Code sessions found.")
        return
    for s in sessions[:args.limit]:
        print(f"  {s['session_id'][:12]}  {s.get('started_at', 'N/A')[:16]}  "
              f"{s['message_count']:>3} msgs  {s['project']}")


def cmd_claude_recall(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    msgs = store.read_messages(args.session_id)
    if not msgs:
        # Try syncing first
        project_path = args.project or os.getcwd()
        sessions = claude_code.list_sessions(project_path)
        match = next((s for s in sessions if s["session_id"].startswith(args.session_id)), None)
        if match:
            claude_code.sync_session(store, match["path"])
            msgs = store.read_messages(match["session_id"])
    if not msgs:
        print(f"No messages for session {args.session_id}")
        return

    claude_args = getattr(args, "claude_args", [])
    # Strip leading '--' separator if present
    if claude_args and claude_args[0] == "--":
        claude_args = claude_args[1:]

    if claude_args:
        # Build context from recalled messages and launch a claude session
        context_lines = [f"# Recalled Session Context ({args.session_id})"]
        context_lines.append(f"# Messages: {len(msgs)} | Showing last {min(args.limit, len(msgs))}")
        context_lines.append("")
        for msg in msgs[-args.limit:]:
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            content = msg.content[:500]
            context_lines.append(f"**{role}** [{msg.timestamp.strftime('%H:%M:%S')}]: {content}")
            context_lines.append("")
        context = "\n".join(context_lines)

        cmd = ["claude", "--append-system-prompt", context] + claude_args
        os.execvp("claude", cmd)
    else:
        # Print-only mode (original behavior)
        for msg in msgs[-args.limit:]:
            role = "User" if msg.role == MessageRole.USER else "Assistant"
            content = msg.content[:200]
            print(f"[{msg.timestamp.strftime('%H:%M:%S')}] {role}: {content}")
            print()


def cmd_claude_search(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    results = store.search_messages(args.query, limit=args.limit)
    if not results:
        print(f"No results for '{args.query}'")
        return
    for msg in results:
        role = "User" if msg.role == MessageRole.USER else "Asst"
        print(f"  [{msg.session_id[:8]}] {role}: {msg.content[:120]}")


def cmd_claude_context(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"))
    # Sync latest before showing context
    if not args.no_sync:
        project_path = args.project or os.getcwd()
        claude_code.sync_all_sessions(store, project_path)
    ctx = claude_code.get_recent_context(
        store, limit=args.sessions, max_messages_per_session=args.messages)
    if ctx:
        print(ctx)
    else:
        print("No previous session context available.")


def main():
    p = argparse.ArgumentParser(prog="dcam", description="DeltaCAT Agent Memory")
    p.add_argument("--namespace", default="dcam")
    p.add_argument("--search-backend", default="bm25", choices=["bm25", "substring"],
                   help="Search algorithm (default: bm25)")
    p.add_argument("--catalog", default="local", choices=["local", "delta", "branch", "deltacat"],
                   help="Storage backend: local, delta, branch (branched delta), or deltacat")
    p.add_argument("--branch", default="main",
                   help="Branch name for branch backend (default: main)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("status")

    chat_p = sub.add_parser("chat")
    csub = chat_p.add_subparsers(dest="chat_cmd")
    s = csub.add_parser("start"); s.add_argument("--title", default=None); s.add_argument("--from", dest="from_session", default=None, help="Copy context from a previous session ID")
    e = csub.add_parser("end"); e.add_argument("session_id"); e.add_argument("--summary", default=None)
    l = csub.add_parser("list"); l.add_argument("--limit", type=int, default=1000)
    r = csub.add_parser("recall"); r.add_argument("session_id"); r.add_argument("--limit", type=int, default=1000)
    sr = csub.add_parser("search"); sr.add_argument("query"); sr.add_argument("--limit", type=int, default=1000)

    enter = csub.add_parser("enter"); enter.add_argument("session_id")

    cp = sub.add_parser("compact"); cp.add_argument("path", nargs="?", default="."); cp.add_argument("--list", action="store_true")
    lk = sub.add_parser("lookup"); lk.add_argument("symbol")
    ft = sub.add_parser("fetch"); ft.add_argument("chunk_id", type=int)
    rv = sub.add_parser("resolve"); rv.add_argument("message")
    sub.add_parser("serve", help="Start DCAM MCP server")

    # branch subcommands
    br_p = sub.add_parser("branch", help="Branch management")
    bsub = br_p.add_subparsers(dest="branch_cmd")
    bsub.add_parser("list")
    bm = bsub.add_parser("merge"); bm.add_argument("name")
    bd = bsub.add_parser("delete"); bd.add_argument("name"); bd.add_argument("--force", action="store_true")

    # memory subcommands
    mem_p = sub.add_parser("memory", help="Project memory (cross-session)")
    msub = mem_p.add_subparsers(dest="mem_cmd")
    ma = msub.add_parser("add"); ma.add_argument("content"); ma.add_argument("--name", default=None); ma.add_argument("--topic", default=None); ma.add_argument("--category", default=None)
    msub.add_parser("list")
    mr = msub.add_parser("recall"); mr.add_argument("name")
    mc = msub.add_parser("context"); mc.add_argument("--session-id", default=None)

    # claude code subcommands
    cc_p = sub.add_parser("claude", help="Claude Code integration")
    ccsub = cc_p.add_subparsers(dest="claude_cmd")
    cci = ccsub.add_parser("init", help="Initialize Claude Code <-> DCAM integration")
    cci.add_argument("--project", default=None, help="Project path (default: cwd)")
    ccs = ccsub.add_parser("sync", help="Sync Claude Code sessions to DCAM")
    ccs.add_argument("--project", default=None); ccs.add_argument("--session-file", default=None)
    ccs.add_argument("--title", default=None)
    ccl = ccsub.add_parser("list", help="List Claude Code sessions")
    ccl.add_argument("--project", default=None); ccl.add_argument("--limit", type=int, default=20)
    ccr = ccsub.add_parser("recall", help="Recall a Claude Code session (pass -- followed by claude options to launch a session)")
    ccr.add_argument("session_id"); ccr.add_argument("--project", default=None)
    ccr.add_argument("--limit", type=int, default=50)
    ccsr = ccsub.add_parser("search", help="Search Claude Code history")
    ccsr.add_argument("query"); ccsr.add_argument("--limit", type=int, default=10)
    ccctx = ccsub.add_parser("context", help="Get context from recent sessions")
    ccctx.add_argument("--project", default=None)
    ccctx.add_argument("--sessions", type=int, default=3, help="Number of recent sessions")
    ccctx.add_argument("--messages", type=int, default=20, help="Messages per session")
    ccctx.add_argument("--no-sync", action="store_true", help="Skip sync before context")

    # orchestrate
    orch_p = sub.add_parser("orchestrate", help="Start orchestration loop")
    orch_p.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    # task subcommands
    task_p = sub.add_parser("task", help="Task management via beads")
    tsub = task_p.add_subparsers(dest="task_cmd")
    tc = tsub.add_parser("create"); tc.add_argument("title"); tc.add_argument("-p", "--priority", type=int, default=1)
    tc.add_argument("--labels", default=None); tc.add_argument("--session", default=None)
    tl = tsub.add_parser("list"); tl.add_argument("--status", default="open")
    tsub.add_parser("ready")
    tp = tsub.add_parser("plan"); tp.add_argument("session_id")

    args, remaining = p.parse_known_args()

    # Pass remaining args to claude recall as claude CLI options
    if args.command == "claude" and getattr(args, "claude_cmd", None) == "recall":
        args.claude_args = remaining
    elif remaining:
        p.error(f"unrecognized arguments: {' '.join(remaining)}")
    else:
        args.claude_args = []

    cmds = {"init": cmd_init, "status": cmd_status, "compact": cmd_compact,
            "lookup": cmd_lookup, "fetch": cmd_fetch, "resolve": cmd_resolve,
            "orchestrate": cmd_orchestrate, "serve": cmd_serve}

    if args.command == "chat":
        {"start": cmd_chat_start, "end": cmd_chat_end, "list": cmd_chat_list,
         "recall": cmd_chat_recall, "search": cmd_chat_search,
         "enter": cmd_chat_enter}.get(args.chat_cmd, lambda _: chat_p.print_help())(args)
    elif args.command == "task":
        {"create": cmd_task_create, "list": cmd_task_list, "ready": cmd_task_ready,
         "plan": cmd_task_plan}.get(args.task_cmd, lambda _: task_p.print_help())(args)
    elif args.command == "memory":
        {"add": cmd_memory_add, "list": cmd_memory_list,
         "recall": cmd_memory_recall,
         "context": cmd_memory_context}.get(args.mem_cmd, lambda _: mem_p.print_help())(args)
    elif args.command == "claude":
        {"init": cmd_claude_init, "sync": cmd_claude_sync, "list": cmd_claude_list,
         "recall": cmd_claude_recall, "search": cmd_claude_search,
         "context": cmd_claude_context}.get(args.claude_cmd, lambda _: cc_p.print_help())(args)
    elif args.command == "branch":
        {"list": cmd_branch_list, "merge": cmd_branch_merge,
         "delete": cmd_branch_delete}.get(args.branch_cmd, lambda _: br_p.print_help())(args)
    elif args.command in cmds:
        cmds[args.command](args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
