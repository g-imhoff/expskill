---
name: review-loop
description: Invoke $review-loop to run adversarial category reviewers over a change, fix each top issue, and repeat until every score reaches 9 of 10.
---

# Review loop

Run this before opening a PR or asking a person to review. Review the change
by category, fix each top issue, and repeat within the cycle limit below.

## Pin the scope

Record the exact diff under review as commit IDs, never moving ref names. For
uncommitted work record paths and content hashes. Reviewers and fixers work
from this pin. If the scope drifts mid run, keep the original pin and disclose
both identities. Never mix revisions without saying so.

## Derive categories from the diff

Read the pinned diff first. Name two to five review categories that fit this
specific change, with one line each saying what good looks like. Most diffs
point at correctness, security, tests, or docs, but let the diff decide. A
docs only change needs no security reviewer. A migration with no UI needs no
visual check. Keep the set small and tied to what actually changed.

Write the category list down with weights that add to 100 percent. Every later
score uses these weights.

## Review each category

Spawn one adversarial reviewer per category. Reviewers run in parallel and stay
read only. Each reviewer tries to disprove the change from its own angle and
returns exactly this JSON, with nothing else around it:

{"category": "<name>", "score": <0-10 integer>, "top_issue": "<single most important fix, or empty string>", "evidence": "<file paths with lines or symbols>"}

Score 9 means the category is ready for human review. Score 0 means broken.
Judge from evidence in this run, never from fixed deductions or imagined
problems. One reviewer covers one category. No reviewer edits code or writes
patches. Naming the top issue is the reviewer's only fix shaped output.

Fail closed. A missing reply, a reply that is not valid JSON, or a reply
missing any field counts as score 0 for that category. Never treat a broken
review as a pass. Never average around it silently. Record the failure and keep
the 0.

## Fix each top issue

Spawn one fixer per non empty top issue. One issue means one fixer. Fixers work
in the pinned scope only and touch nothing unrelated.

When two fixes could touch the same files, give each fixer its own worktree
using `../../scripts/worktrees.py`, resolved relative to the directory containing
this loaded `SKILL.md`, and integrate them one at a time. When fixes touch
disjoint files, fixers may share the checkout. If you are unsure whether files
overlap, use worktrees. Merging two conflicting edits by hand defeats the
point.

Fixers rerun the checks their change affects and report what they ran and what
passed. A fixer that cannot land its issue stops and says so with the reason.
That issue stays open and keeps its failing score.

## Gate the loop

After fixes land, run the reviewers again on the new pin. Compute the weighted
overall as overall = sum of category score times category weight divided by
100. The gate passes when every category scores 9 or higher. One 8 fails the
gate even if the average looks fine.

Run at most three review cycles in total. The count includes the first pass. If
the third pass still has a category below 9, stop and hand the change to a
person with the failing categories, their top issues, and the complete score
history. Never run a fourth cycle to wear the scores down. Never inflate a
score to end the loop. A stale low effort approval is worse than an honest
fail.

A score may rise only when the underlying issue was fixed or disproved with
evidence. If a reviewer raises a score by two points or more between cycles,
the evidence for that category must name the exact fix or provide concrete
disproof of the issue. Otherwise keep the earlier score.

## Report

Write a short report with the category list and weights, every cycle score in
a table, the fixes applied with their verification, and the final gate result
of pass or fail with the reason. Keep it under 300 lines and point at the
exact pins reviewed.
