# DCAM project memory

This directory holds persistent memory captured by
[DeltaCATAgentMemory](https://github.com/Zyiqin-Miranda/DeltaCATAgentMemory)
for this project. It is intentionally split into two zones:

## Committed (review with the code)

These three JSON files are the source of truth for team-facing memory and
are designed to be reviewed in pull requests:

- `decisions.json` — every architectural / scope / business-logic
  decision the agent team has resolved, including superseded revisions.
- `lessons.json` — durable learnings (design, testing, ops) captured by
  agents during work.
- `critical_points.json` — forward-looking invariants and guard rails
  ("never run integ tests under admin creds") that the reviewer checks
  on every run.
- `review_requests.json` — devs' pull-based asks for reviewer feedback.
- `reviews.json` — completed-review records (audit of who reviewed what
  and what new lessons / critical points came out of it).
- `handoffs.json` — structured peer-to-peer handoffs between dev slugs.
- `specs.json` — registered markdown spec files; tracks content hash and
  the most recent decision linked, so `dcam tmux spec drift` can flag
  specs whose linked decisions have changed since last sync.
- `sessions.json` — one row per Claude Code session, with the structured
  heuristic summary (files touched, commands run, URLs/tickets, errors).

## Local only (gitignored)

- `tables/<namespace>/chat_messages.parquet` — full message transcripts.
  These can be large, may contain ephemeral debugging chatter, and are
  not designed for human review. They stay on the contributor's machine.
- `tables/<namespace>/memories.parquet`,
  `tables/<namespace>/compact_chunks.parquet`,
  `tables/<namespace>/compact_files.parquet` — additional indices that
  are derivable from the workspace and don't belong in version control.

## How to use

From any subdirectory of this repo, the `dcam` CLI auto-discovers this
`.dcam/` root by walking up from the current working directory. Examples:

    dcam tmux decisions list
    dcam tmux ask <slug> "..." --options "A:...|B:..."
    dcam tmux decide --id 7 --choice A --rationale "..." --persist claude

To explicitly target this root from outside the repo:

    dcam --root /path/to/repo/.dcam <subcommand>

To see which root the CLI is using right now:

    dcam project path

## Selective startup

Follow `docs/MEMORY_SCOPING.md` in the repository. Save new handoffs with an
explicit task scope. Discover matching metadata and recall selected records;
do not load every project/session record or copy the shared archive here.
