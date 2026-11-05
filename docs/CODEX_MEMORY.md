# Shared Claude and Codex memory

Codex can reuse the existing DCAM store without migrating or duplicating it.
This user's local setup was established on 2026-11-05:

- Global startup instructions: `/Users/zyiqin/.codex/AGENTS.md`.
- Retrieval and provenance guide: `/Users/zyiqin/.codex/shared-memory/DCAM.md`.
- Shared store: `/Users/zyiqin/.dcam`, namespace `dcam`, catalog `local`.
- Original Claude memory remains under its project-specific `.claude/projects/`
  directory; original reusable rules remain under `.claude/rules/`.
- Codex fallback snapshots contain 67 project-memory Markdown files and 9
  reusable-rule Markdown files. Snapshots are not a live synchronization service.

Start a new Codex session to load the new global instructions. Follow the local
guide to retrieve only relevant records and verify dated facts before acting.
The imported memory text is intentionally outside this repository.

For another workstation, locate that user's actual DCAM root and Claude memory
directory, preserve originals, and create an equivalent global AGENTS.md entry
point. Do not blindly copy absolute paths, account defaults, or credentials.
Codex handoffs must be explicitly persisted to DCAM; Claude-specific hooks do
not establish automatic Codex transcript persistence.
