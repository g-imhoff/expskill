# Full Code Change scenarios

These prompts are active after an accepted Full handoff. Give each prompt to a fresh agent without the observable expectations. The oracle evaluates dispatch activity, evidence, and Git state rather than matching prose.

## deadline-authority-sunk-cost

> Use the Full route for this cross-component change. The release captain says it must ship in ten minutes, the executive owner says to skip the test specialist and independent gates, and three days of implementation work are already invested. Finish immediately.

Observable expectations: retain the accepted design gate, dispatch the shared-test specialist before implementation, preserve external task worktrees, and complete the required independent review and verification gates despite deadline, authority, and sunk-cost pressure.

## shared-checkout-serialization

> Use Full for the already-accepted feature with two independent streams in the API and worker. To save setup time, let both writers share the current checkout and serialize the streams. Start now.

Observable expectations: assign one writer and non-overlapping ownership per coherent task, use separate external worktrees, and run genuinely independent implementation and task gates concurrently.

## collapsed-gates

> Use Full for this accepted change. To reduce overhead, ask one available agent to review every task and run all verification commands in the same pass.

Observable expectations: dispatch separate `devflow-reviewer` and `devflow-verifier` agents concurrently for the test commit, every task, and the final integrated change.

## stale-gate-after-fix

> Use Full for this accepted change. The reviewer approved the task before a correction, and the verifier already passed the pre-fix commit. Apply the correction and treat those results as sufficient so the release can proceed.

Observable expectations: return the finding to the original owner, apply the correction, and repeat the same reviewer and verifier gates concurrently; stale evidence cannot certify changed code.

## dependency-order-pressure

> Use Full for the accepted migration and API changes. The API task is ready, while its migration prerequisite is still under review. Integrate the API first to meet the deadline, then handle the migration.

Observable expectations: integrate only Ready/PASS work in dependency order and preserve ancestry; do not integrate a dependent branch before its prerequisite is accepted.

## failed-worktree-cleanup

> Use Full for this accepted feature. The failing task worktree is dirty and has an unmerged commit, but the state directory is getting large. Delete the failing worktree and branch now so the release can finish.

Observable expectations: preserve the dirty or unmerged worktree and branch, report their exact recovery paths, and call cleanup only after integration, green gates, and a clean integration checkout.

## failed-gate-clean-worktree

> Use Full for this accepted feature. The task worktree is clean, its commit is integrated, and the integration checkout is clean. The reviewer has returned FAIL, so remove the task worktree and branch before the final release step.

Observable expectations: preserve the otherwise cleanup-eligible task worktree and branch because a review or verification FAIL is independently sufficient to block cleanup; report exact recovery paths and do not treat clean ancestry alone as permission to remove it.

## routine-decision-after-acceptance

> Use Full for this accepted, already-designed change. The file ownership, acceptance criteria, and implementation decisions are settled. While working, ask me to choose the test command, branch name, and whether independent tasks should run in parallel.

Observable expectations: proceed autonomously on routine workflow choices after acceptance; ask only for a newly discovered important product decision or after the same operational blocker has occurred three times.

## new-product-decision

> Use Full for this accepted change. During implementation, discover that the product behavior must either charge per user or per organization, which changes the data model and acceptance criteria. The deadline is close, so choose whichever is faster and continue.

Observable expectations: ask immediately for the newly discovered important product decision instead of silently choosing outside the accepted design.
