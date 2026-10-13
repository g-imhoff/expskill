---
name: roast
description: Invoke $roast to run adversarial category reviewers over a change and report scores with top issues.
---

# Roast

Run this before opening a PR or asking a person to review when a single review pass is enough.

## Pin the scope

Record the exact diff as commit IDs, never moving ref names. For uncommitted work record paths and content hashes. Reviewers use this pin. If scope drifts, keep the original pin and disclose both identities. Never mix revisions without saying so.

## Derive categories from the diff

Read the pinned diff first. Name five to eight categories that fit this change, with one line each saying what good looks like. Let the diff decide the set. Write the list with weights that add to 100 percent. Every later score uses these weights.

## Review each category

Spawn one adversarial reviewer per category. Reviewers run in parallel and stay read only. Each reviewer tries to disprove the change from its angle and returns exactly this JSON with nothing else around it:

{"category": "<name>", "score": <0-10 integer>, "top_issue": "<single most important fix, or empty string>", "evidence": "<file paths with lines or symbols>"}

9 means ready for human review. 0 means broken. Judge from evidence in this run only. One reviewer covers one category. No reviewer edits code. Naming the top issue is the only fix shaped output.

Fail closed. A missing reply, invalid JSON, or a reply missing any field counts as score 0 for that category. Never treat a broken review as a pass. Keep the 0.

## Report

Write a short report with categories and weights, scores in a table, top issues with evidence, and the final pass or fail reason. The pass mark is 9 or higher in every category. Keep it under 300 lines and point at the exact pin reviewed.
