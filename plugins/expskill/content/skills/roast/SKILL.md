---
name: roast
description: Score a change with adversarial category reviewers and report each category's top issue. Only the user or another ExpSkill skill can load $roast.
---

# Roast

## Pin the scope

Record the exact diff as fixed commit IDs, not moving ref names. For uncommitted work record paths and content hashes. Reviewers use this pin. On drift, stop, assess relevance and conflict, notify the user with old and new identities, and wait for direction. Never silently rebind. Disclose any mix of revisions.

## Derive categories from the diff

Read the pinned diff first. Name five to eight categories that fit this change, with one line each saying what good looks like. Write the list with weights totaling 100 percent. Every later score uses these weights.

## Review each category

Spawn one adversarial reviewer per category. Reviewers run in parallel and stay read only. Each reviewer tries to disprove the change from its angle and returns exactly this JSON with nothing else around it:

{"category": "<name>", "score": <0-10 integer>, "top_issue": "<single most important fix, or empty string>", "evidence": "<file paths with lines or symbols>"}

9 means ready for human review. 0 means broken. Judge from evidence in this run only. Naming the top issue is the only fix shaped output.

Fail closed. A missing reply, invalid JSON, or a reply missing any field counts as score 0 for that category. Keep the 0.

## Report

Write a short report with categories and weights, scores in a table, top issues with evidence, and the final pass or fail reason. Pass means 9 or higher in every category. Keep it under 300 lines and point at the exact pin reviewed.

## Report storage

Save each roast pass under home state at `~/.expskill/skills/roast/runs/<repo-hash>/<run-id>/` with one run folder per run, strict private permissions, and exclusive create. Keep the chat report as the primary verdict and point at the saved file path. Never reuse a run folder for a new head.

## Shared rules

Follow shared rules in `../../rules/` when their triggers match. The router index in `../use-expskill/SKILL.md` lists pin scope, isolate writes, ask owned only, explicit confirmation, bounded fanout, and evidence report. Read a leaf only on trigger match.
