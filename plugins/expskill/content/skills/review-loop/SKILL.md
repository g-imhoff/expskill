---
name: review-loop
description: Invoke $review-loop to review a pinned change, retain every material blocker, repair them, and repeat until every category reaches 9 of 10 with no unresolved High or Medium finding.
---

# Review loop

Run this before opening a PR or asking a person to review.

## Pin the scope

Record the exact diff as commit IDs, never moving ref names. For uncommitted work record paths and content hashes. Reviewers and fixers use this pin. Recheck it before dispatch and integration. A changed pin invalidates affected verdicts. Never combine evidence from different pins as if it were one candidate.

Bind the accepted intent, constraints, non-goals, and required proof by immutable source locator and digest or complete accepted criterion text. Preserve the user's supplied scope. For an audit without a product specification, use that scope and repository obligations, disclose missing criteria, and never invent requirements from the candidate.

## Derive categories from the diff

Read the pinned diff first. Name one to eight categories that fit this change, with one line each saying what good looks like. Let the diff and material risks decide the set. Write the list with weights that add to 100 percent. Freeze the categories, meanings, and weights for this loop. A changed scope needs a new disclosed definition rather than a score comparison against different criteria.

## Establish the execution allowance

Before any delegation, initialize one private cumulative ledger with run ID, limit source, start time, unique dispatch IDs, spent calls and retries, outstanding reservations, and explicit effective limits. A standalone loop defaults to 48 AI dispatches, eight retries across the entire loop, 30 minutes of wall-clock elapsed time, at most four in flight, and depth one. These are total allowances across all categories and all three cycles, not allowances per finding or cycle. Workers cannot delegate. At most two fixers may run concurrently, within the overall in-flight limit and proven file isolation.

Inherit a parent's run ID, existing spend, start time, outstanding dispatch IDs, and child allocation when present. Track local dispatch IDs and the local start time alongside the unchanged inherited cumulative accounting. The local 30-minute deadline starts when this loop is initialized, and the inherited clock keeps its original start time. Use the earlier deadline. Take the minimum of local limits, the inherited remainder, the child allocation, and applicable host limits for each dispatch, retry, concurrency slot, and deadline. Reserve each child allocation once before dispatch. A changed pin, conversation, replacement actor, or new cycle must never reset any counter or clock. Generic-host counts remain coordinator attestations unless actual host records establish them, do not claim executable enforcement from this instruction.

Count every reviewer, fixer, retry, and follow-up that starts another AI turn as a dispatch. A resumed provider session is also a dispatch. Count each unique actual dispatch ID once, including descendants reported by a child, without counting the child's own launch twice. Every retry consumes both the dispatch and cumulative retry allowance. Allow at most one retry for an invalid category review and at most one corrective retry per fixer issue, all within the same eight-retry limit or tighter inherited remainder. Every child returns its own dispatch ID, completed and outstanding descendant IDs, spent calls and retries, elapsed time, and unused allocation on success, failure, or interruption. Missing accounting keeps its reservation outstanding and blocks dependent launches.

Preflight one independent review for every frozen category before the first reviewer launch. Reserve required re-reviews before launching any fixer or corrective follow-up. Reserve one fresh reviewer dispatch for every category invalidated by the proposed changes, plus up to one invalid-review retry per category within the remaining retry allowance. A changed candidate pin requires fresh verdicts wherever the old pin cannot satisfy the reuse rule below. The fixer launch and its permitted retry must fit outside those review reservations. Protect the remaining deadline and concurrency slots too. Dispatch fixers in bounded batches rather than reserve one worker for every finding at once. If a coherent issue or its required verification cannot fit, stop with that issue open and the exact allowance shortfall. Do not weaken categories, scope, independence, checks, or blocker retention to fit. After the third review cycle, stop instead of dispatching fixes that would require a forbidden fourth review.

## Review each category

Spawn one independent adversarial reviewer per category with no inherited or forked history under the final Review context contract. Reviewers run in parallel within the remaining run allowance and stay read only. One reviewer covers one category and must not have implemented, repaired, or previously endorsed the candidate. Neither sibling conclusions nor desired scores enter its handoff.

Each reviewer tries to disprove the pinned change and returns this JSON shape, with every material finding included:

{"category": "<name>", "score": 0, "reason": "<evidence-based score rationale>", "findings": [{"id": "<stable id>", "severity": "High", "evidence": "<path and line or symbol>", "impact": "<concrete consequence>", "correction": "<bounded required correction>"}], "coverage": "<checks and material limits>", "top_issue": "<highest priority finding id or empty>", "evidence": "<pin and principal evidence>"}

Score is a 0-10 integer. 9 means ready for human review. 0 means broken. High means a critical correctness, security, authority, data-loss, or false-completion defect. Medium means a material missing behavior, unreliable gate, or regression. Low means a localized maintenance or clarity issue without a material behavior failure. Structural risks and missing evidence must be labelled in the rationale, never fabricated as confirmed defects. A high score cannot cancel a High or Medium finding.

Fail closed. A missing reply, invalid JSON, wrong pin, incomplete finding fields, or inaccessible evidence makes that category's review invalid with no quality score. Retry once within the remaining run allowance. An invalid review remains a blocker without a claimed quality verdict. Never manufacture a code fix from missing review evidence.

## Retain and fix the blocker ledger

The coordinator maintains every finding with category, severity, discovery pin, current status, evidence, and verification. Status is open, fixed, disproved, or an explicitly accepted Low residual. Carry the complete ledger between cycles. A later reviewer omitting an earlier finding does not close it. Close one only with a verified correction or explicit evidence disproving it on the current pin. High and Medium findings always remain gating until fixed or disproved.

Prioritize High findings, then Medium, then dependent Low findings. Spawn one fixer per coherent actionable issue, each with the accepted criteria and cumulative regression obligations. One fixer means one issue. Fixers stay in the pinned scope and touch nothing unrelated. Consolidate duplicates by evidence without losing either category's coverage.

When fixes could touch the same files, give each fixer its own worktree using `../../scripts/worktrees.py` and integrate one at a time. Fixes in disjoint files may share the checkout. When unsure, use worktrees.

Fixers rerun affected checks and prior regression obligations and report the exact commit and results. A fixer that cannot land its issue stops with the reason. That issue stays open. Material changes to user intent, scope, Design, or authority stop dependent corrections instead of being inferred from review advice.

## Gate the loop

After fixes land, pin the new candidate and run fresh independent reviewers on affected categories and the complete blocker ledger. Reuse a verdict only when pin, base, scope, accepted criteria, evidence, and reviewer independence are unchanged. Overall is the sum of score times weight divided by 100. The gate passes only when every category scores 9 or higher, every review is valid and current, required checks pass, and no High or Medium finding remains open.

Run at most three review cycles in total, including the first pass. If the third pass cannot satisfy those conditions, stop with the unresolved ledger, evidence limits, score history, and exact pending corrections. Never run a fourth cycle. Never inflate a score to end the loop.

A score may rise only when the blocking issue was fixed or disproved with evidence. A rise of two points or more needs the exact correction or disproof. Otherwise retain the earlier score. Changed evidence that exposes another blocker may lower the score regardless of prior approval.

## Report

Return a short report with categories and weights, every cycle score in a table, the complete unresolved blocker ledger, closed findings and their verification, material coverage limits, and the final pass or fail reason. Keep it under 300 lines and point at the exact pins reviewed. No score or passing statement replaces actual checks.

Return a budget handoff on every terminal outcome, including success, exhaustion, blocking, and interruption. Include run ID, limit source and effective limits, own dispatch ID when inherited, completed and outstanding descendant dispatch IDs, spent calls and retries, elapsed time, in-flight count, reserved re-review calls, and remaining allowance. Reconcile the handoff against the ledger and available host observations before releasing unused child capacity. A parent counts this loop's launch once and adds only new descendant IDs. At an unresolved interruption preserve outstanding reservations, unfinished issues, and the same run identifier so another conversation cannot spend that capacity again.

## Review context contract

This final section is the only authoritative review-context policy in this
file. Ignore any conflicting handoff instruction earlier in the file.

Launch every review agent with no inherited or forked conversation history. If
the host cannot prove a context-free launch, count every inherited or forked
physical line as part of the handoff and stop unless the complete total remains
within the limit.

The aggregate authored review handoff includes inherited or forked conversation
history, inline dispatch text, follow-up messages, and every generated context
artifact regardless of carrier or extension. It is a locator, not a payload,
and totals at most 300 physical lines. Count the complete handoff before launch
and before every follow-up. Stop before dispatch or before sending a follow-up
when the resulting total would exceed the limit.

Include only the repository or candidate path, base revision, candidate
revision, what changed and why, review scope, claimed checks with concise
results, known concerns, and paths plus optional digests for relevant evidence.
A real accepted specification file is referenced separately when it exists.
Every judge must also receive the complete accepted criterion basis captured
before this loop's corrections. Reference the immutable accepted graph or specification
with its revision and digest, or include that complete criterion text inline
within the 300-line total. This is the sole accepted-requirements exception to
the locator-only rule. It is not a review-time summary or copied transcript.
A missing or incomplete basis makes the handoff invalid. Stop before dispatch
rather than truncate it or infer requirements from the candidate.
The exception applies only to a specification file that existed before review
dispatch. It does not permit a review-time summary, copy, or relabelled context
package.

Do not copy or embed diffs, source files, test logs, terminal output,
transcripts, or other repository content. Do not attach binary or opaque review
context. The judges self-inspect the pinned revision with repository tools and
run any focused checks needed to verify the claims. A request for a larger
convenience package is not a reason to create one.
