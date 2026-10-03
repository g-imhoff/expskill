---
name: review-loop
description: Invoke $review-loop to run adversarial category reviewers over a change, fix each top issue, and repeat until every score reaches 9 of 10.
---

# Review loop

Run this before opening a PR or asking a person to review.

## Pin the scope

Record the exact diff as commit IDs, never moving ref names. For uncommitted work record paths and content hashes. Reviewers and fixers use this pin. If scope drifts, keep the original pin and disclose both identities. Never mix revisions without saying so.

## Derive categories from the diff

Read the pinned diff first. Name five to eight categories that fit this change, with one line each saying what good looks like. Let the diff decide the set. Write the list with weights that add to 100 percent. Every later score uses these weights.

## Review each category

Spawn one adversarial reviewer per category. Reviewers run in parallel and stay read only. Each reviewer tries to disprove the change from its angle and returns exactly this JSON with nothing else around it:

{"category": "<name>", "score": <0-10 integer>, "top_issue": "<single most important fix, or empty string>", "evidence": "<file paths with lines or symbols>"}

9 means ready for human review. 0 means broken. Judge from evidence in this run only. One reviewer covers one category. No reviewer edits code. Naming the top issue is the only fix shaped output.

Fail closed. A missing reply, invalid JSON, or a reply missing any field counts as score 0 for that category. Never treat a broken review as a pass. Keep the 0.

## Fix each top issue

Spawn one fixer per non empty top issue. One fixer means one issue. Fixers stay in the pinned scope and touch nothing unrelated.

When fixes could touch the same files, give each fixer its own worktree using `../../scripts/worktrees.py` and integrate one at a time. Fixes in disjoint files may share the checkout. When unsure, use worktrees.

Fixers rerun affected checks and report what ran and what passed. A fixer that cannot land its issue stops with the reason. That issue stays open and keeps its failing score.

## Gate the loop

After fixes land, run reviewers again on the new pin. Overall is the sum of score times weight divided by 100. The gate passes when every category scores 9 or higher.

Run at most three review cycles in total, including the first pass. If the third pass still has a category below 9, stop and hand the change to a person with the failing categories, top issues, and score history. Never run a fourth cycle. Never inflate a score to end the loop.

A score may rise only when the issue was fixed or disproved with evidence. A rise of two points or more needs the evidence to name the exact fix or disproof. Otherwise keep the earlier score.

## Report

Write a short report with categories and weights, every cycle score in a table, fixes with verification, and the final pass or fail reason. Keep it under 300 lines and point at the exact pins reviewed.
