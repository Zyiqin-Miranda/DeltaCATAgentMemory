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
import sys
import uuid
from datetime import datetime

from dcam import bridge, compact, kiro, resolver
from dcam.models import ChatMessage, ChatSession, Memory, MemoryType, MessageRole
from dcam.store import DeltaStore
def get_store(ns: str, backend: str = "bm25") -> DeltaStore:
    return DeltaStore(namespace=ns, search_backend=backend)


def cmd_init(args):
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
    sessions = sorted(store.read_sessions(), key=lambda s: s.started_at, reverse=True)
    for s in sessions[:args.limit]:
        ended = "active" if not s.ended_at else s.ended_at.strftime("%H:%M")
        bd = f" [bd:{s.beads_issue_id}]" if s.beads_issue_id else ""
        print(f"  {s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')} → {ended}  "
              f"{s.message_count:>3} msgs  {s.title}{bd}")


def cmd_chat_recall(args):
    store = get_store(args.namespace, args.search_backend)
    msgs = sorted(store.read_messages(args.session_id), key=lambda m: m.timestamp)
    if not msgs:
        print(f"No messages for session {args.session_id}")
        return
    for msg in msgs[-args.limit:]:
        print(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.role.value}: {msg.content}")


def cmd_chat_search(args):
    store = get_store(args.namespace, args.search_backend)
    results = store.search_messages(args.query, limit=args.limit)
    if not results:
        print(f"No results for '{args.query}'")
        return
    for msg in results:
        print(f"  [{msg.session_id}] {msg.role.value}: {msg.content[:120]}")


def cmd_chat_enter(args):
    store = get_store(args.namespace, args.search_backend)
    from dcam.interactive import run_interactive
    run_interactive(store, args.session_id)


def cmd_compact(args):
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
    chunks = compact.lookup(store, args.symbol)
    if not chunks:
        print(f"No matches for '{args.symbol}'")
        return
    for c in chunks:
        print(f"  {c.chunk_type.value:8} {c.name:30} {c.file_path} L{c.start_line}-{c.end_line}")
        print(f"           {c.summary[:100]}")


def cmd_fetch(args):
    store = get_store(args.namespace, args.search_backend)
    chunks = store.read_chunks()
    chunk = next((c for c in chunks if c.chunk_id == args.chunk_id), None)
    if not chunk:
        print(f"Chunk {args.chunk_id} not found", file=sys.stderr)
        sys.exit(1)
    code = compact.fetch_lines(chunk.file_path, chunk.start_line, chunk.end_line)
    print(f"# {chunk.name} ({chunk.chunk_type.value}) — {chunk.file_path} L{chunk.start_line}-{chunk.end_line}")
    print(code)


def cmd_resolve(args):
    store = get_store(args.namespace, args.search_backend)
    ctx = resolver.resolve(store, args.message)
    if ctx:
        print(ctx)
    else:
        print("No relevant context found.")


def cmd_serve(args):
    from dcam.mcp_server import run_server
    run_server()


def cmd_orchestrate(args):
    from dcam.orchestrator import Orchestrator
    store = get_store(args.namespace, args.search_backend)
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
    store = get_store(args.namespace, args.search_backend)
    tasks = plan_session(store, args.session_id)
    if not tasks:
        print("Failed to plan. Check session ID and kiro-cli availability.")
        return
    print(f"Created {len(tasks)} tasks:")
    for t in tasks:
        dep = " (plan)" if "type:plan" in t.labels else ""
        print(f"  {t.id}  {t.title}{dep}")


def main():
    p = argparse.ArgumentParser(prog="dcam", description="DeltaCAT Agent Memory")
    p.add_argument("--namespace", default="dcam")
    p.add_argument("--search-backend", default="bm25", choices=["bm25", "substring"],
                   help="Search algorithm (default: bm25)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("status")

    chat_p = sub.add_parser("chat")
    csub = chat_p.add_subparsers(dest="chat_cmd")
    s = csub.add_parser("start"); s.add_argument("--title", default=None); s.add_argument("--from", dest="from_session", default=None, help="Copy context from a previous session ID")
    e = csub.add_parser("end"); e.add_argument("session_id"); e.add_argument("--summary", default=None)
    l = csub.add_parser("list"); l.add_argument("--limit", type=int, default=20)
    r = csub.add_parser("recall"); r.add_argument("session_id"); r.add_argument("--limit", type=int, default=1000)
    sr = csub.add_parser("search"); sr.add_argument("query"); sr.add_argument("--limit", type=int, default=50)

    enter = csub.add_parser("enter"); enter.add_argument("session_id")

    cp = sub.add_parser("compact"); cp.add_argument("path", nargs="?", default="."); cp.add_argument("--list", action="store_true")
    lk = sub.add_parser("lookup"); lk.add_argument("symbol")
    ft = sub.add_parser("fetch"); ft.add_argument("chunk_id", type=int)
    rv = sub.add_parser("resolve"); rv.add_argument("message")
    sub.add_parser("serve", help="Start DCAM MCP server")

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

    args = p.parse_args()
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
    elif args.command in cmds:
        cmds[args.command](args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
