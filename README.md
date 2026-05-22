# DeltaCATAgentMemory (dcam)

Persistent agent memory that survives across chat sessions. Works with **Claude Code** and **Kiro**, backed by **DeltaCAT** and **Beads**.

## Problem

AI chat sessions are ephemeral — when you close a chat, all context is lost. You start over every time, re-explaining your project, re-reading files, and losing track of what was done.

## Solution

`dcam` persists everything locally:
- **Claude Code integration** — auto-syncs every Claude Code session to a queryable store via a `SessionEnd` hook; loads structured per-session summaries into the next session via `dcam claude context`
- **Kiro integration** — pre-tool hook + AGENTS.md instructions so Kiro agents use compaction instead of reading whole files
- **Chat history** — every message stored and searchable (BM25 ranked) across sessions
- **Project memory** — named, cross-session facts recalled by name from any conversation
- **Long-term memory** — key facts extracted and recalled with Ebbinghaus decay
- **Code index** — files summarized into compact chunks, fetched on demand instead of loading full files into context
- **Decisions and lessons** — first-class records for architectural decisions (with supersession audit trail) and cross-session learnings, rendered as managed sections of `CLAUDE.md`/`AGENTS.md`
- **Multi-agent over tmux** — manager / dev / long-running reviewer workflow with pull-based review requests, structured handoffs, peer-to-peer messaging, decision asks, dep tracking, and a digest view ([TMUX.md](TMUX.md))
- **Specs as versioned artifacts** — register markdown specs anywhere in the repo; DCAM tracks content hash + linked decisions and surfaces drift / NEEDS-UPDATE markers ([TMUX.md § Specs](TMUX.md#specs-as-versioned-artifacts))
- **Auto-extract from transcripts** — `dcam claude extract` heuristically surfaces lesson/decision/critical-point candidates from a session, with interactive accept/reject promotion
- **Project mode** — opt-in `<repo>/.dcam/` directory with JSON-as-primary storage so decisions, lessons, and session summaries are committed and reviewable in PRs alongside source code ([TMUX.md § Project mode](TMUX.md#project-mode-committing-memory-alongside-code))
- **Pre-commit hook** — auto-regenerates `CLAUDE.md`/`AGENTS.md` managed sections when committed JSON state changes, so docs never drift behind decisions
- **Task tracking** — sessions linked to Beads issues for persistent task graphs
- **MCP server** — 15 native tools usable from any MCP-compatible client (Claude Code, Kiro, etc.)
- **Delta storage** — partitioned, append-only deltas with compaction and time-travel

## Install

```bash
cd DeltaCATAgentMemory
pip install -e .
```

### With DeltaCAT backend (optional)

```bash
pip install -e '.[deltacat]'
```

### Prerequisites

- Python ≥ 3.9
- [pyarrow](https://arrow.apache.org/docs/python/) ≥ 14.0
- Optional, but at least one of:
  - [Claude Code](https://docs.claude.com/en/docs/claude-code) — sessions auto-sync via the installed `SessionEnd` hook
  - [Kiro](https://github.com/Zyiqin-Miranda/Kiro) — pre-tool hook + AGENTS.md integration
- Optional: [deltacat](https://github.com/ray-project/deltacat) for ACID-compliant storage
- Optional: [beads](https://github.com/steveyegge/beads) (`bd` CLI) for task tracking
- Optional: `tmux` ≥ 3.0 for the multi-agent workflow

## Quick Start

Pick the path that matches your agent host.

### Claude Code path

```bash
# 1. Install the SessionEnd hook for this project (one-time per repo).
cd /path/to/your-project
dcam claude init

# 2. Use Claude Code normally. Every session auto-syncs to dcam at end.
#    No manual chat-start/chat-end needed.

# 3. Start your next Claude Code session. It auto-loads structured
#    summaries of recent sessions via the CLAUDE.md instruction:
#       dcam claude context

# 4. Search or replay any prior session.
dcam claude list
dcam claude search "auth timeout"
dcam claude recall <session-id>            # Replay a session
dcam claude recall <session-id> -- --resume # Launch a NEW session pre-loaded with that context
```

For multi-agent (manager / dev / reviewer over tmux) and project-mode
(committable team memory), see **[TMUX.md](TMUX.md)**.

### Kiro path

```bash
# 1. Initialize everything (tables + Kiro pre-tool hook + beads check)
dcam init

# 2. Store project-level facts that persist across all sessions
dcam memory add "Uses Python 3.11, Spring Boot backend" --name tech-stack
dcam memory add "Always run pytest before committing" --name dev-rules

# 3. Start a tracked chat session
dcam chat start --title "Fix auth timeout"

# 4. Enter interactive chat (pipes through kiro-cli with project memory + context)
dcam chat enter SESSION_ID

# 5. End session (auto-extracts memories, closes beads issue)
dcam chat end SESSION_ID --summary "Increased token TTL to 30min"

# 6. Later — recall what you did
dcam chat list
dcam chat recall SESSION_ID
dcam chat search "auth timeout"
dcam memory recall tech-stack    # Recall named memory from any session

# 7. Start new session with context from a previous one
dcam chat start --title "Continue work" --from SESSION_ID
```

## Commands

### Setup & Status

```bash
dcam init                    # Create tables, install Kiro hooks, check beads
dcam claude init             # Install Claude Code SessionEnd hook for this project
dcam project init            # Create committable .dcam/ for team-shared memory
dcam status                  # Show sessions, memories, indexed files, beads status
dcam project status          # Show active root, mode, and what is stored
dcam serve                   # Start MCP server (for native tool integration)
```

### Claude Code Integration

`dcam claude` reads Claude Code's JSONL transcripts at
`~/.claude/projects/.../<id>.jsonl`, syncs them to the dcam store, and
surfaces summaries on demand.

```bash
dcam claude init             # Install SessionEnd hook + initial sync
dcam claude sync             # Manually sync recent Claude Code sessions
dcam claude list             # List synced sessions (newest first)
dcam claude list --full-id   # Show full UUIDs (default truncates to 12 chars)
dcam claude recall ID        # Print messages from session (prefix matching works)
dcam claude recall ID -- --resume  # Launch a NEW Claude Code session pre-loaded with that context
dcam claude search "query"   # BM25 search across all synced Claude Code sessions
dcam claude context          # Print structured summaries of recent sessions
dcam claude context --sessions 5 --messages 0  # Just summaries, no message dump
```

Per-session structured summary includes: first/last user prompt, files
touched (`Read`/`Edit`/`Write`), commands run (`Bash`), URLs and tickets
referenced (CR-/SIM-/V-), and errors observed in tool output.

### Project Memory (cross-session)

Named memories that persist across all sessions. Automatically injected into every new session's context.

```bash
dcam memory add "Uses Java 17" --name java-stack              # Store with name
dcam memory add "Deploy: ./gradlew deploy" --name deploy-cmd  # Another named memory
dcam memory add "Updated to Java 21" --name java-stack        # Same name = update
dcam memory recall java-stack                                  # Recall by name
dcam memory recall deploy-cmd                                  # Works in any session
dcam memory list                                               # List all project memories
dcam memory context                                            # Show what gets injected
dcam memory context --session-id SID                           # Project + session memories
```

Memory types:
| Type | Scope | Example |
|------|-------|---------|
| `project` | All sessions (never decays) | "Uses Java 17", "Deploy with gradle" |
| `semantic` | Session-scoped | "Auth module handles JWT tokens" |
| `episodic` | Session-scoped | "Fixed auth bug on Apr 3" |
| `procedural` | Session-scoped | "Run tests before commit" |
| `short_term` | Session-scoped (7 day decay) | "Currently working on auth" |

### Chat History

```bash
dcam chat start              # Start session with auto-generated title
dcam chat start --title "T"  # Start session with custom title
dcam chat start --title "T" --from OLD_SESSION  # New session seeded with old context
dcam chat enter SESSION_ID   # Interactive chat via kiro-cli with context injection
dcam chat end SESSION_ID     # End session, extract memories
dcam chat end SID --summary "what was done"
dcam chat list               # List past sessions with actual message counts
dcam chat list --limit 50    # List more sessions
dcam chat recall SESSION_ID  # Replay all messages (ANSI-stripped, up to 1000)
dcam chat search "query"     # BM25-ranked search across all chat history
```

### Code Compaction

Instead of loading full files into the context window, compact them into indexed summaries and fetch only what's needed.

```bash
dcam compact ./src/          # Index all supported files in a directory
dcam compact file.py         # Index a single file
dcam compact --list          # List all indexed files and chunk counts
dcam lookup MyClass          # Find a function/class across all indexed files
dcam fetch 42                # Fetch raw source code for chunk ID 42
dcam resolve "fix the bug in auth.py"  # Auto-index + return relevant context
```

### Task Management (via Beads)

```bash
dcam task create "Fix auth bug" -p 0           # Create a P0 task
dcam task create "Add tests" --session SID      # Link task to a session
dcam task list                                  # List all open tasks
dcam task ready                                 # Show unblocked tasks
dcam task plan SESSION_ID                       # Auto-decompose into subtasks via kiro
dcam orchestrate                                # Start multi-agent orchestration loop
dcam orchestrate --interval 5                   # Poll every 5 seconds
```

### MCP Server

```bash
# Start the MCP server (stdio transport)
dcam serve

# Register with Claude Code (one-time setup)
claude mcp add dcam dcam serve

# Register with kiro-cli (one-time setup)
kiro-cli mcp add --name dcam --scope global --command dcam --args serve
```

15 tools available via MCP:

| Category | Tools |
|----------|-------|
| Memory | `dcam_store_memory`, `dcam_search_memories`, `dcam_project_memory`, `dcam_recall_memory` |
| Chat | `dcam_recall`, `dcam_list_sessions`, `dcam_search_history` |
| Code | `dcam_compact`, `dcam_lookup`, `dcam_fetch`, `dcam_fetch_chunk` |
| Tasks | `dcam_task_create`, `dcam_task_ready`, `dcam_task_complete` |
| Status | `dcam_status` |

## Storage Backends

dcam supports three storage backends:

### Local (default)

Flat parquet files at `~/.dcam/tables/<namespace>/`. Simple, no extra dependencies.

```bash
dcam init                          # Uses local parquet
```

### Delta (partitioned + versioned)

Append-only deltas with partitioning, compaction, and time-travel. Chat messages are partitioned by session_id.

```bash
dcam --catalog delta init          # Uses delta storage
```

Storage layout:
```
~/.dcam/delta/dcam/
  ├── chat_messages/
  │   ├── __manifest__.json         # Stream version tracker
  │   ├── __global__/               # Messages without session
  │   └── {session_id}/             # Partitioned by session
  │       ├── delta_000001.parquet  # Append-only deltas
  │       ├── delta_000002.parquet
  │       └── snapshot.parquet      # After compaction
  ├── memories/
  │   ├── __global__/               # Project memories (cross-session)
  │   └── {session_id}/             # Session-scoped memories
  └── chat_sessions/
      └── __global__/
```

### DeltaCAT (optional, ACID)

Full DeltaCAT integration with ACID commits, versioning, and high-frequency commit support.

```bash
pip install 'dcam[deltacat]'
dcam --catalog deltacat init
```

| Feature | Local | Delta | DeltaCAT |
|---------|-------|-------|----------|
| Partitioning | ✗ | ✓ by session | ✓ |
| Append-only deltas | ✗ | ✓ | ✓ |
| Compaction | ✗ | ✓ | ✓ |
| Time-travel | ✗ | ✓ | ✓ |
| ACID commits | ✗ | ✗ | ✓ |
| Dependencies | pyarrow | pyarrow | deltacat + daft + ray |

## Search

dcam uses BM25 ranking by default. BM25 scores documents by term frequency and rarity — it finds messages where your search terms appear even if they're not adjacent, and ranks more focused matches higher.

```bash
dcam chat search "auth timeout"                              # BM25 (default)
dcam --search-backend substring chat search "auth timeout"   # Exact substring match
```

| | BM25 (default) | Substring |
|---|---|---|
| "auth timeout" matches "auth handler has timeout issue" | ✓ | ✗ |
| Ranked by relevance | ✓ | ✗ |
| Words don't need to be adjacent | ✓ | ✗ |
| Ignores common words ("the", "a") | ✓ | ✗ |

## How Compaction Works

```
Full file (500 lines)
    ↓ dcam compact
Indexed chunks stored locally:
    function:handle_auth  L12-45   "Validates JWT token and refreshes..."
    class:AuthManager     L47-120  "Manages session lifecycle..."
    function:logout       L122-140 "Clears session and revokes token..."

    ↓ dcam lookup handle_auth
Returns summary only (not full source)

    ↓ dcam fetch 3
Returns raw source for just that chunk
```

Context window sees summaries. Raw code is fetched only when editing.

Supported languages: Python, Go, TypeScript, JavaScript, Java, Rust, Ruby, YAML, JSON, Markdown, Bash, SQL.

## Architecture

```
dcam CLI / MCP Server
  │
  ├── dcam/store.py            → Pluggable storage (local, delta, deltacat, project-JSON)
  │     ├── memories                (semantic, episodic, procedural, short_term, project)
  │     ├── chat_messages           (partitioned by session_id in delta mode)
  │     ├── chat_sessions           (session metadata + structured summaries)
  │     ├── decisions               (open / decided / superseded / withdrawn, with chain)
  │     ├── lessons                 (cross-session learnings by category)
  │     ├── compact_chunks          (indexed code chunks with summaries)
  │     └── compact_files           (file-level summaries)
  │
  ├── dcam/search.py           → Pluggable search (BM25 or substring)
  │
  ├── dcam/mcp_server.py       → MCP server (15 native tools)
  │
  ├── dcam/orchestrator.py     → Multi-agent task orchestration via beads
  │
  ├── dcam/tmux.py             → tmux session layout + manager/dev/reviewer prompts
  │
  ├── dcam/reviews.py          → Pull-based review requests, completed-review records, handoffs
  │
  ├── dcam/extract.py          → Heuristic lesson/decision/critical extraction from transcripts
  │
  ├── dcam/decisions.py        → Decision lifecycle + CLAUDE.md/AGENTS.md persistence
  │
  ├── dcam/project.py          → Project-mode root discovery + pre-commit hook
  │
  ├── dcam/claude_code.py      → Claude Code JSONL parser + sync + summary heuristics
  │
  ├── dcam/json_catalog.py     → JSON-as-primary catalog (committable tables)
  │
  ├── dcam/bridge.py           → Beads (bd CLI) integration
  │
  ├── dcam/compact.py          → Language-aware file indexing
  │
  ├── dcam/resolver.py         → Auto-index + context injection
  │
  ├── dcam/interactive.py      → Interactive chat via kiro-cli
  │
  ├── dcam/agent_instructions.py → Compact context protocol for agents
  │
  ├── dcam/kiro.py             → Kiro hook + AGENTS.md installation
  │
  ├── dcam/delta_store.py      → Delta-native storage engine
  │
  ├── dcam/delta_catalog.py    → Delta catalog adapter (partitions + time-travel)
  │
  ├── dcam/local_catalog.py    → Local parquet storage backend
  │
  └── dcam/deltacat_catalog.py → DeltaCAT storage backend (optional)
```

## Integration with Claude Code

After `dcam claude init`, three things land in your project:

1. A `SessionEnd` hook in `.claude/settings.local.json` that runs
   `dcam claude sync` automatically at the end of every Claude Code
   session. No manual chat-start/end needed.
2. A `CLAUDE.md` instruction telling Claude Code to call
   `dcam claude context` at the start of each session, so prior work is
   loaded as structured summaries (files touched, commands run,
   URLs/tickets, errors).
3. A shell hook script at `.claude/hooks/dcam-session-sync.sh`.

The hook reads Claude Code's JSONL transcripts at
`~/.claude/projects/.../<session-uuid>.jsonl`, deduplicates messages by
UUID, follows conversation branches (so retries/edits don't double-count),
and writes everything to dcam's tables.

To wire dcam in as MCP tools instead of shell commands, register the MCP
server with Claude Code:

```bash
claude mcp add dcam dcam serve
```

The agent then calls `dcam_recall_memory`, `dcam_lookup`, `dcam_fetch`,
etc. as native tools.

For multi-agent workflows (manager / dev / reviewer over tmux) and
project-mode (committable team memory with auto-persist on commit), see
**[TMUX.md](TMUX.md)**.

## Integration with Kiro

After `dcam init`, a Kiro hook is installed at `hooks/dcam-pre-tool.sh` that auto-indexes files before edit operations. Agent instructions are added to `AGENTS.md` telling the agent to use `dcam compact`/`dcam lookup`/`dcam fetch` instead of reading full files.

For native tool integration, register the MCP server:

```bash
kiro-cli mcp add --name dcam --scope global --command dcam --args serve
```

The agent then calls `dcam_compact`, `dcam_lookup`, `dcam_fetch`, `dcam_recall_memory` as native tools — no shell commands needed. All dcam tools are auto-approved via `allowedTools: ["@dcam/*"]` in the agent config.

## Integration with Beads

If the `bd` CLI is installed and initialized (`bd init`), dcam automatically:
- Creates a Beads issue for each chat session
- Logs message previews as issue comments
- Closes the issue when the session ends
- Links session IDs to issue IDs for cross-referencing
- Supports multi-agent orchestration via task dependency graphs

If `bd` is not available, dcam works in standalone mode.

## Multi-Agent Orchestration

Two complementary modes ship in dcam:

**1. Beads-driven task queue** — `dcam orchestrate` polls `bd ready` for
unblocked tasks and dispatches them to kiro-cli agents:

```bash
dcam task create "Step 1: Analyze" -p 0
dcam task create "Step 2: Implement" -p 1    # blocked until Step 1 closes
dcam task plan SESSION_ID                    # Auto-decompose via kiro-cli
dcam orchestrate                             # Start the loop
```

**2. Interactive tmux team** — `dcam tmux` lays out a manager / dev /
reviewer session of Claude Code (or any agent CLI), with first-class
support for decisions, lessons, peer messaging, and dep tracking:

```bash
dcam tmux start <session> --launch
dcam tmux dev <session> <slug> "<brief>" --launch
dcam tmux ask <slug> "<title>" --options "A:...|B:..."
dcam tmux decide --id N --choice A --rationale "..." --persist claude
dcam tmux review <session> --launch
```

Decisions, lessons, and per-session structured summaries can be
committed alongside source code via project mode (`dcam project init`).
A pre-commit hook keeps `CLAUDE.md`/`AGENTS.md` in sync with the JSON
state automatically.

See **[TMUX.md](TMUX.md)** for the full workflow, role prompts, and
end-to-end walkthrough.

## Running Tests

```bash
pip install pytest
pytest tests/
```

## Namespace Isolation

Use `--namespace` to isolate data between projects:

```bash
dcam --namespace project-a chat start --title "Work on project A"
dcam --namespace project-b chat start --title "Work on project B"
dcam --namespace project-a chat list   # Only shows project A sessions
```

## Global Options

```bash
dcam --namespace NAME           # Isolate data by project (default: dcam)
dcam --search-backend bm25      # BM25 ranked search (default)
dcam --search-backend substring  # Exact substring matching
dcam --catalog local            # Flat parquet files (default)
dcam --catalog delta            # Partitioned + versioned deltas
dcam --catalog deltacat         # DeltaCAT ACID storage
dcam --root PATH                # Override storage root (also via DCAM_ROOT env);
                                # in project mode, auto-discovered by walking
                                # up from cwd looking for .dcam/
```

Global flags work either before or after the subcommand:

```bash
dcam --namespace foo claude context
dcam claude context --namespace foo   # equivalent
```

## Example Workflow

### With Claude Code (recommended for new projects)

```bash
# Day 0: Wire dcam into the project (once).
cd /path/to/your-project
dcam claude init
dcam memory add "Uses Python 3.11, FastAPI backend" --name tech-stack
dcam memory add "Deploy with: make deploy-prod" --name deploy-cmd

# Day 1: Just open Claude Code and work. Sessions auto-sync at end.
claude
# ... feature work happens; SessionEnd hook syncs everything to dcam ...

# Day 2: Open Claude Code again. CLAUDE.md instruction auto-loads context.
claude
# (Inside the session: structured summaries of recent work appear via
# `dcam claude context`. Search prior work with `dcam claude search`.)

# Anytime: review what was done across sessions.
dcam claude list
dcam claude search "Stripe webhook"
dcam claude recall <session-id>

# When you're ready for parallelized agent work, spin up the team:
dcam project init                           # Make memory committable
dcam project install-hook                   # Auto-persist on commits
dcam tmux start payment --launch
dcam tmux dev payment api "Add /v2/sessions handler" --launch
# (See TMUX.md for the full multi-agent workflow.)
```

### With Kiro

```bash
# Day 0: Set up project memory (once)
dcam init
dcam memory add "Uses Python 3.11, FastAPI backend" --name tech-stack
dcam memory add "Deploy with: make deploy-prod" --name deploy-cmd
dcam memory add "Tests: pytest tests/ -x" --name test-cmd

# Day 1: Start working on a feature
dcam compact ./src/                    # Index the codebase
dcam chat start --title "Add payment API"

# ... work happens, messages stored ...

dcam chat end abc123 --summary "Added Stripe webhook handler"

# Day 2: Pick up where you left off
dcam chat list                         # See yesterday's session
dcam chat recall abc123                # Review what was done
dcam memory recall tech-stack          # Remember the stack
dcam lookup PaymentHandler             # Find the code you wrote
dcam chat start --title "Add payment tests" --from abc123

# ... continue working with full context + project memory injected ...
```
