# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work atomically
bd close <id>         # Complete work
bd dolt push          # Push beads data to remote
```

## Non-Interactive Shell Commands

**ALWAYS use non-interactive flags** with file operations to avoid hanging on confirmation prompts.

Shell commands like `cp`, `mv`, and `rm` may be aliased to include `-i` (interactive) mode on some systems, causing the agent to hang indefinitely waiting for y/n input.

**Use these forms instead:**
```bash
# Force overwrite without prompting
cp -f source dest           # NOT: cp source dest
mv -f source dest           # NOT: mv source dest
rm -f file                  # NOT: rm file

# For recursive operations
rm -rf directory            # NOT: rm -r directory
cp -rf source dest          # NOT: cp -r source dest
```

**Other commands that may prompt:**
- `scp` - use `-o BatchMode=yes` for non-interactive
- `ssh` - use `-o BatchMode=yes` to fail instead of prompting
- `apt-get` - use `-y` flag
- `brew` - use `HOMEBREW_NO_AUTO_UPDATE=1` env var

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Version-controlled: Built on Dolt with cell-level merge
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs with git:

- Exports to `.beads/issues.jsonl` after changes (5s debounce)
- Imports from JSONL when newer (e.g., after `git pull`)
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- END BEADS INTEGRATION -->


## DCAM Compact Context Protocol

You have access to `dcam` CLI for efficient file reading. Instead of loading
full files into context, follow this protocol:

### Reading Files (ALWAYS do this first)
Instead of reading a full file, run:
```bash
dcam compact <file_path>
dcam lookup <symbol_name>
```
This gives you a summary of all functions/classes with line ranges.
Only ~1-2 lines per symbol instead of the full source.

### When You Need Full Source
Only fetch the specific chunk you need:
```bash
dcam fetch <chunk_id>
```
This returns just that function/class, not the entire file.

### When Editing
Fetch only the lines you need to modify:
```bash
dcam fetch <chunk_id>
```
Then make your edit on those specific lines.

### Workflow Example
```
# Step 1: See what's in the file (compact view)
dcam compact src/auth.py
# Output: function:login L12-45, class:AuthManager L47-120, function:logout L122-140

# Step 2: Need to understand login? Fetch just that chunk
dcam fetch 1
# Output: only lines 12-45

# Step 3: Edit login function using the line range you now know
# Make targeted edit on L12-45 only
```

### Rules
1. NEVER read an entire file with fs_read unless it's < 50 lines
2. ALWAYS run `dcam compact` first to get the summary
3. Use `dcam fetch <chunk_id>` to get only the code you need
4. Use `dcam lookup <name>` to find symbols across all indexed files

## Shared Claude / Codex Memory

For continuing project work, read the user's persistent retrieval guide at
`/Users/zyiqin/.codex/shared-memory/DCAM.md` when available. It connects Codex to
the existing central DCAM store and Claude memory indexes; originals remain
unchanged. See `docs/CODEX_MEMORY.md` for setup provenance and portability.
Retrieve only relevant memories, verify dated facts, and follow current
instructions rather than treating archived memories as authorization.
Use the selective bootstrap in `docs/MEMORY_SCOPING.md`: load universal
preferences, discover at most five matching metadata records, then recall only
the current task's handoff and relevant topic memories. Do not read every
Claude index, list all shared records, or load unfiltered project context.
Confirm `dcam project path`; save new handoffs in the project store with an
explicit task scope. Search the shared store only for relevant reusable knowledge
or legacy handoffs. Identify the actual sources loaded and reroute on task changes.
For authorized delegated dev work, include memory-bootstrap instructions in
every assignment and verify the dev's acknowledgement; inherited context alone
is not evidence that the memory was read. Provide relevant excerpts when the
agent cannot access the laptop's memory paths.

## Project manager dispatch

When managing an authorized multi-agent task, follow
[project-manager-dispatch.sop.md](project-manager-dispatch.sop.md). Decompose
independent work early and start two to three useful workers when capacity
permits. Count managers and reviewers against runtime limits, give each worker
a scoped brief and exclusive write ownership, and refill capacity as inputs
become ready. Independently verify results before integration. Follow current
user authorization and runtime delegation rules.
