# Multi-Agent Coordination with tmux + DCAM

This guide explains how to run a manager / dev / reviewer team of Claude
Code agents coordinated via tmux and DCAM. It is written for both humans
(who are setting things up or reading along) and agents (who will execute
the commands).

If you only need persistent memory for a single Claude Code session, see
the main [README](README.md). This document layers a multi-agent workflow
on top.

## When to use this

You want to develop a non-trivial feature with multiple Claude Code
sessions running in parallel, where:

- A **manager** holds high-level context and makes architectural decisions.
- Several **dev agents** each work on one slice of the feature.
- An **on-demand reviewer** spot-checks dev work against the brief and the
  team's recorded decisions.
- All decisions, lessons learnt, and state changes are captured in
  DeltaCAT-backed parquet tables, plus mirrored into `CLAUDE.md` and/or
  `AGENTS.md` for both humans and future agent sessions.

## Project mode: committing memory alongside code

DCAM has two storage modes:

| Mode    | Root                    | When                                     | What's there                                           |
|---------|-------------------------|------------------------------------------|--------------------------------------------------------|
| Global  | `~/.dcam/` (default)    | Single user, no team review              | Everything in parquet                                  |
| Project | `<repo>/.dcam/` (opt-in)| Internal repos where memory is reviewed  | JSON for decisions/lessons/sessions; parquet for chat  |

**Project mode** is what you want when working inside an internal repo
where the team's decisions and lessons should be committed and reviewed
in pull requests alongside source code. It's opt-in and discoverable.

### Layout

```
<repo>/
├── .dcam/                    ← committed, reviewable
│   ├── README.md             ← auto-generated explainer
│   ├── .gitignore            ← keeps tables/ out of git
│   ├── decisions.json        ← committed (PRIMARY source of truth for decisions)
│   ├── lessons.json          ← committed
│   ├── sessions.json         ← committed (per-session structured summaries)
│   └── tables/               ← gitignored
│       └── <namespace>/
│           ├── chat_messages.parquet
│           ├── memories.parquet
│           ├── compact_chunks.parquet
│           └── compact_files.parquet
└── … rest of your repo …
```

The three JSON files are designed to render cleanly in `git diff`. A
decision being superseded shows up as a one-line status change plus a
new appended record. A lesson added by a dev shows up as a single-block
diff. Reviewers can scan them in PRs without tooling.

`tables/` stays local — it holds raw chat transcripts, BM25 indices, and
file-summary chunks, none of which belong in a code review.

### Setup

From your repo root:

```bash
dcam project init
git add .dcam/
git commit -m "Initialize DCAM team memory"
```

That's it. Every subsequent `dcam` invocation from anywhere inside the
repo will auto-discover `.dcam/` by walking up from the current working
directory (the same way `git` finds `.git/`).

### Discovery order

The CLI resolves the storage root in this order:

1. `--root <path>` flag on the command (highest priority).
2. `DCAM_ROOT` environment variable.
3. `.dcam/` walking up from the current working directory.
4. `~/.dcam/` fallback (global mode).

You can confirm which root is active with:

```bash
dcam project path        # Print the active root
dcam project status      # Print root, mode, and table contents
```

### Working with multiple internal projects

Each repo gets its own `.dcam/`. When you `cd` into a different repo,
the CLI auto-switches to that repo's memory. You don't need to manage
namespaces by hand.

To target a specific repo's memory from outside it (e.g., from a
release-engineering script):

```bash
dcam --root /path/to/repo/.dcam tmux decisions list
```

### Committing the team's work

When you're done with an internal task and want to merge:

```bash
git add .dcam/decisions.json .dcam/lessons.json .dcam/sessions.json
git commit -m "DCAM: record token-storage decisions and review lessons"
```

The diff in the PR will show exactly which decisions changed, what was
chosen, and why. Reviewers don't need DCAM installed to read it.

### Auto-persist on commit (pre-commit hook)

Without the hook, the workflow has a sharp edge: if you `decide` without
`--persist`, then `git commit -a`, the JSON changes ship but the
CLAUDE.md/AGENTS.md managed sections drift behind. The hook closes that
gap.

**What it does:**

1. Fires on `git commit` only when `.dcam/decisions.json`,
   `.dcam/lessons.json`, or `.dcam/sessions.json` is staged. Otherwise no-op.
2. Runs `dcam tmux persist --target auto`, which **only** updates
   markdown files that already contain DCAM markers — so if you've never
   manually persisted to `AGENTS.md`, the hook won't start writing to it
   on its own.
3. Re-stages any markdown that changed, so the regenerated CLAUDE.md
   ships in the same commit as the underlying JSON change.
4. If `dcam` isn't on `PATH` (e.g., a teammate cloned the repo without
   installing DCAM), the hook prints a warning and lets the commit
   proceed unchanged.

**Setup:**

The hook script lives at `.dcam/hooks/pre-commit` (committed, reviewable).
Each contributor symlinks it into their `.git/hooks/`:

```bash
dcam project install-hook
```

This is idempotent. It refuses to overwrite an existing non-DCAM
pre-commit hook (`--force` to override).

**Opt CLAUDE.md or AGENTS.md in once:**

The hook only updates files with markers. To install the markers, run a
manual persist *once* per markdown target:

```bash
dcam tmux persist --target claude       # opt CLAUDE.md in
dcam tmux persist --target agents       # also AGENTS.md if you want it
```

After that, every relevant commit auto-refreshes both files.

**Skipping the hook:**

If you need to commit without auto-persist (debugging, partial state):

```bash
git commit --no-verify
```

**Removing it:**

```bash
dcam project uninstall-hook
```

Only removes the symlink if it points at our hook; safe to run blindly.

**Troubleshooting:**

- *"persist failed"* + commit aborted → there's a problem with your DCAM
  state. The error message comes from the persist subcommand. Skip with
  `--no-verify` to investigate, then fix and recommit.
- *Hook didn't run* → check the symlink: `ls -la .git/hooks/pre-commit`.
  Should point at `../../.dcam/hooks/pre-commit`. If missing, run
  `dcam project install-hook` again.
- *Hook ran but CLAUDE.md wasn't updated* → no DCAM markers in
  CLAUDE.md yet. Run `dcam tmux persist --target claude` manually once.

### Migrating an existing global project to project mode

If you've been using DCAM in global mode (`~/.dcam/`) and want to lift
some of that memory into a repo:

```bash
cd /path/to/repo
dcam project init
# Then re-run any decisions/lessons you want in the repo's history with
# `dcam tmux ask`/`decide`/`lesson`. (There's no automated copy yet —
# the global tables may include unrelated work from other projects.)
```

A future `dcam project import --from-global --filter <namespace>` could
automate the copy; not implemented yet.

## Prerequisites

- DCAM installed (`pip install -e .` from the repo root).
- `tmux` ≥ 3.0 (`brew install tmux` on macOS).
- The `claude` CLI on `$PATH`.
- Optional: `bd` (beads) for task tracking + dependency graph. DCAM falls
  back to silent no-ops if `bd` is not initialized in the project, so you
  can try the workflow without it.

Each window's session is auto-synced to DCAM at end via the SessionEnd
hook installed by `dcam claude init`. Run that once per project before
starting:

```bash
cd /path/to/your-project
dcam claude init
```

## Mental model

```
┌─────────────────── tmux session: <project> ───────────────────┐
│                                                                │
│   window: manager      window: dev-auth      window: dev-api   │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │
│   │  claude      │    │  claude      │    │  claude      │    │
│   │  (MANAGER)   │    │  (DEV slug:  │    │  (DEV slug:  │    │
│   │              │    │   auth)      │    │   api)       │    │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘    │
│          │                   │                   │            │
│   window: review                                              │
│   ┌──────────────┐                                            │
│   │  claude      │   (started on demand)                      │
│   │  (REVIEWER)  │                                            │
│   └──────────────┘                                            │
└────────────────────────────────────────────────────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  ~/.dcam/    │   tables: chat_messages,
                    │  tables/     │   chat_sessions, decisions,
                    │              │   lessons, …
                    └──────────────┘
                           │
                           ▼
                    project/CLAUDE.md (managed sections)
                    project/AGENTS.md (managed sections)
```

Window naming is the contract. All commands key off these names:

| Window name      | Role        | Notes                                  |
|------------------|-------------|----------------------------------------|
| `manager`        | Manager     | Created by `dcam tmux start`           |
| `dev-<slug>`     | Dev         | One per concurrent task                |
| `review`         | Reviewer    | Created by `dcam tmux review`          |

`<slug>` is auto-derived from the task title via `slugify()`: `Auth
Flow refactor!!` → `auth-flow-refactor`.

## Quick start (five commands)

```bash
# 1. Bootstrap DCAM hooks for this project (once).
cd /path/to/your-project
dcam claude init

# 2. Create the tmux session and the manager window.
dcam tmux start myproject --launch

# 3. Spawn two dev windows, each with its own task.
dcam tmux dev myproject auth "Add OAuth refresh-token rotation" --launch
dcam tmux dev myproject api  "Wire /v2/sessions endpoint"        --launch

# 4. Attach and switch between windows with Ctrl-b 0/1/2/…
tmux attach -t myproject

# 5. From outside (or another window), run the reviewer when ready.
dcam tmux review myproject --launch
```

That's it. The manager and devs each have a Claude Code session pre-loaded
with their role prompt and (for devs) their task brief.

## Communication channels

There are three layers — pick the lightest one that fits the message.

### 1. Async via DCAM (default, free)

Every window's transcript is synced at SessionEnd. Anyone can read it:

```bash
dcam claude context --sessions 5    # Structured summary of the 5 most
                                    # recent sessions (any window).
dcam claude search "OAuth"          # Find any prior message mentioning OAuth.
dcam claude recall <session-id>     # Replay one session in full.
```

The structured summary includes first/last user prompt, files touched,
commands run, URLs/tickets referenced, and errors seen — so the manager
can scan dev progress in seconds.

### 2. Milestone via beads comments

When a dev hits a real milestone, post a one-liner that lands on their
beads task (manager and reviewer subscribe by reading `bd show`):

```bash
# Inside a dev window:
dcam tmux update auth "started"
dcam tmux update auth "blocked: waiting on token-storage decision"
dcam tmux update auth "ready for review"
dcam tmux update auth "done"
```

### 3. Live via tmux send-keys

For time-critical interjections only:

```bash
# From the manager window:
dcam tmux send myproject dev-auth "stop, scope just changed: drop OAuth and use JWT"
```

This is `tmux send-keys` under the hood. Prefer decisions and bd comments
for anything that should leave a permanent record.

## Decisions: the heart of the workflow

When a dev hits an architectural fork they cannot resolve alone, they
**ask** rather than silently choosing. The decision is recorded as a row
in the `decisions` parquet table and a `[ask:<id>]` comment on their bd
task.

### Dev side: requesting a decision

```bash
dcam tmux ask auth "Token storage" \
    --context "Where do OAuth refresh tokens live?" \
    --options "server:store in DB|cookie:encrypted httpOnly cookie" \
    --recommend server
```

This is non-blocking. The dev keeps working with their best guess. They
periodically check whether the decision has landed:

```bash
dcam tmux decisions list --status decided
```

### Manager side: triaging and deciding

```bash
# See what is waiting:
dcam tmux decisions list --status open

# Drill into one (full chain history if it's a revision):
dcam tmux decisions show 7

# Resolve it, optionally persisting to CLAUDE.md:
dcam tmux decide --id 7 --choice server \
    --rationale "DB storage gives us revocation; mobile cookie budget is tight." \
    --persist claude
```

### Revising a prior decision

If the team learns something new and needs to change a previous call, the
old decision is **superseded**, not deleted:

```bash
dcam tmux decide --supersedes 7 --choice cookie \
    --rationale "Mobile budget resolved via short-form JWT; revocation via central blocklist." \
    --persist claude
```

Now `dcam tmux decisions list` shows both rows: DEC-7 marked
`superseded`, DEC-9 marked `decided` with `Supersedes. [DEC-7]` in its
rendered markdown. The audit trail is permanent.

## Peer-to-peer dev coordination

When dev A's work depends on dev B's, the team should make it explicit
rather than each dev silently waiting.

```bash
# Declare: dev:auth is blocked by dev:api
dcam tmux dep api auth

# See open dev tasks at a glance (with target marked):
dcam tmux deps auth

# Send a direct message (delivered live via tmux + persisted as a bd
# comment on the receiver's task):
dcam tmux msg auth api "your /v2/sessions response shape needs to include device_id"
```

`dep` wraps `bd dep add` so the orchestrator and `bd ready` honor it.

## Lessons learnt: cross-session knowledge

Anything worth remembering across sessions — design principles you
discovered the hard way, testing pitfalls, ops gotchas — gets logged as a
**lesson**:

```bash
dcam tmux lesson "Always validate input at system boundaries before trusting downstream code." \
    --category design --persist claude

dcam tmux lesson "Run integration tests against a real DB; mocked tests passed but the prod migration failed." \
    --category testing --persist claude
```

Categories: `design`, `testing`, `ops`, `process`, or none. Lessons are
grouped by category in the rendered markdown.

## How decisions and lessons land in CLAUDE.md / AGENTS.md

`--persist claude` (or `agents`, or `both`) regenerates managed sections
delimited by HTML comments:

```markdown
<!-- DCAM:DECISIONS:START -->
## Decisions

### [DEC-7] Token storage  ·  2026-05-21  ·  [superseded]
**Context.** ...
**Options considered:**
- `server` (recommended) — store in DB
- `cookie` — encrypted httpOnly cookie
**Chosen.** `server` (by manager)
**Rationale.** DB storage gives us revocation; mobile cookie budget is tight.
_Requested by_ `auth`

### [DEC-9] Token storage  ·  2026-05-21  ·  [decided]
**Context.** ...
**Chosen.** `cookie` (by manager)
**Rationale.** Mobile budget resolved via short-form JWT; revocation via central blocklist.
**Supersedes.** [DEC-7]
_Requested by_ `auth`
<!-- DCAM:DECISIONS:END -->

<!-- DCAM:LESSONS:START -->
## Lessons learnt

### design
- Always validate input at system boundaries... _(2026-05-21)_

### testing
- Run integration tests against a real DB... _(2026-05-21)_
<!-- DCAM:LESSONS:END -->
```

Properties:

- **Idempotent.** Re-running `dcam tmux persist` produces a byte-identical
  file when nothing changed.
- **Non-destructive.** Anything outside the markers is untouched. Hand-
  written project notes coexist with managed content.
- **Source of truth is the parquet table.** Markdown is a regenerated
  view. Editing inside the markers will be overwritten on the next
  persist; edit the underlying decision instead (see [Editing
  decisions](#editing-decisions-and-lessons)).

To regenerate without making any other change:

```bash
dcam tmux persist --target claude        # CLAUDE.md only
dcam tmux persist --target agents        # AGENTS.md only
dcam tmux persist --target both          # both files
```

## Editing decisions and lessons

The parquet rows are the source of truth.

- **To revise a decision**, use `dcam tmux decide --supersedes <id>` (see
  above). This preserves the audit trail.
- **To withdraw an open decision** that turned out not to need an answer,
  there is no first-class CLI for it yet; mark it manually via
  `Decision.status = withdrawn` in a Python script if you must, or just
  let it stay open and ignore it.
- **To edit a lesson**, the simplest path today is to record a corrected
  lesson and let the old one stay. (A `lesson edit` command can be added
  if needed.)

## Reference: every `dcam tmux` command

| Command                                           | Who   | Purpose                                                   |
|---------------------------------------------------|-------|-----------------------------------------------------------|
| `start <session> [--launch]`                      | human | Create tmux session + `manager` window                    |
| `dev <session> <slug> "<brief>" [--launch]`       | mgr   | Spawn `dev-<slug>` window + create `role:dev` bd task     |
| `review <session> [--launch]`                     | mgr   | Spawn `review` window                                     |
| `status <session>`                                | mgr   | List windows + open `role:dev` bd tasks                   |
| `send <session> <window> "<text>"`                | mgr   | tmux send-keys to a window's pane                         |
| `capture <session> <window> [--tail N]`           | mgr   | Capture a window's pane buffer                            |
| `update <slug> "<msg>"`                           | dev   | Post a `[status]` comment on the dev's bd task            |
| `ask <slug> "<title>" --context --options [--recommend]` | dev   | Request a manager decision                          |
| `decide --id N --choice K --rationale "..." [--persist claude]`  | mgr   | Resolve an open decision                          |
| `decide --supersedes N --choice K --rationale "..." [--persist]` | mgr   | Revise a prior decision                           |
| `decisions list [--status open\|decided\|superseded\|withdrawn]` | any   | Browse decisions                                  |
| `decisions show <id>`                             | any   | Show decision + full chain history                        |
| `lesson "<text>" [--category --persist]`          | any   | Record a cross-session lesson                             |
| `persist [--target claude\|agents\|both\|auto]`   | any   | Re-render managed markdown sections (auto = only opted-in)|
| `msg <from> <to> "<text>"`                        | dev   | Dev-to-dev message (tmux + bd)                            |
| `dep <blocker> <blocked>`                         | any   | Mark `<blocked>` as blocked by `<blocker>`                |
| `deps <slug>`                                     | any   | Show open dev tasks (target marked)                       |

And for project-mode storage management:

| Command                                           | Purpose                                                   |
|---------------------------------------------------|-----------------------------------------------------------|
| `project init [--repo PATH] [--force]`            | Create `<repo>/.dcam/` with the standard layout           |
| `project status`                                  | Show active root, mode, and table contents                |
| `project path`                                    | Print the active DCAM storage root                        |
| `project install-hook [--repo PATH] [--force]`    | Symlink `.git/hooks/pre-commit` to auto-persist on commit |
| `project uninstall-hook [--repo PATH]`            | Remove the symlink (no-op if it isn't ours)               |

Global flags (`--namespace`, `--catalog`, `--branch`, `--search-backend`,
`--root`) work either before or after the subcommand.

## Walkthrough: building a feature with the team

Suppose we're shipping a new `/v2/sessions` API.

```bash
# 1. Manager bootstraps.
dcam tmux start sess-feature --launch

# 2. Manager (in the manager window) decomposes work and spawns two devs:
dcam tmux dev sess-feature schema "Define V2 session schema in OpenAPI" --launch
dcam tmux dev sess-feature handler "Implement /v2/sessions Coral handler" --launch

# 3. dev:handler depends on dev:schema landing first.
dcam tmux dep schema handler

# 4. dev:schema hits a fork on optional fields. Asks the manager:
#    (run inside the dev-schema window)
dcam tmux ask schema "Optional vs required device_id" \
    --context "Old V1 made device_id optional. Mobile needs it required for fraud." \
    --options "required:break old clients|optional-with-default:keep compat" \
    --recommend required

# 5. Manager triages and decides, persisting to CLAUDE.md:
dcam tmux decisions list --status open
dcam tmux decide --id 1 --choice required \
    --rationale "Mobile fraud signal requires it; old clients are <2% traffic and have a deprecation banner." \
    --persist claude

# 6. dev:schema picks up the decision (poll or via tmux send-keys), commits the OpenAPI change,
#    and posts a milestone:
dcam tmux update schema "ready for review"

# 7. Manager kicks off the reviewer:
dcam tmux review sess-feature --launch
#    The reviewer reads dev:schema's transcript, the decisions, and the diff,
#    then leaves a `[review]` bd comment.

# 8. Once green, dev:handler starts (it was blocked by dep). It might log a lesson
#    after a tricky test:
dcam tmux lesson "Coral validators run before route handlers; null device_id never reaches the controller." \
    --category testing --persist claude

# 9. After both devs close their bd tasks, manager re-persists once more for cleanliness:
dcam tmux persist --target both
```

The `decisions` and `lessons` end up in `CLAUDE.md` (and optionally
`AGENTS.md`), so the next session — humans included — can read what was
decided and why without spelunking through transcripts.

## Tips and gotchas

- **One slug = one dev.** Don't create two `dev-auth` windows. The slug
  is the join key for bd tasks, decisions, and messages.
- **Slugs are auto-cleaned.** `dcam tmux dev … "Add OAuth Flow!!"` will
  create a window named `dev-add-oauth-flow`, even if you never typed
  hyphens.
- **Decisions are non-blocking by design.** A dev should keep working
  with their best guess after `ask`. Stalling on every fork defeats the
  point of parallelism.
- **`--persist` is opt-in.** If you don't pass it, the decision/lesson
  lives in DCAM only. That's the right call for ephemeral or speculative
  decisions; flip to `--persist claude` once you're confident it's
  durable.
- **Markers are sacred.** Do not remove the
  `<!-- DCAM:DECISIONS:START -->` / `END` markers from your CLAUDE.md
  by hand. If you do, the next persist will append a new managed section
  rather than replace the old one.
- **No tmux? Most commands still work.** `ask`, `decide`, `decisions
  list/show`, `lesson`, and `persist` only touch the parquet store and
  markdown — useful even from a single Claude Code session as a
  decision-tracking system.
- **bd is optional.** If `bd` isn't initialized, `[ask]`/`[status]`/
  `[review]`/`[decide]` comments are silently skipped. The decisions
  themselves still record correctly in DCAM.

## Roadmap (deferred)

Things this layer deliberately doesn't do yet:

- LLM-generated decision summaries (heuristic-only today).
- Tool-call I/O capture for MCP tools (we record the tool name, not the
  args/result).
- A full `bd` dependency-tree walker; `dcam tmux deps` lists tasks but
  doesn't recurse the graph.
- A continuous reviewer that polls. Today it's on-demand.
- A `lesson edit`/`decision withdraw` first-class CLI. Workarounds above.

If you want any of these prioritized, file an issue or add a
[lesson](#lessons-learnt-cross-session-knowledge) explaining the use
case.
