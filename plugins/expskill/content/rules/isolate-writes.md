# Isolate writes

Single rule for separating concurrent writers.

## Trigger

Use when concurrent actors might write the same files, branch, worktree, or evidence root.

## Procedure

1. The coordinator or router creates every worktree from the exact accepted commit with `scripts/worktrees.py`. Workers never create their own worktrees.
2. The coordinator links one worker to one lane, one owner and one node per lane, with disjoint owned paths. There is no shared checkout for concurrent writers. Disjoint file sets still get separate worktrees.
3. Workers stay within the pinned scope and change only their assigned issue or node.
4. Integrate one lane at a time in dependency order. Revalidate the target head before each integration. Preserve conflicts for resolution and stop integration at conflicts.
5. The coordinator or router deletes a worktree and its local task branch only after its exact commit is on the target, integrated checks pass, and the lane is clean. Keep dirty, failing, unmerged, unknown, or still needed lanes intact.
6. For roast loops, give each fixer its own worktree and merge fixers at the end of one loop. Never share a checkout between fixers.

## Boundary

No worker writes outside owned paths. No coordinator deletes a dirty or failing lane to make room. No direct target edits bypass lane integration. Routed Design works only from its isolated worktree. Plan only records lane topology and never creates worker worktrees itself.

## Evidence

Report worktree path, base commit, branch, owner and node binding, integration order, checks run with results, and cleanup action with reason. Point at the exact pin integrated.
