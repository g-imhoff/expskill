---
name: brainstorm
description: Shape a vague idea into a concrete stress-tested concept in a read-only workshop with the user. Only the user or another ExpSkill skill can load $brainstorm.
---

## Precondition

The user has to give the idea he has, and if there is any context you can gather, if he didn't you should stop and ask him directly.

## Understand and confirm

1. Gather all of the context : if the user gave you places to find context, or that there is content inside of the working directory, following `{expskill_folder}/rules/subagent-usage.md`, make sure to spawn 3 low cost subagent that will go through all of it and return you only the interesting content.
2. Ask the user only for information that the discovery leaves open make it through the `$grill-me` skill.

## Research the landscape

Now that you have a deep understanding of the user idea, load the `$deep-research` skill, the goal here is to be able to gather related ideas, project, informations that could be useful to improve our idea into a concrete stress-tested concept

## Choose techniques

Then read `references/brainstorm-techniques.csv` and select a 3 brainstorming techniques, that using their description fit our case. Show a provisional itinerary naming each technique and why it fits, state that it may change, and let the user replace, add, skip, revisit, or randomize techniques.

## Explore collaboratively

Use the brainstorm techniques you picked to push on the user's idea and help make it concrete. Each time you challenge them, if they get stuck, offer your own take to keep it moving and say what your own challenge makes you think about their idea. 

Do 5-8 challenge per technique, one challenge per turn, then move to the next one. After each technique, give a compact transition summary covering discovery and concept change, ask the confirmation of your understanding following `{expskill_folder}/rules/explicit-confirmation.md`.

When all the technique are done, propose to the user 3 new brainstorming techniques to continue the brainstorm session with or to end it here and start the creation of the concept brief.

## Concept Brief

When the user chooses to consolidate and all completion gates are met, return a concise self-contained brief in this order:
- Concept snapshot : what it is, who or what it affects, and the intended change.
- How it works : the essential experience, behavior, or operating approach through the relevant domain lenses.
- Stress-test result : strongest objections, harmful outcomes, failure modes, resulting corrections, and explicitly accepted risks.
- Deferred uncertainties : Only uncertainties needing later technical design, execution, experimentation, or real-world evidence.
