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
              branch: str = "main", storage_root: str = None) -> DeltaStore:
    return DeltaStore(namespace=ns, search_backend=backend,
                      catalog_backend=catalog, branch=branch,
                      storage_root=storage_root)


_GLOBAL_FLAGS_WITH_VALUE = {"--namespace", "--search-backend", "--catalog",
                            "--branch", "--root"}


def _hoist_global_flags(argv):
    """Move global flags (--namespace, --catalog, etc.) to the front of argv.

    argparse only recognizes options on the parser they're attached to, and
    global options on the top-level parser must appear before the subcommand.
    Users naturally write `dcam claude context --catalog local`, so we
    transparently hoist those flags to the front before parse_known_args.

    Tokens after a bare `--` separator are left untouched so passthrough args
    (e.g. `dcam claude recall ID -- --resume`) keep their meaning.
    """
    out_front = []
    rest = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            rest.extend(argv[i:])
            break
        # Handle "--flag=value" form
        if any(token.startswith(f + "=") for f in _GLOBAL_FLAGS_WITH_VALUE):
            out_front.append(token)
            i += 1
            continue
        # Handle "--flag value" form
        if token in _GLOBAL_FLAGS_WITH_VALUE and i + 1 < len(argv):
            out_front.extend([token, argv[i + 1]])
            i += 2
            continue
        rest.append(token)
        i += 1
    return out_front + rest


def _resolve_session_prefix(store: DeltaStore, prefix: str):
    """Resolve a session_id prefix to a full session_id from stored sessions.

    Returns None if no match. If the prefix is already a full match, returns it
    as-is. If multiple sessions match, prints an error and returns None.
    """
    if not prefix:
        return None
    sessions = store.read_sessions()
    matches = [s.session_id for s in sessions if s.session_id == prefix]
    if matches:
        return matches[0]
    matches = [s.session_id for s in sessions if s.session_id.startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous session prefix '{prefix}', matches: {', '.join(m[:12] for m in matches)}",
              file=sys.stderr)
        return None
    return None


def cmd_init(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    full_session_id = _resolve_session_prefix(store, args.session_id)
    if full_session_id:
        args.session_id = full_session_id
    msgs = sorted(store.read_messages(args.session_id), key=lambda m: m.timestamp)
    if not msgs:
        print(f"No messages for session {args.session_id}")
        return
    for msg in msgs[-args.limit:]:
        content = ansi_escape.sub('', msg.content).strip()
        print(f"[{msg.timestamp.strftime('%H:%M:%S')}] {msg.role.value}: {content}")


def cmd_chat_search(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    results = store.search_messages(args.query, limit=args.limit)
    if not results:
        print(f"No results for '{args.query}'")
        return
    for msg in results:
        print(f"  [{msg.session_id}] {msg.role.value}: {msg.content[:120]}")


def cmd_chat_enter(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    from dcam.interactive import run_interactive
    run_interactive(store, args.session_id)


def cmd_compact(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    chunks = compact.lookup(store, args.symbol)
    if not chunks:
        print(f"No matches for '{args.symbol}'")
        return
    for c in chunks:
        print(f"  {c.chunk_type.value:8} {c.name:30} {c.file_path} L{c.start_line}-{c.end_line}")
        print(f"           {c.summary[:100]}")


def cmd_fetch(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    chunks = store.read_chunks()
    chunk = next((c for c in chunks if c.chunk_id == args.chunk_id), None)
    if not chunk:
        print(f"Chunk {args.chunk_id} not found", file=sys.stderr)
        sys.exit(1)
    code = compact.fetch_lines(chunk.file_path, chunk.start_line, chunk.end_line)
    print(f"# {chunk.name} ({chunk.chunk_type.value}) — {chunk.file_path} L{chunk.start_line}-{chunk.end_line}")
    print(code)


def cmd_resolve(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    ctx = resolver.resolve(store, args.message)
    if ctx:
        print(ctx)
    else:
        print("No relevant context found.")


def cmd_memory_add(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    m = store.add_project_memory(args.content, name=args.name, topic=args.topic, category=args.category)
    label = f" ({m.name})" if m.name else ""
    print(f"Stored project memory {m.id}{label}: {m.content[:80]}")


def cmd_memory_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    mems = store.read_project_memories()
    if not mems:
        print("No project memories stored.")
        return
    for m in mems:
        name = f"[{m.name}] " if m.name else ""
        topic = f"({m.topic}) " if m.topic else ""
        print(f"  {m.id:>4}  {name}{topic}{m.content[:100]}")


def cmd_memory_recall(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    m = store.recall_by_name(args.name)
    if m:
        print(m.content)
    else:
        print(f"No memory named '{args.name}'")


def cmd_memory_context(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    orch = Orchestrator(store, poll_interval=args.interval)
    orch.run()


def cmd_tmux_start(args):
    from dcam import tmux
    project_path = args.project or os.getcwd()
    cmd = tmux.build_manager_launch_cmd(args.claude_bin) if args.launch else None
    name = tmux.start_session(args.session, project_path, manager_cmd=cmd)
    print(f"✓ tmux session '{name}' ready (window: manager)")
    if not args.launch:
        print(f"  To start the manager agent: tmux send-keys -t {name}:manager "
              f"'{tmux.build_manager_launch_cmd(args.claude_bin)}' Enter")
    print(f"  Attach with: tmux attach -t {name}")


def cmd_tmux_dev(args):
    from dcam import tmux
    from dcam.orchestrator import create_task, list_tasks

    project_path = args.project or os.getcwd()
    slug = tmux.slugify(args.slug)
    brief = args.brief

    # --- Idempotency: reuse existing task + window for this slug -----------
    # Pre-2026-05-27 behavior was unconditionally additive: re-running
    # `dcam tmux dev <slug>` produced a second bd task and a second tmux
    # window, leaving `dcam tmux update <slug>` to pick one arbitrarily.
    # Now we look up by `role:dev,slug:<slug>` and reuse what's there.

    existing_task = None
    if not args.no_task:
        try:
            matches = list_tasks(status="open",
                                 labels=["role:dev", f"slug:{slug}"])
        except Exception:
            matches = []
        if len(matches) > 1 and not args.force:
            ids = ", ".join(t.id for t in matches)
            print(
                f"Error: multiple open dev tasks already exist for "
                f"slug:{slug} ({ids}).\n"
                f"  This is corrupted state. Close all but one, or pass "
                f"--force to add yet another.",
                file=sys.stderr,
            )
            sys.exit(1)
        if matches and not args.force:
            existing_task = matches[0]

    window_name = f"dev-{slug}"
    target = f"{args.session}:{window_name}"

    # Window may already exist if the user previously ran `dcam tmux dev`
    # for this slug. Detect and reuse.
    existing_window = (tmux._tmux_available()
                       and tmux.session_exists(args.session)
                       and window_name in tmux.list_windows(args.session))

    # Create / reuse the bd task
    task = existing_task
    if not args.no_task and not existing_task:
        task = create_task(
            f"[dev:{slug}] {brief[:60]}",
            priority=args.priority,
            labels=[f"role:dev", f"slug:{slug}"],
        )

    # Create / reuse the tmux window. spawn_dev_window is already idempotent
    # on the window name itself, but with --launch it would re-send the
    # claude command into an already-running pane. Refuse that case unless
    # --force.
    cmd = (tmux.build_dev_launch_cmd(slug, brief, args.claude_bin)
           if args.launch else None)
    if existing_window and args.launch and not args.force:
        # Don't re-send the claude command into an existing pane.
        cmd = None
        target = tmux.spawn_dev_window(args.session, slug, project_path,
                                       dev_cmd=None)
        print(f"✓ reusing dev window '{target}'")
        print(f"  ⚠ window already running; skipped --launch. Pass --force "
              f"to send the launch command anyway.")
    else:
        target = tmux.spawn_dev_window(args.session, slug, project_path,
                                       dev_cmd=cmd)
        if existing_window:
            print(f"✓ reusing dev window '{target}'")
        else:
            print(f"✓ dev window '{target}' ready")

    if task:
        if existing_task:
            print(f"  Task: {task.id} (existing, label slug:{slug})")
        else:
            print(f"  Task: {task.id} (label slug:{slug})")

    if not args.launch and not existing_window:
        print(f"  To start the dev agent: tmux send-keys -t {target} "
              f"'{tmux.build_dev_launch_cmd(slug, brief, args.claude_bin)}' Enter")


def cmd_tmux_review(args):
    from dcam import tmux
    from dcam.reviews import bootstrap_context

    project_path = args.project or os.getcwd()
    # Pull the reviewer's accumulated learning into the startup prompt
    # so each new reviewer-session knows what prior reviewers learned.
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    extra = bootstrap_context(store) if not args.no_bootstrap else None
    cmd = (tmux.build_reviewer_launch_cmd(args.claude_bin, extra_context=extra)
           if args.launch else None)
    target = tmux.spawn_review_window(args.session, project_path, review_cmd=cmd)
    print(f"✓ review window '{target}' ready")
    if extra and args.launch:
        crit_count = len([l for l in extra.split("\n") if l.startswith("- CP-")])
        find_count = len([l for l in extra.split("\n") if l.startswith("- L-")])
        queue_count = len([l for l in extra.split("\n") if l.startswith("- REQ-")])
        print(f"  Bootstrapped reviewer with {crit_count} critical points, "
              f"{find_count} prior findings, {queue_count} pending requests.")
    if not args.launch:
        print(f"  To start the reviewer: tmux send-keys -t {target} "
              f"'{tmux.build_reviewer_launch_cmd(args.claude_bin)}' Enter")


def cmd_tmux_status(args):
    from dcam import tmux
    from dcam.orchestrator import list_tasks

    if not tmux.session_exists(args.session):
        print(f"No tmux session '{args.session}'.")
        return
    print(f"Session: {args.session}")
    print(f"Windows:")
    for w in tmux.list_windows(args.session):
        marker = ""
        if w == "manager":
            marker = " [manager]"
        elif w == "review":
            marker = " [reviewer]"
        elif w.startswith("dev-"):
            marker = f" [dev:{w[4:]}]"
        print(f"  {w}{marker}")

    # Surface beads tasks tagged role:dev
    try:
        dev_tasks = list_tasks(status="open", labels=["role:dev"])
    except Exception:
        dev_tasks = []
    if dev_tasks:
        print("\nOpen dev tasks:")
        for t in dev_tasks:
            slug = next((l.split(":", 1)[1] for l in t.labels
                         if l.startswith("slug:")), "?")
            print(f"  {t.id}  P{t.priority}  slug:{slug}  {t.title[:60]}")


def cmd_tmux_send(args):
    from dcam import tmux
    tmux.send_keys(args.session, args.window, args.text,
                   press_enter=not args.no_enter)
    print(f"✓ sent {len(args.text)} chars to {args.session}:{args.window}")


def cmd_tmux_capture(args):
    from dcam import tmux
    out = tmux.capture_pane(args.session, args.window, tail=args.tail)
    print(out)


def cmd_tmux_update(args):
    from dcam.orchestrator import comment_task, list_tasks
    # Find the bd task with matching slug label
    open_tasks = list_tasks(status="open", labels=["role:dev", f"slug:{args.slug}"])
    if not open_tasks:
        print(f"No open dev task with slug '{args.slug}' found.", file=sys.stderr)
        sys.exit(1)
    task = open_tasks[0]
    comment_task(task.id, f"[status] {args.message}")
    print(f"✓ commented on {task.id}: [status] {args.message[:80]}")


def _find_dev_task(slug):
    from dcam.orchestrator import list_tasks
    tasks = list_tasks(status="open", labels=["role:dev", f"slug:{slug}"])
    return tasks[0].id if tasks else None


def cmd_tmux_ask(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    task_id = _find_dev_task(args.slug)
    d = decisions.request_decision(
        store, slug=args.slug, title=args.title, context=args.context,
        options=args.options, recommended=args.recommend,
        task_id=task_id, session_id=args.session_id,
        epic=args.epic, op=args.op, ticket=args.ticket,
    )
    scope = []
    if d.epic: scope.append(f"epic={d.epic}")
    if d.op: scope.append(f"op={d.op}")
    scope_str = f", {', '.join(scope)}" if scope else ""
    print(f"✓ requested DEC-{d.id} (slug={args.slug}, status=open{scope_str})")
    if task_id:
        print(f"  bd comment posted on {task_id}")


def cmd_tmux_decide(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    project_path = args.project or os.getcwd()
    d = decisions.decide(
        store,
        decision_id=args.id,
        supersedes=args.supersedes,
        chosen=args.choice,
        rationale=args.rationale,
        decided_by=args.decided_by,
        epic=args.epic, op=args.op, ticket=args.ticket,
        persist_target=args.persist,
        project_path=project_path if args.persist else None,
    )
    print(f"✓ DEC-{d.id} decided ({d.status.value}): chose {d.chosen}")
    if d.supersedes_id:
        print(f"  supersedes DEC-{d.supersedes_id}")
    if args.persist:
        print(f"  persisted to {args.persist}")


def cmd_tmux_decisions_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    decisions_ = store.read_decisions(status=args.status)
    if not decisions_:
        print("No decisions.")
        return
    for d in sorted(decisions_, key=lambda x: x.id or 0):
        age = (datetime.now() - d.created_at).total_seconds() / 60
        chosen = f"→ {d.chosen}" if d.chosen else ""
        print(f"  DEC-{d.id:>3}  {d.status.value:11}  slug:{d.requested_by or '-':<20}  "
              f"{d.title[:50]}  {chosen}  ({int(age)}m old)")


def cmd_tmux_decisions_show(args):
    from dcam import decisions as decmod
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    chain = store.get_decision_chain(args.id)
    if not chain:
        print(f"No decision DEC-{args.id}", file=sys.stderr)
        sys.exit(1)
    print(decmod.render_decisions_section(chain))


def cmd_tmux_lesson(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    project_path = args.project or os.getcwd()
    l = decisions.add_lesson(
        store, content=args.content, category=args.category,
        source_slug=args.slug,
        epic=args.epic, op=args.op, ticket=args.ticket,
        persist_target=args.persist,
        project_path=project_path if args.persist else None,
    )
    print(f"✓ recorded lesson #{l.id} (category={l.category or 'general'})")
    if args.persist:
        print(f"  persisted to {args.persist}")


def cmd_tmux_request_review(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    project_path = args.project or os.getcwd()
    req = reviews.request_review(
        store, slug=args.slug, notes=args.notes or "",
        scope_files=args.files or "",
        epic=args.epic, op=args.op, ticket=args.ticket,
        related_decision_ids=args.decisions or "",
        session_id=args.session_id,
        project_path=project_path,
        tmux_session=args.tmux_session,
    )
    print(f"✓ requested REQ-{req.id} (slug={args.slug}, status=pending)")
    if req.git_head:
        print(f"  HEAD: {req.git_head}")
    if args.tmux_session:
        print(f"  notification sent to {args.tmux_session}:review (best-effort)")


def cmd_tmux_reviews_pending(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    pending = store.read_review_requests(status="pending")
    claimed = store.read_review_requests(status="claimed")
    items = sorted(pending + claimed, key=lambda r: r.id or 0)
    if not items:
        print("No pending review requests.")
        return
    for r in items:
        scope_bits = []
        if r.epic: scope_bits.append(r.epic)
        if r.op: scope_bits.append(r.op)
        scope = " · ".join(scope_bits) or "(unscoped)"
        age = (datetime.now() - r.created_at).total_seconds() / 60
        marker = "[claimed]" if r.status.value == "claimed" else "[pending]"
        print(f"  REQ-{r.id:>3}  {marker:<10}  slug:{r.slug or '-':<15}  "
              f"[{scope:<25}]  ({int(age)}m old)  {r.notes[:60]}")


def cmd_tmux_reviews_show(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    reqs = store.read_review_requests()
    target = next((r for r in reqs if r.id == args.id), None)
    if not target:
        print(f"No review request id={args.id}", file=sys.stderr)
        sys.exit(1)
    print(f"REQ-{target.id}  status={target.status.value}")
    print(f"  slug:        {target.slug or '-'}")
    print(f"  epic/op:     {target.epic or '-'} / {target.op or '-'}")
    print(f"  ticket:      {target.ticket or '-'}")
    print(f"  git_head:    {target.git_head or '-'}")
    print(f"  files:       {target.scope_files or '-'}")
    print(f"  decisions:   {target.related_decision_ids or '-'}")
    print(f"  created_at:  {target.created_at.isoformat()}")
    if target.claimed_by:
        print(f"  claimed_by:  {target.claimed_by} at {target.claimed_at}")
    if target.completed_at:
        print(f"  completed:   {target.completed_at.isoformat()}")
    if target.notes:
        print(f"\nNotes:\n  {target.notes}")
    # If completed, surface the matching Review.
    if target.status.value == "done":
        review = next((rv for rv in store.read_reviews()
                       if rv.request_id == target.id), None)
        if review:
            print(f"\nReview RV-{review.id} (by {review.reviewer}):")
            print(f"  {review.blocking_findings} blocking, "
                  f"{review.advisory_findings} advisory")
            if review.lessons_added:
                print(f"  Lessons added: {review.lessons_added}")
            if review.critical_added:
                print(f"  Critical points added: {review.critical_added}")
            print(f"\n{review.summary}")


def cmd_tmux_reviews_claim(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    try:
        req = reviews.claim_review_request(store, args.id, claimed_by=args.by)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ claimed REQ-{req.id} as {req.claimed_by}")


def cmd_tmux_reviews_complete(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    project_path = args.project or os.getcwd()
    lessons_added = []
    critical_added = []
    if args.lessons_added:
        lessons_added = [int(x) for x in args.lessons_added.split(",") if x.strip()]
    if args.critical_added:
        critical_added = [int(x) for x in args.critical_added.split(",") if x.strip()]
    try:
        rv = reviews.complete_review(
            store, request_id=args.id, summary=args.summary,
            blocking_findings=args.blocking, advisory_findings=args.advisory,
            lessons_added=lessons_added, critical_added=critical_added,
            reviewer=args.by,
            project_path=project_path if args.persist else None,
            persist_target=args.persist,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ completed REQ-{args.id} → RV-{rv.id} "
          f"({rv.blocking_findings} blocking, {rv.advisory_findings} advisory)")
    if args.persist:
        print(f"  persisted to {args.persist}")


def cmd_tmux_reviews_list(args):
    """List all completed reviews (audit trail)."""
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    rs = store.read_reviews()
    if not rs:
        print("No reviews recorded yet.")
        return
    for rv in sorted(rs, key=lambda r: r.id or 0):
        scope_bits = []
        if rv.epic: scope_bits.append(rv.epic)
        if rv.op: scope_bits.append(rv.op)
        scope = " · ".join(scope_bits) or "(unscoped)"
        date = rv.created_at.strftime("%Y-%m-%d %H:%M")
        print(f"  RV-{rv.id:>3}  REQ-{rv.request_id:>3}  {rv.reviewer:<15}  "
              f"[{scope:<25}]  {rv.blocking_findings}b/{rv.advisory_findings}a  {date}")


def cmd_tmux_reviews_withdraw(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    try:
        req = reviews.withdraw_review_request(store, args.id, reason=args.reason or "")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ withdrew REQ-{req.id}")


def cmd_tmux_handoff_create(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    project_path = args.project or os.getcwd()
    h = reviews.create_handoff(
        store, from_slug=args.from_slug, to_slug=args.to_slug,
        files=args.files or "", notes=args.notes or "",
        epic=args.epic, op=args.op, ticket=args.ticket,
        project_path=project_path if args.persist else None,
        persist_target=args.persist,
        tmux_session=args.tmux_session,
    )
    print(f"✓ handoff HO-{h.id}: {h.from_slug} → {h.to_slug}")
    if args.persist:
        print(f"  persisted to {args.persist}")


def cmd_tmux_handoff_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    handoffs = store.read_handoffs(status=args.status)
    if not handoffs:
        print("No handoffs.")
        return
    for h in sorted(handoffs, key=lambda x: x.id or 0):
        scope_bits = []
        if h.epic: scope_bits.append(h.epic)
        if h.op: scope_bits.append(h.op)
        scope = " · ".join(scope_bits) or "(unscoped)"
        date = h.created_at.strftime("%Y-%m-%d")
        print(f"  HO-{h.id:>3}  [{h.status.value:<13}]  "
              f"{h.from_slug:>10} → {h.to_slug:<10}  [{scope:<20}]  {date}  "
              f"{h.notes[:50]}")


def cmd_tmux_handoff_ack(args):
    from dcam import reviews
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    try:
        h = reviews.acknowledge_handoff(store, args.id, ack_notes=args.notes)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ HO-{h.id} acknowledged")
    if args.persist:
        from dcam.decisions import persist_to_project
        project_path = args.project or os.getcwd()
        persist_to_project(store, project_path, target=args.persist)
        print(f"  persisted to {args.persist}")


def _hash_file(path):
    import hashlib
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def cmd_tmux_spec_add(args):
    from pathlib import Path
    from dcam.models import Spec
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()

    target = Path(args.path).resolve()
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        sys.exit(1)
    rel = os.path.relpath(target, args.repo or os.getcwd())

    # If a spec with this path already exists, update its hash; else append.
    existing = next((s for s in store.read_specs() if s.path == rel), None)
    new_hash = _hash_file(target)
    if existing:
        existing.content_hash = new_hash
        existing.epic = args.epic if args.epic is not None else existing.epic
        existing.op = args.op if args.op is not None else existing.op
        existing.title = args.title if args.title is not None else existing.title
        existing.last_synced_at = datetime.now()
        existing.updated_at = datetime.now()
        store.update_spec(existing)
        s = existing
    else:
        s = Spec(path=rel, title=args.title or target.stem,
                 content_hash=new_hash, epic=args.epic, op=args.op,
                 last_synced_at=datetime.now())
        s = store.append_spec(s)
    print(f"✓ spec SP-{s.id}: {s.path}  hash={s.content_hash[:12]}")


def cmd_tmux_spec_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    specs = store.read_specs()
    if not specs:
        print("No specs registered.")
        return
    for s in sorted(specs, key=lambda x: x.id or 0):
        scope_bits = []
        if s.epic: scope_bits.append(s.epic)
        if s.op: scope_bits.append(s.op)
        scope = " · ".join(scope_bits) or "(unscoped)"
        link = f" → DEC-{s.last_linked_decision_id}" if s.last_linked_decision_id else ""
        print(f"  SP-{s.id:>3}  [{scope:<20}]  {s.path}{link}  "
              f"hash={s.content_hash[:12] if s.content_hash else '?'}")


def cmd_tmux_spec_ref(args):
    """Link a decision to a spec; persist appends a NEEDS-UPDATE marker."""
    from pathlib import Path
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    specs = store.read_specs()
    target = next((s for s in specs if s.id == args.spec_id), None)
    if not target:
        print(f"No spec id={args.spec_id}", file=sys.stderr)
        sys.exit(1)
    target.last_linked_decision_id = args.decision_id
    target.updated_at = datetime.now()
    store.update_spec(target)

    # Append a NEEDS-UPDATE marker to the spec file so the next reader sees it.
    repo_root = args.repo or os.getcwd()
    spec_path = Path(repo_root) / target.path
    if spec_path.exists():
        text = spec_path.read_text()
        marker = f"\n<!-- DCAM:NEEDS-UPDATE: DEC-{args.decision_id} -->\n"
        if marker.strip() not in text:
            if not text.endswith("\n"):
                text += "\n"
            spec_path.write_text(text + marker)
            print(f"✓ SP-{target.id} linked to DEC-{args.decision_id}; "
                  f"NEEDS-UPDATE marker appended to {target.path}")
        else:
            print(f"✓ SP-{target.id} linked to DEC-{args.decision_id} "
                  f"(marker already present)")
    else:
        print(f"⚠ SP-{target.id} linked to DEC-{args.decision_id}, but "
              f"{target.path} not found in repo; skipped marker")


def cmd_tmux_spec_drift(args):
    """List specs whose on-disk hash differs from the recorded hash, OR
    specs that contain unresolved NEEDS-UPDATE markers."""
    from pathlib import Path
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    specs = store.read_specs()
    repo_root = Path(args.repo or os.getcwd())
    drifted = []
    pending_updates = []
    for s in specs:
        path = repo_root / s.path
        if not path.exists():
            drifted.append((s, "missing"))
            continue
        actual_hash = _hash_file(path)
        if actual_hash != s.content_hash:
            drifted.append((s, "content-changed"))
        # NEEDS-UPDATE markers
        text = path.read_text(errors="replace")
        for line in text.splitlines():
            if "DCAM:NEEDS-UPDATE:" in line:
                pending_updates.append((s, line.strip()))

    if drifted:
        print("Drift detected (on-disk hash != recorded hash):")
        for s, reason in drifted:
            print(f"  SP-{s.id:>3}  {reason:<15}  {s.path}")
            print(f"       run `dcam tmux spec add {s.path}` to refresh")
    if pending_updates:
        print("\nPending NEEDS-UPDATE markers (decisions to reconcile in the spec):")
        for s, line in pending_updates:
            print(f"  SP-{s.id:>3}  {s.path}: {line}")
    if not drifted and not pending_updates:
        print("No drift; all specs match their recorded hash and have no "
              "unresolved NEEDS-UPDATE markers.")


def cmd_tmux_critical_add(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    store.init_tables()
    project_path = args.project or os.getcwd()
    cp = decisions.add_critical_point(
        store, content=args.content, rationale=args.rationale,
        epic=args.epic, op=args.op, ticket=args.ticket,
        source_slug=args.slug,
        persist_target=args.persist,
        project_path=project_path if args.persist else None,
    )
    print(f"✓ recorded critical point CP-{cp.id}")
    if args.persist:
        print(f"  persisted to {args.persist}")


def cmd_tmux_critical_list(args):
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    points = store.read_critical_points(status=args.status)
    if not points:
        print("No critical points.")
        return
    for p in sorted(points, key=lambda x: x.id or 0):
        scope = []
        if p.epic: scope.append(f"epic={p.epic}")
        if p.op: scope.append(f"op={p.op}")
        scope_str = " ".join(scope) or "(unscoped)"
        marker = "" if p.status.value == "active" else f" [{p.status.value}]"
        print(f"  CP-{p.id:>3}{marker}  {scope_str:<35}  {p.content[:80]}")


def cmd_tmux_critical_retire(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    try:
        cp = decisions.retire_critical_point(store, args.id, reason=args.reason or "")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ CP-{cp.id} retired")
    if args.persist:
        project_path = args.project or os.getcwd()
        decisions.persist_to_project(store, project_path, target=args.persist)
        print(f"  persisted to {args.persist}")


def cmd_tmux_digest(args):
    """Aggregate per-dev status, open decisions, recent lessons, and critical
    points into a single 'standup' view."""
    from dcam import tmux as tmux_mod
    from dcam.orchestrator import list_tasks, _run_bd
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))

    print("DCAM digest")
    print("=" * 60)

    # Per-dev status: pull each dev task + its most recent [status] comment.
    dev_tasks = []
    try:
        dev_tasks = list_tasks(status="open", labels=["role:dev"])
    except Exception:
        pass
    if dev_tasks:
        print("\nActive dev tasks:")
        for t in sorted(dev_tasks, key=lambda x: x.id):
            slug = next((l.split(":", 1)[1] for l in t.labels
                         if l.startswith("slug:")), "?")
            # Best-effort fetch of latest [status] comment
            latest_status = ""
            try:
                show = _run_bd(["show", t.id, "--json"])
                if isinstance(show, dict):
                    comments = show.get("comments", []) or []
                    for c in reversed(comments):
                        body = (c.get("body") or "").strip()
                        if body.startswith("[status]"):
                            latest_status = body[len("[status]"):].strip()[:80]
                            break
            except Exception:
                pass
            tail = f"  → {latest_status}" if latest_status else ""
            print(f"  {t.id}  slug:{slug:<20}  {t.title[:50]}{tail}")
    else:
        print("\nActive dev tasks: (none)")

    # Open decisions, grouped by scope
    open_decisions = store.read_decisions(status="open")
    print(f"\nOpen decisions: {len(open_decisions)}")
    if open_decisions:
        from dcam.decisions import _scope_label
        from collections import defaultdict
        grouped = defaultdict(list)
        for d in open_decisions:
            grouped[_scope_label(d.epic, d.op)].append(d)
        for scope in sorted(grouped):
            print(f"  {scope}:")
            for d in grouped[scope]:
                age_min = (datetime.now() - d.created_at).total_seconds() / 60
                print(f"    DEC-{d.id:>3}  slug:{d.requested_by or '-':<15}  "
                      f"{d.title[:55]}  ({int(age_min)}m old)")

    # Recent lessons (last N)
    lessons = store.read_lessons()
    if lessons:
        recent = sorted(lessons, key=lambda l: l.created_at, reverse=True)[:args.recent_lessons]
        print(f"\nRecent lessons ({len(recent)} of {len(lessons)} total):")
        for l in recent:
            scope = []
            if l.epic: scope.append(l.epic)
            if l.op: scope.append(l.op)
            scope_str = " · ".join(scope) if scope else "(unscoped)"
            print(f"  L-{l.id:>3}  [{scope_str}]  {l.content[:80]}")

    # Critical points by scope
    crits = store.read_critical_points(status="active")
    if crits:
        from collections import defaultdict
        from dcam.decisions import _scope_label
        grouped = defaultdict(int)
        for p in crits:
            grouped[_scope_label(p.epic, p.op)] += 1
        print(f"\nActive critical points: {len(crits)}")
        for scope, n in sorted(grouped.items()):
            print(f"  {scope}: {n}")


def cmd_tmux_persist(args):
    from dcam import decisions
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    project_path = args.project or os.getcwd()
    paths = decisions.persist_to_project(store, project_path, target=args.target)
    if not paths:
        if args.target == "auto":
            print("No files with DCAM markers found. Run `dcam tmux persist "
                  "--target claude` (or agents/both) once to opt CLAUDE.md/"
                  "AGENTS.md in.")
        else:
            print(f"No files written.")
        return
    for p in paths:
        print(f"✓ wrote {p}")


def cmd_tmux_msg(args):
    from dcam import tmux
    from dcam.orchestrator import comment_task
    # Live forward via tmux send-keys
    notice = f"[msg from dev:{args.from_slug}] {args.text}"
    if args.session:
        try:
            tmux.send_keys(args.session, args.to_slug, notice)
        except Exception as e:
            print(f"  (tmux send skipped: {e})", file=sys.stderr)
    # Persistent record on receiver's bd task
    receiver_task = _find_dev_task(args.to_slug)
    if receiver_task:
        comment_task(receiver_task, notice)
        print(f"✓ msg sent to dev:{args.to_slug} (bd: {receiver_task})")
    else:
        print(f"  (no open bd task for dev:{args.to_slug})")


def cmd_tmux_dep(args):
    from dcam.orchestrator import add_dependency
    blocker_id = _find_dev_task(args.blocker)
    blocked_id = _find_dev_task(args.blocked)
    if not (blocker_id and blocked_id):
        print(f"Need open dev tasks for both slugs (blocker={blocker_id}, "
              f"blocked={blocked_id})", file=sys.stderr)
        sys.exit(1)
    if add_dependency(blocker_id, blocked_id):
        print(f"✓ {args.blocked} now blocked by {args.blocker} "
              f"({blocked_id} ← {blocker_id})")
    else:
        print("Failed to add dependency", file=sys.stderr)
        sys.exit(1)


def cmd_tmux_deps(args):
    from dcam.orchestrator import list_tasks
    target_id = _find_dev_task(args.slug)
    if not target_id:
        print(f"No open dev task for slug '{args.slug}'", file=sys.stderr)
        sys.exit(1)
    # bd doesn't expose `dep show` cleanly through our wrapper; surface via list
    tasks = list_tasks(status="open", labels=["role:dev"])
    print(f"All open dev tasks (use `bd show {target_id}` for full dep tree):")
    for t in tasks:
        slug = next((l.split(":", 1)[1] for l in t.labels if l.startswith("slug:")), "?")
        marker = " ← target" if t.id == target_id else ""
        print(f"  {t.id}  slug:{slug:<20}  {t.title[:60]}{marker}")


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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    tasks = plan_session(store, args.session_id)
    if not tasks:
        print("Failed to plan. Check session ID and kiro-cli availability.")
        return
    print(f"Created {len(tasks)} tasks:")
    for t in tasks:
        dep = " (plan)" if "type:plan" in t.labels else ""
        print(f"  {t.id}  {t.title}{dep}")


def cmd_claude_init(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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
        sid = s['session_id'] if args.full_id else s['session_id'][:12]
        print(f"  {sid}  {s.get('started_at', 'N/A')[:16]}  "
              f"{s['message_count']:>3} msgs  {s['project']}")


def cmd_claude_recall(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    # Resolve session_id prefix against stored sessions
    full_session_id = _resolve_session_prefix(store, args.session_id)
    if full_session_id:
        args.session_id = full_session_id
    msgs = store.read_messages(args.session_id)
    if not msgs:
        # Try syncing first
        project_path = args.project or os.getcwd()
        sessions = claude_code.list_sessions(project_path)
        match = next((s for s in sessions if s["session_id"].startswith(args.session_id)), None)
        if match:
            claude_code.sync_session(store, match["path"])
            args.session_id = match["session_id"]
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
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
    results = store.search_messages(args.query, limit=args.limit)
    if not results:
        print(f"No results for '{args.query}'")
        return
    for msg in results:
        role = "User" if msg.role == MessageRole.USER else "Asst"
        print(f"  [{msg.session_id[:8]}] {role}: {msg.content[:120]}")


def cmd_claude_context(args):
    store = get_store(args.namespace, args.search_backend, args.catalog, getattr(args, "branch", "main"), getattr(args, "root", None))
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


def cmd_claude_extract(args):
    from dcam import extract
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    storage_root = store.storage_root

    # Resolve session prefix the same way recall does
    full = _resolve_session_prefix(store, args.session_id)
    if full:
        args.session_id = full

    cands = extract.extract_candidates_from_session(
        store, args.session_id, role=args.role)
    if not cands:
        print(f"No candidates found in session {args.session_id}.")
        return

    path = extract.write_candidates(storage_root, cands)
    print(f"✓ {len(cands)} candidate(s) written to {path}")
    by_kind = {}
    for c in cands:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    for k, n in sorted(by_kind.items()):
        print(f"    {k}: {n}")
    print()
    print("Review them with:  dcam claude extract-review")


def cmd_claude_extract_review(args):
    """Interactive accept/edit/reject loop for stored candidates."""
    from dcam import extract
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), getattr(args, "root", None))
    storage_root = store.storage_root
    candidates = extract.load_candidates(storage_root)
    pending = [c for c in candidates if c.get("status", "candidate") == "candidate"]
    if not pending:
        print("No pending candidates to review.")
        return
    print(f"Reviewing {len(pending)} candidate(s). "
          f"For each: [a]ccept, [s]kip, [r]eject, [q]uit.\n")
    project_path = args.project or os.getcwd()
    promoted = 0
    for cand in pending:
        print("─" * 60)
        print(f"  kind:       {cand['kind']}")
        print(f"  match:      {cand.get('match_type', '?')}")
        print(f"  source:     {cand.get('source_role')} in "
              f"{cand.get('source_session_id', '?')[:12]}")
        print(f"  content:    {cand['content']}")
        try:
            choice = input("  > [a/s/r/q] ").strip().lower()
        except EOFError:
            choice = "q"
        if choice == "q":
            break
        if choice == "r":
            cand["status"] = "rejected"
            continue
        if choice == "s":
            continue
        if choice == "a":
            epic = input("  epic [optional]: ").strip() or None
            op = input("  op [optional]:   ").strip() or None
            try:
                result = extract.promote_candidate(
                    store, cand, epic=epic, op=op,
                    project_path=project_path,
                    persist_target=args.persist,
                )
                cand["status"] = "accepted"
                cand["promoted_to"] = result
                promoted += 1
                print(f"  ✓ promoted to {result['kind']} #{result['id']}")
            except Exception as e:
                print(f"  ✗ promote failed: {e}")
        else:
            print(f"  (unknown choice; skipping)")
    extract.save_candidates(storage_root, candidates)
    print(f"\nPromoted {promoted} candidate(s).")


def cmd_project_init(args):
    from dcam.project import init_project, discover_root, is_project_root
    repo = args.repo or os.getcwd()
    root = init_project(repo, namespace=args.namespace, force=args.force)
    print(f"✓ DCAM project initialized at {root}")
    print(f"  Committed:   decisions.json, lessons.json, sessions.json")
    print(f"  Local-only:  tables/{args.namespace}/  (gitignored)")
    print(f"  Hook source: hooks/pre-commit  (committed; install per clone)")
    print()
    print("Next steps:")
    print(f"  1. Review {root}/.gitignore and {root}/README.md")
    print(f"  2. git add {root}/ && git commit -m 'Initialize DCAM memory'")
    print(f"  3. dcam project install-hook   "
          f"# enable auto-persist on git commit")
    print(f"  4. From anywhere in the repo: dcam tmux ask … / decide … / lesson …")
    # Touch the catalog so empty tables get created with the right shape.
    store = get_store(args.namespace, args.search_backend, args.catalog,
                      getattr(args, "branch", "main"), str(root))
    store.init_tables()


def cmd_project_status(args):
    from dcam.project import discover_root, is_project_root
    root = discover_root(args.root)
    project_mode = is_project_root(root)
    print(f"Storage root: {root}")
    print(f"Mode:         {'project (committable)' if project_mode else 'global (~/.dcam/)'}")
    if project_mode:
        for name in ("decisions.json", "lessons.json", "sessions.json"):
            f = root / name
            if f.exists():
                size = f.stat().st_size
                # Count records cheaply
                try:
                    import json
                    rows = len(json.loads(f.read_text() or "[]"))
                except Exception:
                    rows = "?"
                print(f"  {name:<20} {rows:>4} rows ({size} bytes)")
            else:
                print(f"  {name:<20}  (missing — run `dcam project init`)")
        tables_dir = root / "tables"
        if tables_dir.exists():
            for ns_dir in sorted(tables_dir.iterdir()):
                if not ns_dir.is_dir():
                    continue
                pq_files = list(ns_dir.glob("*.parquet"))
                total = sum(p.stat().st_size for p in pq_files)
                print(f"  tables/{ns_dir.name}/  {len(pq_files)} parquet files "
                      f"({total} bytes)  [local]")
    else:
        # Global mode: surface the parquet tables at ~/.dcam/tables/.
        tables_dir = root / "tables"
        if tables_dir.exists():
            for ns_dir in sorted(tables_dir.iterdir()):
                if ns_dir.is_dir():
                    pq_files = list(ns_dir.glob("*.parquet"))
                    print(f"  tables/{ns_dir.name}/  {len(pq_files)} parquet files")


def cmd_project_path(args):
    from dcam.project import discover_root
    print(discover_root(args.root))


def cmd_project_install_hook(args):
    from dcam.project import install_hook
    repo = args.repo or os.getcwd()
    try:
        target = install_hook(repo, force=args.force)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ pre-commit hook installed at {target}")
    print(f"  → linked to .dcam/hooks/pre-commit")
    print(f"  Auto-runs `dcam tmux persist --target auto` whenever a")
    print(f"  committed DCAM JSON file is staged. Skip with --no-verify.")


def cmd_project_uninstall_hook(args):
    from dcam.project import uninstall_hook
    repo = args.repo or os.getcwd()
    removed = uninstall_hook(repo)
    if removed:
        print(f"✓ removed {removed}")
    else:
        print("No DCAM-installed pre-commit hook found (nothing changed).")


def main():
    p = argparse.ArgumentParser(prog="dcam", description="DeltaCAT Agent Memory")
    p.add_argument("--namespace", default="dcam")
    p.add_argument("--search-backend", default="bm25", choices=["bm25", "substring"],
                   help="Search algorithm (default: bm25)")
    p.add_argument("--catalog", default="local", choices=["local", "delta", "branch", "deltacat"],
                   help="Storage backend: local, delta, branch (branched delta), or deltacat")
    p.add_argument("--branch", default="main",
                   help="Branch name for branch backend (default: main)")
    p.add_argument("--root", default=None,
                   help="DCAM storage root (overrides DCAM_ROOT and the "
                        ".dcam/ walk-up discovery)")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init")
    sub.add_parser("status")

    # Project (committable, .dcam/) subcommands
    proj_p = sub.add_parser("project",
                            help="Project-scoped, committable memory (.dcam/)")
    psub = proj_p.add_subparsers(dest="project_cmd")
    pi = psub.add_parser("init", help="Create .dcam/ in the target repo")
    pi.add_argument("--repo", default=None,
                    help="Repo root (default: cwd)")
    pi.add_argument("--force", action="store_true",
                    help="Overwrite existing README/.gitignore/empty JSON files")
    psub.add_parser("status",
                    help="Show the active root and what is stored")
    psub.add_parser("path",
                    help="Print the active DCAM storage root")
    pih = psub.add_parser(
        "install-hook",
        help="Symlink .git/hooks/pre-commit → .dcam/hooks/pre-commit "
             "so committed JSON changes auto-regenerate CLAUDE.md/AGENTS.md")
    pih.add_argument("--repo", default=None, help="Repo root (default: cwd)")
    pih.add_argument("--force", action="store_true",
                     help="Overwrite an existing non-DCAM pre-commit hook")
    puh = psub.add_parser("uninstall-hook",
                          help="Remove the DCAM pre-commit symlink "
                               "(no-op if it isn't ours)")
    puh.add_argument("--repo", default=None)

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
    ccl.add_argument("--full-id", action="store_true", help="Print full session UUIDs")
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

    ccex = ccsub.add_parser("extract",
                            help="Heuristically extract lesson/decision/critical "
                                 "candidates from a session transcript")
    ccex.add_argument("session_id")
    ccex.add_argument("--role", default="any",
                      choices=["any", "user", "assistant"])
    ccrv = ccsub.add_parser("extract-review",
                            help="Interactively accept/edit/reject candidates "
                                 "and promote accepted ones to real records")
    ccrv.add_argument("--project", default=None)
    ccrv.add_argument("--persist", choices=["claude", "agents", "both"],
                      default=None,
                      help="Persist promoted records to CLAUDE.md/AGENTS.md")

    # orchestrate
    orch_p = sub.add_parser("orchestrate", help="Start orchestration loop")
    orch_p.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    # tmux multi-agent subcommands
    tmux_p = sub.add_parser("tmux", help="Multi-agent coordination via tmux")
    txsub = tmux_p.add_subparsers(dest="tmux_cmd")

    tx_start = txsub.add_parser("start", help="Create tmux session with manager window")
    tx_start.add_argument("session", help="tmux session name")
    tx_start.add_argument("--project", default=None, help="Project path (default: cwd)")
    tx_start.add_argument("--launch", action="store_true",
                          help="Auto-launch the manager agent in the window")
    tx_start.add_argument("--claude-bin", default="claude",
                          help="Path/name of the claude binary")

    tx_dev = txsub.add_parser("dev", help="Spawn a dev window")
    tx_dev.add_argument("session", help="tmux session name")
    tx_dev.add_argument("slug", help="Short task identifier (slugified)")
    tx_dev.add_argument("brief", help="One-line task description for the dev")
    tx_dev.add_argument("--project", default=None)
    tx_dev.add_argument("--launch", action="store_true",
                        help="Auto-launch the dev agent in the window")
    tx_dev.add_argument("--claude-bin", default="claude")
    tx_dev.add_argument("--priority", "-p", type=int, default=1)
    tx_dev.add_argument("--no-task", action="store_true",
                        help="Skip creating a beads task for this dev")
    tx_dev.add_argument("--force", action="store_true",
                        help="Add a duplicate task/window even if one exists "
                             "for this slug, or re-send --launch into a "
                             "running pane. Rarely what you want.")

    tx_review = txsub.add_parser("review",
                                  help="Spawn the long-lived reviewer window")
    tx_review.add_argument("session", help="tmux session name")
    tx_review.add_argument("--project", default=None)
    tx_review.add_argument("--launch", action="store_true")
    tx_review.add_argument("--claude-bin", default="claude")
    tx_review.add_argument("--no-bootstrap", action="store_true",
                           help="Skip injecting prior critical points + "
                                "review-finding lessons into the startup prompt")

    tx_status = txsub.add_parser("status", help="Show tmux windows + open dev tasks")
    tx_status.add_argument("session", help="tmux session name")

    tx_send = txsub.add_parser("send", help="Send text to a window's pane")
    tx_send.add_argument("session"); tx_send.add_argument("window")
    tx_send.add_argument("text")
    tx_send.add_argument("--no-enter", action="store_true")

    tx_cap = txsub.add_parser("capture", help="Capture a window's pane buffer")
    tx_cap.add_argument("session"); tx_cap.add_argument("window")
    tx_cap.add_argument("--tail", type=int, default=200)

    tx_upd = txsub.add_parser("update", help="Post a milestone status to the dev's beads task")
    tx_upd.add_argument("slug"); tx_upd.add_argument("message")

    tx_ask = txsub.add_parser("ask", help="Dev requests a manager decision")
    tx_ask.add_argument("slug")
    tx_ask.add_argument("title")
    tx_ask.add_argument("--context", default="")
    tx_ask.add_argument("--options", default="",
                        help="Either 'A:summary|B:summary' or JSON list")
    tx_ask.add_argument("--recommend", default=None, help="Recommended option key")
    tx_ask.add_argument("--session-id", default=None)
    tx_ask.add_argument("--epic", default=None,
                        help="Epic scope, e.g. 'native-read'")
    tx_ask.add_argument("--op", default=None,
                        help="Per-op scope, e.g. 'CreateProvider'")
    tx_ask.add_argument("--ticket", default=None,
                        help="External ticket URL (any tracker)")

    tx_dec = txsub.add_parser("decide", help="Manager resolves a decision")
    tx_dec.add_argument("--id", type=int, default=None,
                        help="Existing open decision to resolve")
    tx_dec.add_argument("--supersedes", type=int, default=None,
                        help="Older decision id to supersede with a new revision")
    tx_dec.add_argument("--choice", required=True)
    tx_dec.add_argument("--rationale", required=True)
    tx_dec.add_argument("--decided-by", default="manager")
    tx_dec.add_argument("--epic", default=None,
                        help="Override / set epic scope")
    tx_dec.add_argument("--op", default=None,
                        help="Override / set per-op scope")
    tx_dec.add_argument("--ticket", default=None,
                        help="Override / set ticket URL")
    tx_dec.add_argument("--persist", choices=["claude", "agents", "both"],
                        default=None)
    tx_dec.add_argument("--project", default=None)

    tx_decs = txsub.add_parser("decisions", help="List/show decisions")
    decsub = tx_decs.add_subparsers(dest="dec_cmd")
    dl = decsub.add_parser("list")
    dl.add_argument("--status", default=None,
                    choices=["open", "decided", "superseded", "withdrawn"])
    ds = decsub.add_parser("show")
    ds.add_argument("id", type=int)

    tx_les = txsub.add_parser("lesson", help="Record a lesson learnt")
    tx_les.add_argument("content")
    # Free-form: common values are design/testing/ops/process/review-finding,
    # but agents may invent new ones. Don't constrain via `choices`.
    tx_les.add_argument("--category", default=None,
                        help="design | testing | ops | process | review-finding | …")
    tx_les.add_argument("--slug", default=None, help="Source dev slug")
    tx_les.add_argument("--epic", default=None)
    tx_les.add_argument("--op", default=None)
    tx_les.add_argument("--ticket", default=None)
    tx_les.add_argument("--persist", choices=["claude", "agents", "both"],
                        default=None)
    tx_les.add_argument("--project", default=None)

    tx_crit = txsub.add_parser("critical",
                               help="Forward-looking invariants the reviewer enforces")
    cpsub = tx_crit.add_subparsers(dest="cp_cmd")
    cpa = cpsub.add_parser("add", help="Record a critical point")
    cpa.add_argument("content")
    cpa.add_argument("--rationale", default=None,
                     help="Why this point matters")
    cpa.add_argument("--epic", default=None)
    cpa.add_argument("--op", default=None)
    cpa.add_argument("--ticket", default=None)
    cpa.add_argument("--slug", default=None, help="Source dev slug")
    cpa.add_argument("--persist", choices=["claude", "agents", "both"],
                     default=None)
    cpa.add_argument("--project", default=None)
    cpl = cpsub.add_parser("list", help="List critical points")
    cpl.add_argument("--status", default=None,
                     choices=["active", "retired"])
    cpr = cpsub.add_parser("retire", help="Retire a critical point")
    cpr.add_argument("id", type=int)
    cpr.add_argument("--reason", default=None)
    cpr.add_argument("--persist", choices=["claude", "agents", "both"],
                     default=None)
    cpr.add_argument("--project", default=None)

    tx_dig = txsub.add_parser("digest",
                              help="Standup-style aggregator: per-dev status, "
                                   "open decisions, recent lessons, critical points")
    tx_dig.add_argument("--recent-lessons", type=int, default=5,
                        help="How many recent lessons to surface (default: 5)")

    # Review-request workflow
    tx_rreq = txsub.add_parser("request-review",
                               help="Dev asks the reviewer for feedback")
    tx_rreq.add_argument("slug")
    tx_rreq.add_argument("--notes", default=None,
                         help="What to look at (one-liner; details in transcript)")
    tx_rreq.add_argument("--files", default=None,
                         help="Comma-separated globs the reviewer should focus on")
    tx_rreq.add_argument("--decisions", default=None,
                         help="Comma-separated DEC ids relevant to the change")
    tx_rreq.add_argument("--epic", default=None)
    tx_rreq.add_argument("--op", default=None)
    tx_rreq.add_argument("--ticket", default=None)
    tx_rreq.add_argument("--session-id", default=None)
    tx_rreq.add_argument("--project", default=None)
    tx_rreq.add_argument("--tmux-session", default=None,
                         help="If set, send a live notification into this tmux session's review window")

    tx_rev = txsub.add_parser("reviews", help="Review-request queue")
    revsub = tx_rev.add_subparsers(dest="rev_cmd")
    revsub.add_parser("pending", help="List pending + claimed requests")
    rsh = revsub.add_parser("show", help="Show a single request + its review")
    rsh.add_argument("id", type=int)
    rcl = revsub.add_parser("claim", help="Claim a pending request")
    rcl.add_argument("id", type=int)
    rcl.add_argument("--by", default="reviewer",
                     help="Identity recorded as claimed_by")
    rco = revsub.add_parser("complete", help="Close a request with a review record")
    rco.add_argument("id", type=int)
    rco.add_argument("--summary", required=True)
    rco.add_argument("--blocking", type=int, default=0)
    rco.add_argument("--advisory", type=int, default=0)
    rco.add_argument("--lessons-added", default=None,
                     help="Comma-sep lesson ids recorded during this review")
    rco.add_argument("--critical-added", default=None,
                     help="Comma-sep critical-point ids recorded during this review")
    rco.add_argument("--by", default="reviewer")
    rco.add_argument("--persist", choices=["claude", "agents", "both"],
                     default=None)
    rco.add_argument("--project", default=None)
    revsub.add_parser("list", help="Audit trail of completed reviews")
    rwd = revsub.add_parser("withdraw", help="Withdraw a pending request")
    rwd.add_argument("id", type=int)
    rwd.add_argument("--reason", default=None)

    # Handoff workflow
    tx_ho = txsub.add_parser("handoff",
                             help="Structured peer-to-peer handoff between dev slugs")
    hosub = tx_ho.add_subparsers(dest="ho_cmd")
    hoc = hosub.add_parser("create", help="Create a handoff")
    hoc.add_argument("from_slug", metavar="from")
    hoc.add_argument("to_slug", metavar="to")
    hoc.add_argument("--files", default=None,
                     help="Comma-sep paths/globs the receiver needs")
    hoc.add_argument("--notes", default=None)
    hoc.add_argument("--epic", default=None)
    hoc.add_argument("--op", default=None)
    hoc.add_argument("--ticket", default=None)
    hoc.add_argument("--persist", choices=["claude", "agents", "both"],
                     default=None)
    hoc.add_argument("--project", default=None)
    hoc.add_argument("--tmux-session", default=None,
                     help="If set, send-keys a notification into the receiver's window")
    hol = hosub.add_parser("list")
    hol.add_argument("--status", default=None,
                     choices=["pending", "acknowledged", "withdrawn"])
    hoa = hosub.add_parser("ack", help="Receiver acknowledges a handoff")
    hoa.add_argument("id", type=int)
    hoa.add_argument("--notes", default=None)
    hoa.add_argument("--persist", choices=["claude", "agents", "both"],
                     default=None)
    hoa.add_argument("--project", default=None)

    # Spec workflow
    tx_sp = txsub.add_parser("spec",
                             help="Versioned markdown spec artifacts (anywhere in the repo)")
    spsub = tx_sp.add_subparsers(dest="sp_cmd")
    spa = spsub.add_parser("add", help="Register or refresh a spec by path")
    spa.add_argument("path",
                     help="Path to a markdown file (relative to repo root or absolute)")
    spa.add_argument("--title", default=None)
    spa.add_argument("--epic", default=None)
    spa.add_argument("--op", default=None)
    spa.add_argument("--repo", default=None,
                     help="Repo root for path resolution (default: cwd)")
    spsub.add_parser("list")
    spr = spsub.add_parser("ref",
                           help="Link a decision to a spec; appends a "
                                "NEEDS-UPDATE marker into the spec file")
    spr.add_argument("spec_id", type=int)
    spr.add_argument("decision_id", type=int)
    spr.add_argument("--repo", default=None)
    spd = spsub.add_parser("drift",
                           help="List specs whose on-disk hash changed or "
                                "that contain unresolved NEEDS-UPDATE markers")
    spd.add_argument("--repo", default=None)

    tx_pst = txsub.add_parser("persist",
                              help="Render decisions+lessons into CLAUDE.md/AGENTS.md")
    tx_pst.add_argument("--target",
                        choices=["claude", "agents", "both", "auto"],
                        default="claude",
                        help="auto = only update files that already have "
                             "DCAM markers (used by the pre-commit hook)")
    tx_pst.add_argument("--project", default=None)

    tx_msg = txsub.add_parser("msg", help="Dev-to-dev message via tmux + bd")
    tx_msg.add_argument("from_slug"); tx_msg.add_argument("to_slug")
    tx_msg.add_argument("text")
    tx_msg.add_argument("--session", default=None,
                        help="tmux session for live send-keys (optional)")

    tx_depa = txsub.add_parser("dep", help="Mark <blocked> as blocked by <blocker>")
    tx_depa.add_argument("blocker"); tx_depa.add_argument("blocked")

    tx_deps = txsub.add_parser("deps", help="Show open dev tasks (with target marked)")
    tx_deps.add_argument("slug")

    # task subcommands
    task_p = sub.add_parser("task", help="Task management via beads")
    tsub = task_p.add_subparsers(dest="task_cmd")
    tc = tsub.add_parser("create"); tc.add_argument("title"); tc.add_argument("-p", "--priority", type=int, default=1)
    tc.add_argument("--labels", default=None); tc.add_argument("--session", default=None)
    tl = tsub.add_parser("list"); tl.add_argument("--status", default="open")
    tsub.add_parser("ready")
    tp = tsub.add_parser("plan"); tp.add_argument("session_id")

    # Allow global flags (--namespace/--search-backend/--catalog/--branch) to
    # appear after the subcommand by hoisting them to the front before parsing.
    argv = _hoist_global_flags(sys.argv[1:])
    args, remaining = p.parse_known_args(argv)

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
         "context": cmd_claude_context,
         "extract": cmd_claude_extract,
         "extract-review": cmd_claude_extract_review}.get(args.claude_cmd, lambda _: cc_p.print_help())(args)
    elif args.command == "branch":
        {"list": cmd_branch_list, "merge": cmd_branch_merge,
         "delete": cmd_branch_delete}.get(args.branch_cmd, lambda _: br_p.print_help())(args)
    elif args.command == "project":
        {"init": cmd_project_init, "status": cmd_project_status,
         "path": cmd_project_path,
         "install-hook": cmd_project_install_hook,
         "uninstall-hook": cmd_project_uninstall_hook}.get(
             getattr(args, "project_cmd", None),
             lambda _: proj_p.print_help())(args)
    elif args.command == "tmux":
        try:
            tmux_dispatch = {
                "start": cmd_tmux_start, "dev": cmd_tmux_dev,
                "review": cmd_tmux_review, "status": cmd_tmux_status,
                "send": cmd_tmux_send, "capture": cmd_tmux_capture,
                "update": cmd_tmux_update,
                "ask": cmd_tmux_ask, "decide": cmd_tmux_decide,
                "lesson": cmd_tmux_lesson, "persist": cmd_tmux_persist,
                "msg": cmd_tmux_msg, "dep": cmd_tmux_dep, "deps": cmd_tmux_deps,
                "digest": cmd_tmux_digest,
                "request-review": cmd_tmux_request_review,
            }
            if args.tmux_cmd == "decisions":
                {"list": cmd_tmux_decisions_list, "show": cmd_tmux_decisions_show}.get(
                    getattr(args, "dec_cmd", None),
                    lambda _: tx_decs.print_help())(args)
            elif args.tmux_cmd == "critical":
                {"add": cmd_tmux_critical_add,
                 "list": cmd_tmux_critical_list,
                 "retire": cmd_tmux_critical_retire}.get(
                    getattr(args, "cp_cmd", None),
                    lambda _: tx_crit.print_help())(args)
            elif args.tmux_cmd == "reviews":
                {"pending": cmd_tmux_reviews_pending,
                 "show": cmd_tmux_reviews_show,
                 "claim": cmd_tmux_reviews_claim,
                 "complete": cmd_tmux_reviews_complete,
                 "list": cmd_tmux_reviews_list,
                 "withdraw": cmd_tmux_reviews_withdraw}.get(
                    getattr(args, "rev_cmd", None),
                    lambda _: tx_rev.print_help())(args)
            elif args.tmux_cmd == "handoff":
                {"create": cmd_tmux_handoff_create,
                 "list": cmd_tmux_handoff_list,
                 "ack": cmd_tmux_handoff_ack}.get(
                    getattr(args, "ho_cmd", None),
                    lambda _: tx_ho.print_help())(args)
            elif args.tmux_cmd == "spec":
                {"add": cmd_tmux_spec_add,
                 "list": cmd_tmux_spec_list,
                 "ref": cmd_tmux_spec_ref,
                 "drift": cmd_tmux_spec_drift}.get(
                    getattr(args, "sp_cmd", None),
                    lambda _: tx_sp.print_help())(args)
            else:
                tmux_dispatch.get(args.tmux_cmd,
                                  lambda _: tmux_p.print_help())(args)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.command in cmds:
        cmds[args.command](args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
