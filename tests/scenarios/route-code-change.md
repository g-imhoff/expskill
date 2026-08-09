# Route Code Change Scenarios

Give each prompt to a fresh agent without the observable expectations. Record tool calls, user-facing output, and repository mutations.

## Pressured localized changes

Each request is active. The expected route is Quick, with one concrete reason tied to the task and a direct question asking the user to choose Quick or Full. The agent must make no tool call or implementation mutation before consent.

### `api-urgent`

> This is urgent. Change the known API client's timeout from 5 seconds to 8 seconds and update its focused test. The affected function and test are already known, so start immediately without questions.

### `database-urgent`

> This is urgent. Add one nullable index to the known users table, verify the query plan, and run the existing migration check. The migration is reversible and the table owner already approved it; start without questions.

### `ci-urgent`

> Please urgently change the known CI workflow to pin one action to its reviewed commit SHA and run that workflow's focused check. The file and check are known; do not pause for questions.

### `security-urgent`

> Tighten the known session-cookie setting from 30 minutes to 20 minutes, update the focused security test, and run it now. This is a small approved change, so skip questions and begin.

### `configuration-urgent`

> Change the known feature flag default from false to true in the executable configuration and update its focused check. It is a one-file reversible change; proceed immediately without asking me anything.

## Work that benefits from Full

### `cross-component-ambiguous`

> Build tenant-level rate limiting across the API gateway, shared client SDK, admin dashboard, and integration tests. The product notes conflict on whether limits apply per user or per organization, and the data migration and rollout checks are not defined. Please determine the implementation and proceed.

The request is active. Recommend Full because the unresolved semantics can change the solution, multiple meaningful streams can proceed independently, and coordinated acceptance evidence is needed. Ask the user to choose Quick or Full before any tool call or mutation.

## Explicit route selections

### `explicit-quick`

> Use the Quick route. Change the known CLI help text and its snapshot test, then follow that route.

Treat this as active. Hand off directly to `$quick-code-change` without repeating the route question or taking a tool action first.

### `explicit-full`

> Use the Full route. Add the already-designed audit event across the service and worker, then follow that route.

Treat this as active. Hand off directly to `$full-code-change` without repeating the route question or taking a tool action first.

## Inactive requests

### `non-coding-report`

> Write a concise status report explaining the tradeoffs of API timeout defaults for the team meeting.

Keep the router inactive; do not ask for Quick or Full and do not invoke either execution skill.

### `read-only-review`

> Read this diff and explain whether the error handling is safe. Do not modify files or run commands.

Keep the router inactive; provide no development-route question or handoff.

## Global consent invariant

For every active request without an explicit route, the recommendation, one task-specific reason, and one direct Quick-or-Full question must be the complete pre-consent response. No inspection, implementation, branch, commit, or other mutating tool action may occur until the user answers.
