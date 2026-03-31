# DeltaCATAgentMemory (dcam)

Persistent agent memory that survives across chat sessions. One CLI integrating **Kiro** + **DeltaCAT** + **Beads**.

## Problem

AI chat sessions are ephemeral — when you close a chat, all context is lost. You start over every time, re-explaining your project, re-reading files, and losing track of what was done.

## Solution

`dcam` persists everything locally in DeltaCAT tables:
- **Chat history** — every message stored and searchable across sessions
- **Long-term memory** — key facts extracted and recalled with Ebbinghaus decay
- **Code index** — files summarized into compact chunks, fetched on demand instead of loading full files into context
- **Task tracking** — sessions linked to Beads issues for persistent task graphs

## Install

```bash
cd DeltaCATAgentMemory
pip install -e .
```

### Prerequisites

- Python ≥ 3.9
- [pyarrow](https://arrow.apache.org/docs/python/) ≥ 14.0
- [deltacat](https://github.com/ray-project/deltacat)
- Optional: [beads](https://github.com/steveyegge/beads) (`bd` CLI) for task tracking
- Optional: [Kiro](https://github.com/Zyiqin-Miranda/Kiro) for hook integration

## Quick Start

```bash
# 1. Initialize everything (deltacat tables + kiro hooks + beads check)
dcam init

# 2. Start a tracked chat session
dcam chat start --title "Fix auth timeout"
# Output: {"session_id": "a1b2c3d4e5f6", "title": "Fix auth timeout", "beads_issue_id": "bd-x1y2"}

# 3. (Your chat happens here — messages are stored automatically)

# 4. End session (auto-extracts memories, closes beads issue)
dcam chat end a1b2c3d4e5f6 --summary "Increased token TTL to 30min"

# 5. Later — recall what you did
dcam chat list
dcam chat recall a1b2c3d4e5f6
dcam chat search "auth timeout"
```

## Commands

### Setup & Status

```bash
dcam init                    # Create tables, install hooks, check beads
dcam status                  # Show sessions, memories, indexed files, beads status
```

### Chat History

```bash
dcam chat start              # Start session with auto-generated title
dcam chat start --title "T"  # Start session with custom title
dcam chat end SESSION_ID     # End session, extract memories
dcam chat end SID --summary "what was done"
dcam chat list               # List past sessions (most recent first)
dcam chat list --limit 50    # List more sessions
dcam chat recall SESSION_ID  # Replay all messages from a session
dcam chat recall SID --limit 20  # Last 20 messages only
dcam chat search "query"     # Full-text search across all chat history
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

### How Compaction Works

```
Full file (500 lines)
    ↓ dcam compact
Indexed chunks stored in DeltaCAT:
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
dcam CLI
  │
  ├── dcam/store.py      → DeltaCAT tables (pyarrow + deltacat)
  │     ├── memories          (semantic, episodic, procedural, short_term)
  │     ├── chat_messages     (all messages across all sessions)
  │     ├── chat_sessions     (session metadata + beads links)
  │     ├── compact_chunks    (indexed code chunks with summaries)
  │     └── compact_files     (file-level summaries)
  │
  ├── dcam/bridge.py     → Beads (bd CLI)
  │     ├── Creates issue per chat session
  │     ├── Logs message summaries as comments
  │     └── Closes issue on session end
  │
  ├── dcam/compact.py    → File indexing
  │     ├── Language-aware parsing (Python, Go, TS, JS, Java, Rust)
  │     ├── Extracts functions, classes, imports
  │     └── Generates one-line summaries per chunk
  │
  ├── dcam/resolver.py   → Auto context injection
  │     ├── Parses messages for file/symbol references
  │     ├── Auto-indexes unindexed files on first reference
  │     └── Returns compact context block for agent injection
  │
  └── dcam/kiro.py       → Kiro integration
        ├── Installs pre-tool-use hook
        ├── Auto-compacts files before edit operations
        └── Creates .kiro/agents/dcam.json config
```

## Integration with Kiro

After `dcam init`, a Kiro hook is installed at `hooks/dcam-pre-tool.sh`. This hook runs before every file operation and auto-indexes the target file, so the compact index stays fresh without manual intervention.

The agent config at `.kiro/agents/dcam.json` tells Kiro about the memory provider.

## Integration with Beads

If the `bd` CLI is installed and initialized (`bd init`), dcam automatically:
- Creates a Beads issue for each chat session
- Logs message previews as issue comments
- Closes the issue when the session ends
- Links session IDs to issue IDs for cross-referencing

If `bd` is not available, dcam works in standalone mode with DeltaCAT only.

## Integration with KiroAgentTeams (kat)

The `kat` CLI includes a `memory` subcommand that delegates to the deltacat memory module:

```bash
kat memory init
kat memory chat start --title "Fix bug"
kat memory compact ./src/
kat memory status
```

The orchestrator in KiroAgentTeams auto-injects compact context into the agent's context window via the `CompactContext` field in the context template.

## Data Storage

All data is stored locally in DeltaCAT tables under the `dcam` namespace (configurable with `--namespace`). No external services required. Data persists across chat sessions, machine restarts, and agent invocations.

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
dcam chat start --title "Add payment tests"

# ... continue working with full context of what was done before ...
```
