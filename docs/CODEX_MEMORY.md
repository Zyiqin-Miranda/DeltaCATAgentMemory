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

The startup guide requires reading both memory indexes, listing central records,
and retrieving relevant topic memories. Authorized dev assignments must explicitly
carry that bootstrap and request an acknowledgement of loaded sources, including
fresh-context agents. For independent remote hosts or alternate Codex profiles,
provide relevant excerpts rather than assuming laptop paths are available.
The local topic map is `/Users/zyiqin/.codex/shared-memory/SUMMARY.md`.

Global AGENTS.md directly embeds concise verification, dev/CR, and build/deploy
preferences so they are part of every new session's instruction chain on this
profile. Detailed sources remain selective. `shared-memory/ROUTING.md` maps task
triggers to entry-point memories, requires source-read receipts, and reroutes
when scope changes. Parents must pass core rules and matched routes to devs;
receipts require review rather than assuming perfect automatic compliance.

The DCAM UX update adds metadata-aware `memory search`, stable memory handles,
timestamp/provenance/supersession display, and read-only `memory validate-refs`.
Use the guide's explicit store selectors for these commands. Markdown indexes
can now be re-compacted into heading sections with directly fetchable chunk IDs.
Restart existing DCAM MCP server processes to load the updated code/tools.
