"""Project-scoped storage for DCAM.

DCAM normally writes to `~/.dcam/tables/<namespace>/...` so memory is
shared across projects but can't be committed alongside team code.

When a project wants its memory to live with the source — committed,
diffable, reviewable in PRs — it can opt in by creating a `.dcam/`
directory at its repo root.

Discovery order (highest priority first):

1. `--root <path>` CLI flag (or explicit constructor arg).
2. `DCAM_ROOT` environment variable.
3. `.dcam/` walking up from the current working directory.
4. `~/.dcam/` fallback (the historical default).

Layout when project mode is active:

    <repo>/
        .dcam/
            README.md            (auto-explainer)
            .gitignore           (so we don't commit raw transcripts)
            decisions.json       (committable; primary)
            lessons.json         (committable; primary)
            sessions.json        (committable; per-session metadata + summary)
            tables/              (gitignored; per-namespace)
                <namespace>/
                    chat_messages.parquet
                    memories.parquet
                    compact_chunks.parquet
                    compact_files.parquet
"""

import os
from pathlib import Path
from typing import Optional


PROJECT_DIR_NAME = ".dcam"


# Auto-generated explainer dropped at .dcam/README.md on `dcam project init`.
PROJECT_README = """# DCAM project memory

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
"""


# What goes inside .dcam/.gitignore. Note that we do NOT ignore the JSON
# files — those are the whole point.
PROJECT_GITIGNORE = """# Local-only DCAM tables (large, ephemeral, may contain debugging chatter).
tables/

# Working files for the search backend, etc.
.cache/
*.tmp
"""


# Pre-commit hook that auto-regenerates the CLAUDE.md/AGENTS.md managed
# sections when committed JSON state changes. Lives at
# `.dcam/hooks/pre-commit` and is symlinked into `.git/hooks/` by
# `dcam project install-hook` so each contributor opts in explicitly.
PRE_COMMIT_HOOK = """#!/usr/bin/env bash
# DCAM pre-commit hook: regenerate the managed CLAUDE.md / AGENTS.md
# sections when any committed DCAM JSON file is staged. Auto-stages the
# regenerated markdown so it lands in the same commit.
#
# Skip with: git commit --no-verify

set -e

# Find the repo root + dcam dir. The hook may be invoked from any cwd.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
    exit 0
fi
DCAM_DIR="$REPO_ROOT/.dcam"
if [[ ! -d "$DCAM_DIR" ]]; then
    exit 0
fi

# No-op unless one of the committable JSON files is staged.
STAGED=$(git diff --cached --name-only --diff-filter=ACM | \\
         grep -E '^\\.dcam/(decisions|lessons|sessions|critical_points)\\.json$' || true)
if [[ -z "$STAGED" ]]; then
    exit 0
fi

# Find a dcam binary; if none, warn and let the commit proceed unchanged.
DCAM_BIN=$(command -v dcam 2>/dev/null || true)
if [[ -z "$DCAM_BIN" ]]; then
    echo "[dcam pre-commit] dcam CLI not found on PATH; skipping" \\
         "auto-persist of CLAUDE.md/AGENTS.md." >&2
    exit 0
fi

# Regenerate only files that already have DCAM markers (target=auto).
OUTPUT=$("$DCAM_BIN" --root "$DCAM_DIR" tmux persist --target auto \\
                  --project "$REPO_ROOT" 2>&1) || {
    echo "[dcam pre-commit] persist failed:" >&2
    echo "$OUTPUT" >&2
    echo "[dcam pre-commit] commit aborted. Skip with --no-verify." >&2
    exit 1
}

# Re-stage any markdown that changed.
for md in CLAUDE.md AGENTS.md; do
    if [[ -f "$REPO_ROOT/$md" ]]; then
        if ! git -C "$REPO_ROOT" diff --quiet -- "$md" 2>/dev/null; then
            git -C "$REPO_ROOT" add "$md"
            echo "[dcam pre-commit] auto-staged $md"
        fi
    fi
done

exit 0
"""


def _walk_up_for_dcam(start: Path) -> Optional[Path]:
    """Walk up the filesystem looking for a `.dcam/` directory.

    Stops at filesystem root. Returns the `.dcam/` path or None.
    """
    cur = start.resolve()
    while True:
        candidate = cur / PROJECT_DIR_NAME
        if candidate.is_dir():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def discover_root(explicit_root: Optional[str] = None,
                  cwd: Optional[Path] = None) -> Path:
    """Resolve the active DCAM root using the documented priority order.

    Returns a Path that is guaranteed to exist as a directory by the time
    a caller writes to it (we do not create here; the catalog or
    `dcam project init` does that). The caller can use `is_project_root`
    to distinguish project mode from the global fallback.
    """
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()
    env_root = os.environ.get("DCAM_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    walk = _walk_up_for_dcam(cwd or Path.cwd())
    if walk:
        return walk
    return Path.home() / PROJECT_DIR_NAME


def is_project_root(path: Path) -> bool:
    """A 'project root' is a `.dcam/` that lives anywhere except $HOME.

    The historical global fallback at `~/.dcam/` is *not* considered a
    project root for the purposes of JSON-as-primary committable storage.
    """
    path = path.resolve()
    home = (Path.home() / PROJECT_DIR_NAME).resolve()
    return path != home


def init_project(repo_path: str, namespace: str = "dcam",
                 force: bool = False) -> Path:
    """Create `<repo>/.dcam/` with the standard layout.

    - Drops a README explaining what each file is.
    - Drops a .gitignore so `tables/` stays local.
    - Creates empty decisions.json / lessons.json / sessions.json so the
      catalog has something to read on the first call.
    - Creates `tables/<namespace>/` so parquet tables have a home.

    Returns the path to the created `.dcam/` directory.
    """
    import json

    repo = Path(repo_path).resolve()
    if not repo.exists():
        raise FileNotFoundError(f"{repo} does not exist")

    root = repo / PROJECT_DIR_NAME
    root.mkdir(exist_ok=True)

    readme = root / "README.md"
    gitignore = root / ".gitignore"
    if force or not readme.exists():
        readme.write_text(PROJECT_README)
    if force or not gitignore.exists():
        gitignore.write_text(PROJECT_GITIGNORE)

    # Empty JSON files are safer than missing ones — the catalog can do
    # `json.loads(path.read_text() or "[]")` either way, but new contributors
    # see something concrete in `git status` after `dcam project init`.
    for name in ("decisions.json", "lessons.json", "sessions.json",
                 "critical_points.json"):
        f = root / name
        if force or not f.exists():
            f.write_text("[]\n")

    (root / "tables" / namespace).mkdir(parents=True, exist_ok=True)

    # Drop the pre-commit hook script alongside the project files. The
    # script itself is committed; each contributor still has to run
    # `dcam project install-hook` to symlink it into .git/hooks/.
    hooks_dir = root / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / "pre-commit"
    if force or not hook_path.exists():
        hook_path.write_text(PRE_COMMIT_HOOK)
        hook_path.chmod(0o755)

    return root


def install_hook(repo_path: str, force: bool = False) -> Path:
    """Symlink `<repo>/.git/hooks/pre-commit` to `<repo>/.dcam/hooks/pre-commit`.

    Each contributor runs this once per clone so the auto-persist hook
    fires on their commits. Refuses to overwrite an existing non-DCAM
    hook unless `force=True`.

    Returns the path to the installed hook (the symlink), so the caller
    can show it.
    """
    repo = Path(repo_path).resolve()
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        raise RuntimeError(f"{repo} is not a git repository (no .git/)")

    src = repo / PROJECT_DIR_NAME / "hooks" / "pre-commit"
    if not src.exists():
        raise RuntimeError(f"DCAM hook script missing at {src}. "
                           f"Run `dcam project init` first.")

    target_dir = git_dir / "hooks"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "pre-commit"

    # If something is already there, decide whether it's safe to replace.
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            try:
                resolved = target.resolve(strict=False)
            except OSError:
                resolved = None
            if resolved == src.resolve():
                # Already correctly installed; nothing to do.
                return target
        if not force:
            raise RuntimeError(
                f"{target} already exists. Inspect it; pass --force to "
                f"replace, or move it aside if you want to chain hooks."
            )
        target.unlink()

    # Use a relative symlink so the link is portable across clones.
    rel_src = os.path.relpath(src, target_dir)
    target.symlink_to(rel_src)
    return target


def uninstall_hook(repo_path: str) -> Optional[Path]:
    """Remove `.git/hooks/pre-commit` if and only if it points at our hook."""
    repo = Path(repo_path).resolve()
    target = repo / ".git" / "hooks" / "pre-commit"
    if not target.exists() and not target.is_symlink():
        return None
    if target.is_symlink():
        try:
            resolved = target.resolve(strict=False)
        except OSError:
            resolved = None
        expected = (repo / PROJECT_DIR_NAME / "hooks" / "pre-commit").resolve()
        if resolved == expected:
            target.unlink()
            return target
    return None
