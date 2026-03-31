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


def get_store(ns: str) -> DeltaStore:
    return DeltaStore(namespace=ns)


def cmd_init(args):
    store = get_store(args.namespace)
    store.init_tables()
    hook_path, config_path = kiro.install_hooks()
    print(f"✓ DeltaCAT tables initialized (namespace: {args.namespace})")
    print(f"✓ Kiro hook installed: {hook_path}")
    print(f"✓ Kiro agent config: {config_path}")
    print(f"✓ Beads: {'connected' if bridge.bd_available() else 'not available (install bd for task tracking)'}")


def cmd_status(args):
    store = get_store(args.namespace)
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
    store = get_store(args.namespace)
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
    print(json.dumps({"session_id": session.session_id, "title": session.title,
                       "beads_issue_id": session.beads_issue_id}, indent=2))


def cmd_chat_end(args):
    store = get_store(args.namespace)
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
    store = get_store(args.namespace)
    sessions = sorted(store.read_sessions(), key=lambda s: s.started_at, reverse=True)
    for s in sessions[:args.limit]:
        ended = "active" if not s.ended_at else s.ended_at.strftime("%H:%M")
        bd = f" [bd:{s.beads_issue_id}]" if s.beads_issue_id else ""
        print(f"  {s.session_id}  {s.started_at.strftime('%Y-%m-%d %H:%M')} → {ended}  "
              f"{s.message_count:>3} msgs  {s.title}{bd}")


def cmd_chat_recall(args):
    store = get_store(args.namespace)
    msgs = sorted(store.read_messages(args.session_id), key=lambda m: m.timestamp)
    if not msgs:
        print(f"No messages for session {args.session_id}")
        return
    for msg in msgs[-args.limit:]:
        print(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.role.value}: {msg.content}")


def cmd_chat_search(args):
    store = get_store(args.namespace)
    q = args.query.lower()
    for msg in store.read_messages():
        if q in msg.content.lower():
            print(f"  [{msg.session_id}] {msg.role.value}: {msg.content[:120]}")


def cmd_compact(args):
    store = get_store(args.namespace)
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
    store = get_store(args.namespace)
    chunks = compact.lookup(store, args.symbol)
    if not chunks:
        print(f"No matches for '{args.symbol}'")
        return
    for c in chunks:
        print(f"  {c.chunk_type.value:8} {c.name:30} {c.file_path} L{c.start_line}-{c.end_line}")
        print(f"           {c.summary[:100]}")


def cmd_fetch(args):
    store = get_store(args.namespace)
    chunks = store.read_chunks()
    chunk = next((c for c in chunks if c.chunk_id == args.chunk_id), None)
    if not chunk:
        print(f"Chunk {args.chunk_id} not found", file=sys.stderr)
        sys.exit(1)
    code = compact.fetch_lines(chunk.file_path, chunk.start_line, chunk.end_line)
    print(f"# {chunk.name} ({chunk.chunk_type.value}) — {chunk.file_path} L{chunk.start_line}-{chunk.end_line}")
    print(code)


def cmd_resolve(args):
    store = get_store(args.namespace)
    ctx = resolver.resolve(store, args.message)
    if ctx:
        print(ctx)
    else:
        print("No relevant context found.")


def main():
    p = argparse.ArgumentParser(prog="dcam", description="DeltaCAT Agent Memory")
    p.add_argument("--namespace", default="dcam")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("status")

    chat_p = sub.add_parser("chat")
    csub = chat_p.add_subparsers(dest="chat_cmd")
    s = csub.add_parser("start"); s.add_argument("--title", default=None)
    e = csub.add_parser("end"); e.add_argument("session_id"); e.add_argument("--summary", default=None)
    l = csub.add_parser("list"); l.add_argument("--limit", type=int, default=20)
    r = csub.add_parser("recall"); r.add_argument("session_id"); r.add_argument("--limit", type=int, default=100)
    sr = csub.add_parser("search"); sr.add_argument("query")

    cp = sub.add_parser("compact"); cp.add_argument("path", nargs="?", default="."); cp.add_argument("--list", action="store_true")
    lk = sub.add_parser("lookup"); lk.add_argument("symbol")
    ft = sub.add_parser("fetch"); ft.add_argument("chunk_id", type=int)
    rv = sub.add_parser("resolve"); rv.add_argument("message")

    args = p.parse_args()
    cmds = {"init": cmd_init, "status": cmd_status, "compact": cmd_compact,
            "lookup": cmd_lookup, "fetch": cmd_fetch, "resolve": cmd_resolve}

    if args.command == "chat":
        {"start": cmd_chat_start, "end": cmd_chat_end, "list": cmd_chat_list,
         "recall": cmd_chat_recall, "search": cmd_chat_search}.get(args.chat_cmd, lambda _: chat_p.print_help())(args)
    elif args.command in cmds:
        cmds[args.command](args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
