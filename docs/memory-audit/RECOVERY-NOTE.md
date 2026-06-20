# Recovery Note: shared-working-tree clobber + isolation (2026-06-20)

What happened, what was lost, what was recovered, and the isolation fix. Recorded for
faithful reporting (req #1) because memory data and work are precious.

## The incident

This memory mission and a concurrent "Specialized Agents" mission share ONE git working tree
(`/Users/saksham/Vscode/OpenComputerV2/OpenComputerV2`). The agents mission ran a branch
checkout/reset (reflog: `checkout: moving from feat/agents-orchestrator-mission to main`,
preceded by a `reset` and a `hermes update` autostash). That reverted every tracked file and,
via the autostash, removed my uncommitted memory work from the working tree:
- `store.py` reverted (0 occurrences of my bi-temporal / readonly code).
- New modules gone from disk (`agent/memory_merge.py`, `agent/memory_reconcile.py`,
  `tools/memory_redaction.py`).
- All `docs/memory-audit/*` gone.

NONE of it was committed (the mission had not reached a commit checkpoint), so it was pure
uncommitted working-tree state, which is exactly what a checkout/reset/clean/autostash can wipe.

## The recovery (100 percent)

The `hermes update` autostash captured the whole tree INCLUDING untracked files
(`git stash --include-untracked`). All my work was in `stash@{0}`:
- 3 tracked edits: `store.py`, `hermes_state.py`, `tools/session_search_tool.py`.
- 22 untracked files: the docs, the 3 modules, the eval skill + gold set, and 6 test files.

Recovered surgically with `git checkout stash@{0} -- <tracked>` and
`git checkout stash@{0}^3 -- <untracked>` (NOT `git stash pop`, so the stash stayed intact as a
backup until the work was safely committed). Verified: `store.py` had 9 memory-method hits
again; all modules + docs present; 286 tests passed on the recovery branch.

## The isolation fix (so it cannot recur)

1. Committed the recovered work to a branch `feat/memory-subsystem` (main-based backup),
   then to `feat/memory-mission` on the correct base.
2. Discovered local `main` is 283 commits behind `feat/agents-orchestrator-mission` (where the
   running gateway and the original build live). Main lacks features my work integrates with
   (the threat-scan memory fence #3943, the `agent-profiles` isolation).
3. On the user's decision, created a dedicated git WORKTREE at
   `/Users/saksham/Vscode/OpenComputerV2/OC-memory` on branch `feat/memory-mission`, based on
   `feat/agents-orchestrator-mission`. Each mission now has its OWN working directory and HEAD,
   so a checkout in one cannot touch the other. Set up its venv (`uv sync` + pytest).
4. Full memory baseline on the correct base: **323 passed** (more than the 299 on main, because
   the feat-base features and their tests are present).
5. Restored the shared main dir to `main` (where the agents mission left it) so this mission did
   not disturb that one.

## Standing rule going forward

ALL memory-mission work happens in the worktree `/Users/saksham/Vscode/OpenComputerV2/OC-memory`
(branch `feat/memory-mission`), with its own `.venv`. Commit after every wave so work is durable
in git, never just in the working tree. The two missions must not share a working tree again.

## Open coordination item for the user

The two missions integrate into different bases right now (memory on the feat base; the agents
mission also on feat). At final integration, decide how both merge into the product line
(upstream sync of main first, or merge both feat branches). Not blocking the memory build.
