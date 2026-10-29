---
name: autonomous-run
description: Turn a task into an ready to review PR through $use-expskill workflow and $roast-loop workflow. Load the skill only when the user ask it.
---

## Preconditions

Run only with one task and one repository target. 

The user has to tell you which AI model he expect you to use, if he don't, read `{expskill_folder}/rules/subagent-usage.md` propose him the low, mid and high cost model and suggest him the models that fit the best from your point of view depending on the difficulty of the task.

If the idea/context given to you doesn't give you as much context needed to achieve the task, ask me all of your questions using the `$grill-me` skill. Make sure to start the workflow only once you asked all of the question needed.

## Start the workflow

Launch one initial conversation in the target repository, for this follow `{expskill_folder}/rules/subagent-usage.md` 

## Participate as the user

Explicit invocation delegates decisions needed to achieve the supplied task within the human's stated scope and constraints to you. Read proposals and their evidence before accepting them to review them adversially and make sure to take the best decisions. Give explicit answers and confirmations when requested.

Answer from the supplied context and accepted decisions. Choose reasonably within the delegation and identify assumptions. Ask the human only when a decision requires information you cannot obtain, you should still prefer to do everything autonomously when possible. Relay their answer to the requesting conversation.

The goal here is to make the complete `$use-expskill` workflow by interacting with the ai agent. You should then ask him specifically to load this skill and follow the workflow specified there.

## Deliver the draft PR

Once the `$use-expskill` workflow is done push the branch. Open the PR as a draft.

Keep the PR blank for the moment, the description and other should be written later at the exception of yodea design link provided by the `$design` skill, this allow the user to know what he should expect when the workflow is done.

## Run the roast loop

Once the `$use-expskill` workflow has finished and delivered the draft PR, load `$roast-loop` skill and run it yourself, in this conversation, targeting the delivered PR. 

If the loop makes fixes, commit them and push only to the existing non-protected PR branch, refresh the affected checks, and update the PR description.

# Make the PR ready for review

When the final push is done, make sure that the checks of the PR succeed, if not fix those issues and push them until the checks fully succeed, then make sure to load the `$fill-pr` skill to complete the PR and mark it as ready for review by using gh pr ready or the host equivalent.
