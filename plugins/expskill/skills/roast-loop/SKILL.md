---
name: roast-loop
description: Run $roast, fix each top issue, and repeat until every category scores 9 of 10. Only the user or another ExpSkill skill can load $roast-loop.
---

# Roast loop

## Pin the scope

Pin the diff and uncommitted work per `../../rules/pin-scope.md`. Reviewers and fixers use this pin. Handle drift per `../../rules/pin-scope.md` and disclose mixed revisions per `../../rules/pin-scope.md`.

## Run $roast for each review pass

Load [the `$roast` skill](../roast/SKILL.md) and follow its pin, derive, review, and report steps for one pass. Keep the category list and weights from the first pass for the whole loop. Reviewers stay read only and follow the `$roast` reviewer contract and fail closed rule. A missing reply, invalid JSON, or a reply missing any field counts as score 0 for that category.

## Fix each top issue

Spawn one fixer per non empty top issue. Confine fixers to one lane per `../../rules/isolate-writes.md`.

Give each fixer its own worktree using `../../scripts/worktrees.py` and integrate one at a time with separate checkouts per `../../rules/isolate-writes.md`.

Fixers rerun affected checks and report what ran and what passed. A fixer that cannot land its issue stops with the reason. That issue stays open and keeps its failing score.

## Gate the loop

After fixes land, re-pin and run `$roast` again on the new head per `../../rules/pin-scope.md`. Overall is the sum of score times weight divided by 100. The gate passes when every category scores 9 or higher.

Run at most three review cycles, including the first pass. If the third pass has a category below 9, stop and hand the change to a person with the failing categories, top issues, and score history. Scores stay at their evidenced values.

A score may rise only when the issue was fixed or disproved with evidence. A rise of two points or more needs the evidence to name the exact fix or disproof. Otherwise keep the earlier score.

## Report

Write a short report with categories and weights, every cycle score in a table, fixes with verification, and the final pass or fail reason. Keep it under 300 lines and point at the exact pins reviewed.
