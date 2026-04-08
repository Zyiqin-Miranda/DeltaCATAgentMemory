# DeltaCATAgentMemory (dcam)

Persistent agent memory that survives across chat sessions. One CLI integrating **Kiro** + **DeltaCAT** + **Beads**.

## Problem

AI chat sessions are ephemeral — when you close a chat, all context is lost. You start over every time, re-explaining your project, re-reading files, and losing track of what was done.

## Solution

`dcam` persists everything locally:
- **Chat history** — every message stored and searchable (BM25 ranked) across sessions
- **Long-term memory** — key facts extracted and recalled with Ebbinghaus decay
- **Code index** — files summarized into compact chunks, fetched on demand instead of loading full files into context
- **Task tracking** — sessions linked to Beads issues for persistent task graphs
- **MCP server** — 13 native tools for AI assistants via Model Context Protocol
- **Multi-agent orchestration** — task decomposition and dispatch via beads dependency graph

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
- Optional: [deltacat](https://github.com/ray-project/deltacat) for ACID-compliant storage
- Optional: [beads](https://github.com/steveyegge/beads) (`bd` CLI) for task tracking
- Optional: [Kiro](https://github.com/Zyiqin-Miranda/Kiro) for hook integration

## Quick Start

```bash
# 1. Initialize everything (tables + kiro hooks + beads check)
dcam init

# 2. Start a tracked chat session
dcam chat start --title "Fix auth timeout"

# 3. Enter interactive chat (pipes through kiro-cli with context)
dcam chat enter SESSION_ID

# 4. End session (auto-extracts memories, closes beads issue)
dcam chat end SESSION_ID --summary "Increased token TTL to 30min"

# 5. Later — recall what you did
dcam chat list
dcam chat recall SESSION_ID
dcam chat search "auth timeout"

# 6. Start new session with context from a previous one
dcam chat start --title "Continue work" --from SESSION_ID
```

## Commands

### Setup & Status

```bash
dcam init                    # Create tables, install hooks, check beads
dcam status                  # Show sessions, memories, indexed files, beads status
dcam serve                   # Start MCP server (for native tool integration)
```

### Chat History

```bash
dcam chat start              # Start session with auto-generated title
dcam chat start --title "T"  # Start session with custom title
dcam chat start --title "T" --from OLD_SESSION  # New session seeded with old context
dcam chat enter SESSION_ID   # Interactive chat via kiro-cli with context injection
dcam chat end SESSION_ID     # End session, extract memories
dcam chat end SID --summary "what was done"
dcam chat list               # List past sessions (most recent first)
dcam chat list --limit 50    # List more sessions
dcam chat recall SESSION_ID  # Replay all messages from a session (up to 1000)
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

# Register with kiro-cli (one-time setup)
kiro-cli mcp add --name dcam --scope global --command dcam --args serve
```

13 tools available via MCP: `dcam_store_memory`, `dcam_search_memories`, `dcam_recall`, `dcam_list_sessions`, `dcam_search_history`, `dcam_compact`, `dcam_lookup`, `dcam_fetch`, `dcam_fetch_chunk`, `dcam_task_create`, `dcam_task_ready`, `dcam_task_complete`, `dcam_status`.

## Storage Backends

dcam supports two storage backends:

### Local (default)

Stores data as parquet files at `~/.dcam/tables/<namespace>/`. No extra dependencies.

```bash
dcam init                          # Uses local parquet
dcam --catalog local status        # Explicit
```

### DeltaCAT (optional)

ACID-compliant storage with versioning, time-travel, and high-frequency commit support.

```bash
pip install 'dcam[deltacat]'
dcam --catalog deltacat init       # Uses DeltaCAT tables
```

| Feature | Local (parquet) | DeltaCAT |
|---------|----------------|----------|
| ACID commits | ✗ | ✓ |
| Concurrent writes | Last write wins | Safe |
| Version history | ✗ | ✓ (time-travel) |
| High-frequency commits | File rewrite each time | Append-optimized |
| Rollback | ✗ | ✓ |
| Dependencies | pyarrow only | deltacat + daft + ray |

## Search

dcam uses BM25 ranking by default for search. BM25 scores documents by term frequency and rarity — it finds messages where your search terms appear even if they're not adjacent, and ranks more focused matches higher.

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
  ├── dcam/store.py          → Pluggable storage (local parquet or DeltaCAT)
  │     ├── memories              (semantic, episodic, procedural, short_term)
  │     ├── chat_messages         (all messages across all sessions)
  │     ├── chat_sessions         (session metadata + beads links)
  │     ├── compact_chunks        (indexed code chunks with summaries)
  │     └── compact_files         (file-level summaries)
  │
  ├── dcam/search.py         → Pluggable search (BM25 or substring)
  │
  ├── dcam/mcp_server.py     → MCP server (13 native tools)
  │
  ├── dcam/orchestrator.py   → Multi-agent task orchestration via beads
  │
  ├── dcam/bridge.py         → Beads (bd CLI) integration
  │
  ├── dcam/compact.py        → Language-aware file indexing
  │
  ├── dcam/resolver.py       → Auto-index + context injection
  │
  ├── dcam/interactive.py    → Interactive chat via kiro-cli
  │
  ├── dcam/agent_instructions.py → Compact context protocol for agents
  │
  ├── dcam/kiro.py           → Kiro hook + AGENTS.md installation
  │
  ├── dcam/local_catalog.py  → Local parquet storage backend
  │
  └── dcam/deltacat_catalog.py → DeltaCAT storage backend (optional)
```

## Integration with Kiro

After `dcam init`, a Kiro hook is installed at `hooks/dcam-pre-tool.sh` that auto-indexes files before edit operations. Agent instructions are added to `AGENTS.md` telling the agent to use `dcam compact`/`dcam lookup`/`dcam fetch` instead of reading full files.

For native tool integration, register the MCP server:

```bash
kiro-cli mcp add --name dcam --scope global --command dcam --args serve
```

The agent then calls `dcam_compact`, `dcam_lookup`, `dcam_fetch` as native tools — no shell commands needed.

## Integration with Beads

If the `bd` CLI is installed and initialized (`bd init`), dcam automatically:
- Creates a Beads issue for each chat session
- Logs message previews as issue comments
- Closes the issue when the session ends
- Links session IDs to issue IDs for cross-referencing
- Supports multi-agent orchestration via task dependency graphs

If `bd` is not available, dcam works in standalone mode.

## Multi-Agent Orchestration

```bash
# Create tasks with dependencies
dcam task create "Step 1: Analyze" -p 0
dcam task create "Step 2: Implement" -p 1    # blocked until Step 1 closes

# Or auto-decompose from a session goal
dcam task plan SESSION_ID

# Start the orchestration loop
dcam orchestrate
```

The orchestrator polls `bd ready` for unblocked tasks, dispatches them to kiro-cli agents with session context, logs results, and closes tasks on completion.

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
dcam --namespace NAME          # Isolate data by project (default: dcam)
dcam --search-backend bm25     # BM25 ranked search (default)
dcam --search-backend substring # Exact substring matching
dcam --catalog local           # Local parquet files (default)
dcam --catalog deltacat        # DeltaCAT ACID storage
```

## Example Workflow

```bash
# Day 1: Start working on a feature
dcam init
dcam compact ./src/                    # Index the codebase
dcam chat start --title "Add payment API"

# ... work happens, messages stored ...

dcam chat end abc123 --summary "Added Stripe webhook handler"

# Day 2: Pick up where you left off
dcam chat list                         # See yesterday's session
dcam chat recall abc123                # Review what was done
dcam lookup PaymentHandler             # Find the code you wrote
dcam chat start --title "Add payment tests" --from abc123

# ... continue working with full context of what was done before ...
```
