# Isolate writes

## Procedure

1. The coordinator or router creates every worktree from the exact accepted commit with `../scripts/worktrees.py` relative to this rules folder. Only the coordinator or router creates worktrees; Plan records lane topology, and routed Design works from its isolated worktree.
2. The coordinator links one worker to one lane. One lane means one owned path set plus one task. One owner means the single worker allowed to write that lane. Give concurrent writers separate checkouts, even for disjoint file sets.
3. Workers stay within the pinned scope and change only their assigned issue or node.
4. Integrate one lane at a time in dependency order. Revalidate the target head before each integration. Preserve conflicts for resolution and stop integration at conflicts.
5. The coordinator or router deletes a worktree and its local task branch only after its exact commit is on the target, integrated checks pass, and the lane is clean. Keep dirty, failing, unmerged, unknown, or still needed lanes intact.
