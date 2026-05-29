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
- `tmux` ≥ 1.8 supported (Amazon Linux 2's default). For best
  experience use `tmux` ≥ 3.0:
  - macOS: `brew install tmux`.
  - AL2: `sudo amazon-linux-extras enable epel && sudo yum install -y tmux3`,
    or build from source.
  Older versions work for `start/dev/review/send/capture` but may show
  cosmetic quirks (e.g. window-name disambiguation flags) that DCAM
  works around with explicit `rename-window` calls.
- The `claude` CLI on `$PATH`.
- Optional: `bd` (beads) for task tracking + dependency graph. DCAM
  auto-runs `bd init` from `dcam project init` when bd is on PATH but
  no `.beads/` exists in the repo. If bd is missing entirely, DCAM
  surfaces a one-line warning explaining what won't work.

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

## Architectures: all-in-one vs. hybrid

Pick the architecture that matches your environment **before** running
any of the commands below. The wrong choice doesn't break things
catastrophically, but the hybrid is dramatically more resilient for
the common Amazon-internal "Mac + remote cloud desk" setup.

### All-in-one (single host)

Manager + dev + reviewer all live in one tmux session on one host.

```
┌─ tmux session: <project> ─────────────────────────────────┐
│  manager  │  dev-foo  │  dev-bar  │  review               │
└───────────────────────────────────────────────────────────┘
```

Best when:

- You're on a single laptop with the codebase, tools, and credentials
  all local. No SSH layer, no remote auth.
- You're doing quick iteration where losing the whole team to a
  credential expiry or a host crash is OK.

### Hybrid: local manager + remote workers (recommended for cloud desks)

The manager runs on your local Mac in your interactive terminal. The
dev and reviewer agents run on the remote cloud desk where the code
and build tools are. The manager talks to the remote workers over
SSH, polling state via `dcam` and (optionally) reading the live event
stream via `dcam tmux watch`.

```
Local Mac:                                Remote dev desk:
┌──────────────────────────────────┐      ┌─────────────────────────────────────┐
│ Manager Claude                   │ SSH  │ tmux session (dev + reviewer only)  │
│ - In user's terminal             │ ───► │ ┌─────────────┐  ┌────────────────┐ │
│ - Polls dev/reviewer panes       │      │ │ Dev Claude  │  │ Reviewer Claude│ │
│ - Reads DCAM state via SSH       │      │ │ + codebase  │  │ + codebase     │ │
│ - Surfaces P1 issues to user     │      │ └─────────────┘  └────────────────┘ │
│   directly via terminal          │      │                                     │
│ - Manages cross-host signaling   │      │ DCAM state: <repo>/.dcam/           │
│   when dev desk creds expire     │      │ Workers SSH-accessible              │
└──────────────────────────────────┘      └─────────────────────────────────────┘
```

Why this matches reality for Amazon-internal users:

- **Credential expiry.** When AWS / midway tokens expire on the dev
  desk, every agent in an all-in-one session dies at once. The
  manager — the agent meant to *surface* auth issues — dies along
  with the workers. With a local manager, the manager stays alive
  and notices `bd list` failing across SSH; it can ask the user to
  refresh tokens directly in the terminal the user is already
  watching.

- **Out-of-band channel to the user.** A local manager already runs
  in the user's terminal. It doesn't need a tmux pane the user must
  attach to. P1 issues surface synchronously.

- **Resilience to per-host failures.** Reboot the dev desk; the
  manager survives and can re-establish coordination. Bug 14 (bd
  Dolt corruption) becomes a recovery loop instead of a total loss.

- **Role-fit.** Workers need codebase, build tools, IDE-equivalents
  → must be where the code is. The manager needs SSH, DCAM read
  access, and a terminal to talk to the user → must be where the
  user is.

### Running the hybrid pattern

Worker side, on the remote dev desk:

```bash
# As normal — start the tmux session and spawn workers there.
ssh dev-desk
cd /path/to/repo
dcam project init                       # if not already
dcam tmux start <project>
dcam tmux dev <project> auth   "Auth refactor"      --launch
dcam tmux dev <project> api    "Wire /v2/sessions"  --launch
dcam tmux review <project>                          --launch
detach the tmux session and disconnect.
```

Manager side, on the local Mac:

```bash
# Open a fresh terminal pane. Run the manager Claude here.
claude --append-system-prompt "You are the manager. Watch the remote
team via 'ssh dev-desk dcam --root /path/to/repo/.dcam tmux watch
<project>'. Surface blockers to the user. Tell devs to refresh
credentials when bd starts failing."
```

The manager Claude stays attached to your terminal. It reads remote
state via repeated SSH calls; the live event stream comes from
`dcam tmux watch <project>` (newline-delimited JSON, one event per
state change). The manager doesn't need to be a tmux pane on the
remote box — it lives where you do.

If you prefer the all-in-one pattern despite the trade-offs, the
Quick Start below works as-is on a single host.

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
rendered markdown. The audit trail is permanent. Scope (epic / op /
ticket) is automatically carried forward to the new revision unless you
override it on the new `decide` call.

### Scope: epic, op, and ticket links

Decisions, lessons, and critical points all accept three optional scope
fields:

| Flag        | Purpose                                                | Example                       |
|-------------|--------------------------------------------------------|-------------------------------|
| `--epic`    | Epic-level grouping (free-form slug)                    | `--epic native-read`          |
| `--op`      | Per-operation grouping (free-form name)                 | `--op CreateProvider`         |
| `--ticket`  | External tracker URL — any tracker works (kept generic) | `--ticket https://…`          |

Why this matters: a project with 10 epics and 60+ subtasks can't
review a flat list. Scope groups everything in `CLAUDE.md` /
`AGENTS.md` under headings like `### native-read · CreateProvider`
instead of one giant pile. The `digest` command and the reviewer's
critical-point lookups also use these fields.

Example end-to-end:

```bash
dcam tmux ask read-prov "Recorder API shape" \
    --context "Native Read Provider needs a recorder for parity tests; what API?" \
    --options "callback:per-call hook|table:write to a parquet table" \
    --recommend table \
    --epic native-read --op CreateProvider \
    --ticket https://example.com/tickets/B784B446
```

Tickets render as a generic `[↗ ticket](url)` in the markdown — no
internal-tool name leaks into committed docs.

## Critical key points: forward-looking invariants

Lessons are reactive (_we learned X_). **Critical points** are
prescriptive — invariants the team commits to upholding from now on.
The reviewer agent reads them on every run and flags violations.

```bash
dcam tmux critical add "Never run integ tests under admin creds; always Test/ReadOnly." \
    --rationale "We had prod data corruption when admin creds leaked into integ runner." \
    --epic native-read --persist claude

dcam tmux critical add "DDB Local lies about GSI consistency; integ-verify before shipping." \
    --rationale "Mock passes, prod fails — lost a week to this once." \
    --persist claude
```

When a critical point no longer applies (e.g. an SDK fix), retire it
rather than deleting:

```bash
dcam tmux critical retire 2 --reason "DDB Local matches prod GSI semantics in 2026.04 SDK." \
    --persist claude
```

Retired points stay in `critical_points.json` for audit but are dropped
from the rendered `## Critical key points` section so the reviewer's
focus is on what's actively enforced.

The reviewer prompt is wired to read this section every run; see
`REVIEWER_PROMPT` in `dcam/tmux.py`.

## Daily digest: standup-style snapshot

`dcam tmux digest` aggregates everything an agent or human needs for a
quick sync:

```bash
dcam tmux digest
```

Output covers:
- Active dev tasks with their latest `[status]` comment.
- Open decisions, grouped by `epic · op` scope.
- Recent lessons (defaults to 5; tunable via `--recent-lessons N`).
- Active critical points, counted by scope.

Useful for the manager at the top of each day instead of running
`tmux capture-pane` against every dev window.

## Live event stream: `dcam tmux watch <session>`

`digest` is a one-shot snapshot. For the hybrid (local manager +
remote workers) architecture you usually want a *stream*: the local
manager Claude reads remote state continuously and acts on changes as
they happen, instead of polling.

```bash
# On the manager-side (typically your local Mac), running over SSH:
ssh dev-desk dcam --root /path/to/repo/.dcam tmux watch <session>
```

The command emits **newline-delimited JSON** to stdout. The first
line is always a `snapshot` event carrying full current state, so a
manager that just connected sees the world. Subsequent lines are
deltas, one per affected entity.

### Event shape

```json
{"ts": "2026-05-29T...", "kind": "snapshot", "session": "<name>", "state": {...}}
{"ts": "2026-05-29T...", "kind": "decisions_added",        "id": "DEC-2", "data": {...}}
{"ts": "2026-05-29T...", "kind": "decisions_changed",      "id": "DEC-1", "data": {...}}
{"ts": "2026-05-29T...", "kind": "review_requests_added",  "id": "REQ-3", "data": {...}}
{"ts": "2026-05-29T...", "kind": "dev_tasks_changed",      "id": "<bd-task-id>", "data": {...}}
{"ts": "2026-05-29T...", "kind": "critical_points_added",  "id": "CP-4", "data": {...}}
{"ts": "2026-05-29T...", "kind": "handoffs_added",         "id": "HO-2", "data": {...}}
{"ts": "2026-05-29T...", "kind": "shutdown"}
```

`kind` is always `<entity>_<change>` where:
- `<entity>` is one of: `review_requests`, `decisions`, `critical_points`,
  `dev_tasks`, `specs`, `handoffs`, `reviews`.
- `<change>` is one of: `added`, `changed`, `removed`. (`changed`
  fires when *any* tracked field on the entity differs from the
  previous snapshot.)

The `dev_tasks_changed` event is especially useful for the manager —
it surfaces the dev's most recent `[status]` bd comment without the
manager having to `tmux capture-pane` against the dev window.

Flags:
- `--interval N` — poll every N seconds (default 5).
- `--once` — emit a single `snapshot` and exit; useful for cron-style
  polling or one-shot SSH calls.

The watch loop runs until interrupted (Ctrl-C or SIGTERM) and emits a
final `shutdown` event.

## Long-running reviewer agent

The reviewer is a **persistent** Claude Code session in the `review` tmux
window — not a polling loop. Devs explicitly pull it in by filing review
requests; the reviewer drains them at its own pace and grows its own
memory across requests via lessons and critical points.

### Why pull, not push

Polling reviewers either over-trigger (wasting tokens on no-op sweeps)
or under-trigger (missing real review needs because the heuristic
didn't fire). Pull-based puts the dev in control: when the dev's work
is at a reviewable point, *they* file the request. The reviewer agent
is then notified live through tmux send-keys + a durable
`review_requests.json` row.

### Setup

```bash
# Spawn the reviewer window. The launcher embeds a startup-context
# snippet showing all active critical points + recent review-finding
# lessons + the current pending queue, so the reviewer agent boots
# already knowing what prior reviewers learned.
dcam tmux review <session> --launch
```

Each new reviewer-session has access to the same growing context — the
reviewer literally learns over time. Rendered by `bootstrap_context()`
in `dcam/reviews.py`.

### Dev side: requesting a review

```bash
dcam tmux request-review auth-flow \
    --notes "Token refactor ready; please verify revocation path." \
    --files "src/auth/*.py,tst/auth/*.py" \
    --decisions "1,3" \
    --epic native-read --op CreateProvider \
    --tmux-session myproject
```

Captures git HEAD, the dev's slug, optional file globs, related
decisions, scope, and ticket. Files a `review_requests.json` row with
`status=pending`. If `--tmux-session` is set and the `review` window
exists, send-keys a one-line notification into the reviewer's pane.

### Reviewer side: draining the queue

```bash
dcam tmux reviews pending             # what's queued
dcam tmux reviews show <id>           # full request + the dev's notes
dcam tmux reviews claim <id>          # mark in-progress
# ... do the review using the agent's full toolkit ...
# (file reads, git diff, web/internal search, run scripts, run tests,
#  consult dcam tmux decisions show, dcam tmux critical list, etc.)

# Record durable learnings BEFORE completing (see REVIEWER_PROMPT in
# dcam/tmux.py for the workflow detail):
dcam tmux lesson "Revocation tests should hit central blocklist not local cache." \
    --category review-finding --epic native-read --op CreateProvider
dcam tmux critical add "Refresh-token revocation must be synchronous, not eventual." \
    --rationale "Async path leaves a 1-2s window where revoked tokens still authenticate." \
    --epic native-read

# Close the request:
dcam tmux reviews complete <id> \
    --summary "Logic correct; verify integ test exists for blocklist hit." \
    --blocking 0 --advisory 1 \
    --lessons-added 5 --critical-added 2 \
    --by claude-reviewer \
    --persist claude
```

### How the reviewer learns over time

1. The reviewer logs lessons with `--category review-finding`.
2. Critical points it discovers are persisted into the
   `## Critical key points` section of `CLAUDE.md` / `AGENTS.md`.
3. Next time you run `dcam tmux review <session> --launch`, the
   launcher pulls the active critical points + recent review-finding
   lessons and appends them to `REVIEWER_PROMPT`.
4. So the *next* reviewer session boots already knowing what the
   *previous* reviewer caught.

This is the "agent that grows" loop. Tools the reviewer has (because
it's a real Claude Code session, not a one-shot caller): web search,
internal code search via MCP if the project has it wired, writing and
running scratch Python or shell, reading any file, running tests, git
diff/log/show, and any other dcam command.

### Audit trail

Every completed request gets a row in `reviews.json`:

```bash
dcam tmux reviews list
# RV-1  REQ-1  claude-reviewer  [native-read · CreateProvider]  0b/1a  2026-05-22 12:35
```

This file is committable and human-reviewable in PRs alongside the
decisions and lessons it produced.

## Handoffs: structured peer-to-peer transfers

When dev A finishes a slice that dev B will pick up, a `dcam tmux msg`
is too thin: it's not durable, doesn't carry file lists or scope, and
doesn't render anywhere reviewable. **Handoffs** fill that role.

```bash
# Producer:
dcam tmux handoff create read-prov read-data \
    --files "tst/parity/recorder.py,tst/parity/normalizer.py" \
    --notes "Recorder API + normalizer rules; see DEC-1 + DEC-3." \
    --epic native-read --op ReadDataset \
    --persist claude

# Receiver acknowledges when they pick it up:
dcam tmux handoff ack 1 --notes "Picked up; will start with normalizer first." \
    --persist claude
```

Render as a managed `## Handoffs` section in `CLAUDE.md`/`AGENTS.md`,
grouped by status (pending vs acknowledged) so the team can see what
slices are flowing where.

## Specs as versioned artifacts

A `spec` is any markdown file in the repo that the team treats as a
controlled design artifact. DCAM tracks the spec's content hash and
the most recent decision linked to it. Drift detection then surfaces:

- Specs whose on-disk content hash differs from the recorded hash
  (someone edited the spec since DCAM last saw it).
- Specs that contain unresolved `<!-- DCAM:NEEDS-UPDATE: DEC-N -->`
  markers that `dcam tmux spec ref` appended when a related decision
  changed.

```bash
# Register a spec (or refresh its hash after intentional edits):
dcam tmux spec add docs/specs/router.md --epic native-read --op router \
    --title "Router scaffold spec"

# Link a decision to a spec — appends a NEEDS-UPDATE marker to the
# spec file so the next reader knows this spec must reconcile DEC-N:
dcam tmux spec ref <spec-id> <decision-id>

# Surface specs that drifted or have pending markers:
dcam tmux spec drift
```

The spec file itself stays where it logically belongs in the repo
(typically `docs/specs/...`); DCAM only tracks the path + hash, not
the content.

## Auto-extracting candidates from transcripts

The team will inevitably let a few learnings slip past `dcam tmux
lesson` / `dcam tmux critical add`. `dcam claude extract` walks a
session transcript looking for keyword markers and surfaces candidates
for explicit promotion.

```bash
# Heuristic scan over a stored Claude Code session:
dcam claude extract <session-id>

# Output saved to <storage-root>/extract_candidates.json. Then:
dcam claude extract-review --persist claude
```

The interactive review prompts you for each candidate: `[a]ccept`,
`[s]kip`, `[r]eject`. Accepted candidates are promoted to real
`Lesson`, `CriticalPoint`, or `Decision` records — and (if `--persist`)
flushed into `CLAUDE.md`/`AGENTS.md` immediately. Rejected ones stay
in the candidates buffer with `status=rejected` so the same line isn't
proposed again.

Heuristic markers (no LLM):
- **Strong** (line starts with): `lesson:`, `decision:`, `critical:`,
  `rule:`, `invariant:`, `principle:`, `gotcha:`, `watch out:`.
- **Soft** (phrase anywhere in a sentence): `from now on`, `we'll
  never`, `we should never`, `always validate`, `the rule is`,
  `learned that`, `we learned`, `next time`, `burned us`.

Strong matches yield exactly the content after the prefix. Soft
matches yield the containing sentence. The pass dedupes by
`(kind, content)` so rerunning extract on the same session is a no-op.

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
    --category testing --epic native-read --op ReadDataset --persist claude
```

Categories: `design`, `testing`, `ops`, `process`, or none. Lessons are
grouped first by `epic · op` scope (when set) and then by category in the
rendered markdown. Add `--ticket <url>` to link a lesson back to its
originating tracker item.

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

## Recovering from a corrupted bd database

If `bd list` (or anything else that touches the beads database) starts
failing with messages like:

```
[circuit-breaker] port 0: open → open (active probe failed, cooldown reset)
Error: failed to open database: dolt circuit breaker is open
```

…the in-repo Dolt sql-server backing `.beads/` has been corrupted. The
single most common trigger is a `git stash --include-untracked` cycle
that swept the live `.beads/` directory into a stash and popped it
back. The Dolt store does not survive that.

`dcam project init` (since the 2026-05-29 fixes) writes `.beads/` to
`<repo>/.git/info/exclude`, which makes git treat `.beads/` as ignored
for stash purposes. If your repo was initialized before that fix, run:

```bash
dcam project init --force            # idempotent; just refreshes the exclude
grep -F '.beads/' .git/info/exclude  # should print '.beads/'
```

### Recovery procedure (when prevention is too late)

1. Stop every Dolt sql-server process associated with bd.

   ```bash
   bd dolt stop || true
   pkill -f "dolt sql-server" || true
   ```

2. Remove stale pid/port/lock files in the repo and (often the
   actual culprit) in your home directory.

   ```bash
   rm -f ./.beads/dolt-server.pid \
         ./.beads/dolt-server.port \
         ./.beads/dolt-server.lock \
         ~/.beads/dolt-server.pid \
         ~/.beads/dolt-server.port \
         ~/.beads/dolt-server.lock
   ```

3. If the in-repo Dolt store is unrecoverable, blow it away. **You
   lose bd task history if you skipped backups.** DCAM's JSON-backed
   state (decisions, lessons, critical points, specs, handoffs,
   reviews) is unaffected — it's stored in `.dcam/*.json`.

   ```bash
   rm -rf .beads/dolt
   ```

4. Re-init.

   ```bash
   bd init
   dcam project init --force          # re-applies .git/info/exclude
   ```

5. Verify.

   ```bash
   bd list
   ```

### Avoiding the global-state collision

If `dcam project init` printed:

```
⚠ Found competing global beads dir at ~/.beads/.
```

…you have a per-user beads installation from before project mode. It
isn't itself broken, but its stale pid/port files can collide with the
in-repo bd in subtle ways during recovery probes. If recovery keeps
failing, clean it up:

```bash
ls ~/.beads/        # confirm it's not actively in use
rm -rf ~/.beads/    # only if you're not relying on it for some other repo
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
| `ask <slug> "<title>" --context --options [--recommend --epic --op --ticket]` | dev   | Request a manager decision                          |
| `decide --id N --choice K --rationale "..." [--epic --op --ticket --persist]`  | mgr   | Resolve an open decision                          |
| `decide --supersedes N --choice K --rationale "..." [--persist]` | mgr   | Revise a prior decision (scope/ticket inherit unless overridden) |
| `decisions list [--status open\|decided\|superseded\|withdrawn]` | any   | Browse decisions                                  |
| `decisions show <id>`                             | any   | Show decision + full chain history                        |
| `lesson "<text>" [--category --epic --op --ticket --persist]` | any   | Record a cross-session lesson                             |
| `critical add "<text>" [--rationale --epic --op --ticket --persist]` | any | Record a forward-looking invariant                  |
| `critical list [--status active\|retired]`        | any   | List critical points                                      |
| `critical retire <id> [--reason --persist]`       | any   | Retire a critical point (kept in JSON for audit)          |
| `digest [--recent-lessons N]`                     | any   | Standup snapshot: dev status, open decisions, lessons, CPs|
| `watch <session> [--interval N --once]`           | mgr   | Long-running NDJSON event stream (snapshot + diffs)       |
| `request-review <slug> [--notes --files --decisions --epic --op --ticket --tmux-session]` | dev | File a review request; live-notify the reviewer window  |
| `reviews pending`                                 | any   | List pending + claimed review requests                    |
| `reviews show <id>`                               | any   | Show a request and its review (if completed)              |
| `reviews claim <id> [--by]`                       | reviewer | Claim a request                                          |
| `reviews complete <id> --summary [--blocking N --advisory M --lessons-added IDs --critical-added IDs --persist]` | reviewer | Close request with a Review record |
| `reviews list`                                    | any   | Audit trail of completed reviews                          |
| `reviews withdraw <id> [--reason]`                | dev   | Withdraw a request that's no longer needed                |
| `handoff create <from> <to> [--files --notes --epic --op --ticket --persist --tmux-session]` | dev | Structured peer handoff                          |
| `handoff list [--status pending\|acknowledged]`   | any   | List handoffs                                             |
| `handoff ack <id> [--notes --persist]`            | dev   | Acknowledge a handoff                                     |
| `spec add <path> [--title --epic --op --repo]`    | any   | Register or refresh a spec by path                        |
| `spec list`                                       | any   | List registered specs                                     |
| `spec ref <spec-id> <decision-id> [--repo]`       | any   | Link a decision; appends NEEDS-UPDATE marker to the spec  |
| `spec drift [--repo]`                             | any   | List drifted specs + unresolved NEEDS-UPDATE markers      |
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
- Bidirectional sync to external trackers — `--ticket <url>` records the
  link, but DCAM doesn't post comments back to the tracker.
- A `lesson edit` / `decision withdraw` first-class CLI. Workarounds above.
- Auto-running `dcam claude extract` on SessionEnd; today it's manual.

If you want any of these prioritized, file an issue or add a
[lesson](#lessons-learnt-cross-session-knowledge) explaining the use
case.
