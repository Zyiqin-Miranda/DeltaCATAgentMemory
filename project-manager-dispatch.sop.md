# Project Manager Worker Dispatch

## Overview

Use this procedure to complete an authorized project task with independent workers while the manager coordinates dependencies, decisions, integration, and verification. It applies to native agent tools or DCAM tmux teams. This document grants no delegation, production, publication, or access authority.

## Parameters

- **task_context** (required): The current user request and existing project/Beads task context, including the objective, stable task scope, repository, authorized actions, and acceptance criteria. Derive these from the active conversation, applicable instructions, and current evidence.
- **worker_target** (optional, default: "2-3"): Useful concurrent workers to aim for when independent work exists; actual runtime capacity and resource limits take precedence.
- **coordination_runtime** (optional, default: "current runtime"): Available native agent tools or an already configured DCAM tmux session, including capacity and active workers.

**Constraints for parameter acquisition:**
- You MUST obtain parameters from existing task, Beads, and runtime context before requesting input.
- If all required parameters are already provided, You MUST proceed to the Steps.
- If any required parameters are missing, You MUST ask for them before proceeding.
- When asking for parameters, You MUST request all parameters in a single prompt.
- When asking for parameters, You MUST use the exact parameter names as defined.
- You MUST treat routine implementation choices within existing authorization as manager decisions; no extra user approval is required.

## Steps

### 1. Establish the authorized dispatch boundary

Confirm the task, available runtime, and source of current delegation authority.

**Constraints:**
- You MUST check current user instructions, applicable project instructions, and runtime policies before spawning workers. Existing authorization for delegation is sufficient when its scope still applies.
- You MUST use the current task scope and existing Beads work; discover or create additional Beads work only when required by the authorized task. Beads is the task tracker.
- You MUST complete the selective memory bootstrap: read the memory guide and routing map named in the applicable project instructions, confirm `dcam project path`, discover at most five matching metadata records, then recall only matching handoffs and required topic entry points.
- You MUST inspect active workers, runtime slot limits, tool availability, and shared constraints such as build hosts, credentials, rate limits, test environments, and writable paths.
- You MUST reserve manager capacity and account for reviewers and other active agents in the same runtime limit.
- You MUST NOT spawn around a delegation prohibition or runtime limit because a speed target does not override current authority.
- You MUST validate the boundary against current instructions and runtime evidence. If delegation is unavailable or prohibited, continue authorized work serially and report the specific reason.

### 2. Form independent work units early

Decompose the objective before doing a long investigation or implementation yourself.

**Constraints:**
- You MUST identify useful independent investigation, implementation, documentation, test, or review units with explicit inputs, outputs, acceptance checks, and dependency edges.
- You MUST personally verify the factual premises needed for dispatch against current files or evidence; label unresolved premises as assumptions the worker must verify.
- You MUST assign each changed file, shared mutable artifact, branch/worktree, output path, and contested resource a single owner for each concurrent phase.
- You MUST separate worker outputs with distinct absolute paths. Preserve existing user changes and use separate work branches or isolated checkouts when development requires them.
- You MUST serialize overlapping edits and stateful operations unless evidence establishes safe isolation; independent read-only investigations may share source files.
- You SHOULD target two or three useful workers, capped by available runtime slots, independent ready units, and resource-safe concurrency.
- You MUST NOT split a trivial task or invent unrelated work merely to fill slots because dispatch overhead and integration cost can exceed the benefit.
- You MUST validate that each ready unit can progress without another worker's unfinished output. If independence fails, split its read-only preparation from its dependent changes or queue it behind the dependency.

### 3. Launch workers with complete scoped assignments

Dispatch the ready units promptly through the runtime's supported tools.

**Constraints:**
- You MUST launch two or three useful workers when that many independent units and safe slots are available, within the selected target. A smaller or serial dispatch requires a concrete reason from Step 2.
- You MUST use available native agent tools or verified DCAM tmux commands; inspect current CLI help before relying on unfamiliar syntax.
- You MUST provide each worker the objective, task scope/Beads association, owned files/resources, dependencies, absolute input/output paths, acceptance checks, relevant tool/skill instructions, and allowed actions.
- You MUST include the resolved guide path, its required selective bootstrap, matched routes, and core preferences in every assignment, including fresh-context assignments.
- You MUST require an early receipt listing actual memory files and records read before implementation. If paths are inaccessible on another host/profile, provide the relevant content explicitly.
- You MUST pass these core preferences: inspect evidence rather than assume; preserve user changes and separate development branches; verify behavior against its source; use draft CRs unless publication is authorized; verify destination/revision; run appropriate gates; rebuild dependent consumers and inspect packaged artifacts when relevant; memories are historical context, not authorization.
- You MUST set a concise result contract: status, artifact paths, revision or source snapshot, verification commands/logs, memory receipt, remaining limitations, and next dependency.
- You MUST prohibit further delegation when it is outside current authority or would overrun shared capacity.
- You MUST validate launch acknowledgement and memory receipts. If launch fails, retry only after resolving the reported cause, or continue other ready work and record the specific limitation.

### 4. Coordinate and refill the useful work

Keep workers progressing while managing decisions, dependencies, and integration.

**Constraints:**
- You MUST spend the manager's main effort on coordination and verification rather than serially performing work already owned by workers.
- You MAY perform short evidence checks, resolve decisions, prepare integration, or take a separate unit when doing so improves the critical path without delaying dispatch or colliding with ownership.
- You MUST review worker completion and blocker events, then re-evaluate ready work and safe capacity at each event. Apply Step 5 verification to each result during this loop before accepting inputs or releasing dependent work.
- You MUST refill a released slot promptly when independent useful work remains. Reuse an available worker through a supported follow-up mechanism or launch a replacement within capacity.
- You MUST resolve routine authorized decisions directly. Request user input only when a required decision exceeds existing authority or cannot be derived from evidence.
- You MUST let unblocked units continue when another unit fails or waits. A blocked live worker still consumes capacity unless the runtime confirms it has released its slot.
- You MUST avoid simultaneous writes to shared Beads state, Git checkout/index, memory stores, build outputs, or deployment/test resources unless the supported mechanism provides safe coordination.
- You MUST validate progress against actual artifacts and task state, not merely pane existence or a worker's claimed completion. If activity cannot be confirmed, request a concrete status/evidence update and redirect stalled work.

### 5. Verify and integrate worker results independently

Accept only results whose evidence supports the task's acceptance criteria.

**Constraints:**
- You MUST inspect the actual files/diffs and revision or source snapshot independently of the worker's narrative before accepting the unit.
- You MUST check test/build logs against the claimed source state, command, timing, exit status, and actual result counts; timestamps alone do not prove correctness.
- You MUST perform meaningful independent checks for the behavior or artifact being accepted, and compare source behavior when porting.
- You MUST verify the exact review revision when validating a CR. Rebuild dependent consumers and inspect packaged/runtime artifacts when relevant.
- You MUST review cross-worker interactions and run appropriate integration gates after combining changes.
- You MUST return failed verification to the owning worker with concrete evidence; other independent units continue. A separate reviewer may help when capacity permits, but its report does not replace the parent's evidence check.
- You MUST record failures, skipped gates, and remaining hypotheses accurately. A partial result does not satisfy an unmet acceptance criterion.

### 6. Close the task with a scoped handoff

Provide a verified outcome and durable continuation context.

**Constraints:**
- You MUST update Beads completion or remaining work according to the existing task workflow and authorized scope.
- You MUST persist a concise handoff in the confirmed project store when required, with explicit task scope and stable name; read it back to verify scope and content.
- You MUST include actual memory sources loaded, accepted artifact/revision references, verification evidence, limitations, remaining dependencies, and any reason for using fewer workers.
- You MUST follow applicable commit, push, and cleanup requirements for authorized repository work; a draft-only or read-only assignment does not authorize repository or external writes.
- You MUST report the result and material limitations to the parent/user only after checking the acceptance criteria. If required work remains, state its exact blocker or next owner rather than claiming completion.

## Examples

For a configured DCAM tmux session, current syntax requires both session and slug:

```bash
dcam tmux dev <session> <slug> "Read the assigned brief at <absolute-path>; report its required memory receipt before implementation." --project <absolute-repo-path> --launch
dcam tmux digest --session <session>
```

The brief contains Step 3's assignment contract. `dev` normally creates a Beads task; inspect existing task association before using it, and use `--no-task` only with a verified existing association. Do not use `--force` to bypass an active worker.

A four-slot runtime that counts the manager can support up to three workers when no reviewer or other agent uses a slot. Two workers plus a reviewer may be better when review is ready. A task with one safe implementation unit and no useful independent investigation stays serial.

## Troubleshooting

- No safe independent work: explain the actual dependency or shared owner and proceed serially.
- Resource contention: reduce concurrency for the contested resource and continue independent read-only work.
- Missing memory source: retrieve the available fallback or relevant parent-provided excerpts, disclose the gap, and avoid treating absent context as verified.
- Unsupported runtime command: inspect help and use a supported equivalent; report a capability gap rather than inventing an API.
- Worker result conflicts with evidence: retain verified artifacts, return the discrepancy to its owner, and withhold acceptance until resolved.
