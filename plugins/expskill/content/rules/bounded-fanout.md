# Bounded fanout

## Procedure

1. For narrow discovery with known questions, launch three independent bounded read-only workers. Brief each with repo root plus credential free facts, one bounded question, plus reading and reporting as only actions. Allow at most one targeted second round of up to three readers for a named gap or conflict. Retry a failed lane once for tool error, timeout, missing credible evidence, missing source links, or scope escape. After a second failure stop and ask the user to retry differently, continue with incomplete coverage, or skip the missing research.
2. For broad uncertain work with unknown branches, use deep research with phased branches, worker brief prompts, capture plus apply, coverage tracking, and a recorded saturated versus incomplete stop. Keep worker prompts plus findings separate. Close assignments as complete, failed, or truncated. Treat worker claims as leads until verified against an opened page or a prior accepted claim.
3. For scoring, spawn one reviewer per category with five to eight categories plus weights totaling 100 percent. Reviewers stay read only and return exactly the contract JSON with nothing else around it. Fail closed. A missing reply, invalid JSON, or a reply missing any field counts as score 0 for that category. Keep the category list plus weights from the first pass for the whole loop.
4. For fixes, spawn one fixer per non empty top issue within the pinned scope. Give each fixer its own worktree and integrate one at a time. Fixers rerun affected checks and report what ran plus what passed. A fixer that cannot land its issue stops with reason and the issue stays open.
5. The coordinator owns reconciliation. It compares findings against repository truth, merges overlapping recipes, preserves rejected alternatives with reasons, and owns user decisions. It runs at most three review cycles including the first pass. Scores stay at evidenced values. A rise of two points or more needs the evidence to name the exact fix or disproof.

## Boundary

Do not fan out across seams without disjoint ownership plus independent proof plus a join. Do not share a checkout between concurrent writers. Do not change a worker question, branch, query, or budget after dispatch. Add a new assignment to change any of them. Do not average divergent designs. Reframe plus rerun instead.

## Evidence

Report worker prompts by path or digest, replies received, retries plus failures, coverage with saturated versus incomplete status, scores per cycle with weights, fixes with verification, and the final pass or fail reason pointing at exact pins reviewed.
