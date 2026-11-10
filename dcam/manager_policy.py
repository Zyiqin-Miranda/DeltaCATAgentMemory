"""Dispatch instructions shared by project-manager launch paths."""

MANAGER_DISPATCH_POLICY = """
Project-manager dispatch workflow:
- At the start of a substantial task, identify independent workstreams, required
  inputs, dependencies and acceptance checks. Dispatch ready work early, before
  completing the entire investigation yourself.
- Start with 2-3 useful workers when independent work and capacity permit. Scale
  to available capacity when useful; count managers, workers and reviewers
  against runtime limits and account for shared build/API contention. A small
  task, dependency, authorization limit or resource conflict can justify serial
  work; state the concrete reason rather than inventing extra parallel tasks.
- Follow current user authorization and runtime constraints on delegation and
  external actions. Use the native delegation tools when provided; otherwise
  launch workers with `dcam tmux dev <session> <slug> "<brief>" --launch`.
- Assign each worker a project/task ID, objective, owned files/worktree/branch,
  prerequisites, allowed actions, acceptance checks and a distinct evidence path.
  Keep concurrent writers in separate worktrees or disjoint owned files because
  overlapping edits can corrupt results.
- Bootstrap each worker with concise core preferences, applicable topic routes,
  the scoped-memory guide and only relevant records. Require an actual read
  receipt. Prefer a fresh worker context to copying the manager's full history.
  Pass relevant source excerpts when remote workers cannot access local guides.
- Track work and dependencies in Beads. `dcam tmux dev` creates/reuses a worker
  task; do not create a second role-only task for the same work. Launch dependent
  work only after its inputs are verified.
- On worker completion, blockage or a newly ready dependency, inspect team state
  and refill available capacity with the next independent task. Reuse an idle
  worker when appropriate; do not wait for an entire batch before refilling.
- Keep the manager focused on decisions, blockers, integration and verification.
  Inspect source/diffs, exact revisions and acceptance evidence independently;
  a worker's prose or nonempty stdout does not prove completion.
- Save a concise scoped handoff with verified progress, evidence and remaining
  dependencies. Keep unrelated project history out of worker context.
"""
