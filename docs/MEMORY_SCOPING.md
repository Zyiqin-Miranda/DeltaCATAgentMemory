# Selective project, task and session memory

Load universal working preferences once. Keep investigation history outside the
always-loaded instructions.

For a new task:

1. Run `dcam project path` to confirm the selected store. Prefer the repository's
   `.dcam/` for new project/task records; use an explicit shared root only to
   retrieve relevant cross-project knowledge or older handoffs.
2. Choose a stable task scope (normally its Beads ID or external task ID).
   On installations with scoped metadata retrieval, discover up to five records with
   `dcam memory index '<task terms>' --scope '<task-id>' --limit 5 --json`.
3. Recall only relevant handles from that index. If an older record has no scope,
   inspect bounded keyword metadata in the shared store and select by task/name;
   do not fall back to loading the whole archive. Check CLI help on older
   installations: use a task ID in the topic/name and selected search/recall when
   `--scope` or `memory index` is unavailable.
4. Read matching topic entry points. Search a Markdown index for missing relevant
   links; read the operations index only for an operations task. Do not read all
   project indexes or unrelated incident histories at startup.
5. Report the actual records/files loaded and verify mutable claims against code,
   logs or current documents.

`memory context` without a selector no longer includes all project records.
`memory context --session-id '<session-id>'` includes only that session's active,
current memories, including its own persistent handoffs. It excludes other
sessions and unattached project records and limits context to 4,000 characters.
Integrations that support keyword retrieval can use
`memory context --query '<task terms>' --scope '<task-id>' --limit 5 --max-chars 4000`.
Otherwise discover metadata and recall specific handles.

Persist one concise handoff per task using `--topic`, `--scope` and a stable
`--name`; revise it with `memory update` as evidence changes. Keep decisions,
evidence references, limitations and current status, not a full transcript.
Use Beads for task tracking. Promote reusable verified lessons to shared memory
only when they apply beyond that task. No history is automatically copied from
the shared store into a new project.

Raw transcripts and local memory tables stay outside Git. Codex handoffs require
an explicit save; this workflow does not claim automatic transcript recording.
