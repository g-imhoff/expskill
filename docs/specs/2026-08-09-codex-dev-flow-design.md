# Codex Dev Flow Design

## Purpose

Codex Dev Flow provides an opt-in development workflow that keeps the primary agent conversational while delegating bounded work to model-pinned subagents. It replaces project-local workflow agents and orchestration files with one private, reusable Codex plugin.

The workflow applies only to requests that change code or its executable configuration. It does not activate for reports, academic writing, general research, explanations, or read-only analysis.

## Distribution

The private `g-imhoff/codex-dev-flow` repository is a local Codex marketplace. Its installable plugin lives under `plugins/codex-dev-flow/`. One idempotent installer registers the marketplace and links the plugin's custom agent profiles into `~/.codex/agents/`.

Codex plugins do not natively register custom subagent profiles. The linked profiles remain owned by this repository, use names prefixed with `devflow-`, and are never copied into product repositories. Installation stops rather than overwriting a conflicting user-owned profile.

The installer does not modify a product repository. A new Codex task is required after installation or profile changes so Codex can rediscover the plugin skills and custom agents.

## Model policy

The primary orchestrator is always GPT-5.6 Sol with max reasoning. It owns the conversation, route recommendation, accepted design, plan, coordination, integration, and final decisions.

Every token-heavy subagent uses GPT-5.6 Luna with max reasoning:

- `devflow-explorer` performs bounded read-only codebase investigation.
- `devflow-test-engineer` owns the overall test strategy and shared or cross-cutting acceptance tests.
- `devflow-implementer` owns one implementation task and its task-local tests.
- `devflow-verifier` runs independent checks and may create build or temporary artifacts, but never edits tracked source files.

The independent `devflow-reviewer` uses GPT-5.6 Sol with xhigh reasoning in a read-only sandbox. It reviews one coherent task or the integrated change. Its input is limited to the accepted contract, task brief, scoped diff, relevant repository policy, and concise verification evidence.

No workflow profile uses Terra or another model. The orchestrator never delegates a large execution loop to Sol. The reviewer never implements fixes.

## Route selection

When the user has not explicitly selected a route, the orchestrator must recommend Quick or Full, give one concrete reason, and ask the user to choose. It performs no implementation before that answer.

Route selection depends on the shape of the work, not the subsystem name or a generic risk label.

Quick is recommended when the change is localized, understood, reversible, and has bounded acceptance checks. A small API, database, CI, security, or configuration change can remain Quick.

Full is recommended only when at least one of these conditions makes the larger workflow useful:

- an unresolved design decision can materially change the solution;
- at least two meaningful implementation streams can proceed independently;
- important unknowns require investigation before the solution is stable;
- shared or cross-cutting test architecture is needed;
- acceptance requires coordinated evidence across components;
- failure is difficult to reproduce, diagnose, or reverse.

## Quick route

The Sol Max orchestrator performs a short plan, implementation, and focused checks in the current working tree. It does not create a commit unless the user asks.

After implementation, it starts `devflow-reviewer` and `devflow-verifier` concurrently. The reviewer inspects the bounded change while the verifier independently executes the relevant checks. Findings return to the orchestrator, which fixes them and repeats both gates.

## Full route

The Sol Max orchestrator explores the request, presents a proposition and decision recap, and waits for explicit acceptance. After acceptance, it continues autonomously unless a new important product decision appears or the same blocker survives three attempts.

It creates a dependency graph of coherent, review-sized tasks. It maximizes useful parallelism without creating artificial microtasks. Each task has one writer, an explicit file scope, acceptance criteria, dependencies, and a commit boundary.

The Luna Max test engineer defines the behavior matrix before implementation. It owns shared and cross-cutting acceptance tests. Each Luna Max implementer owns its task-local tests and implementation using red-green-refactor.

Independent tasks use separate Git worktrees and branches outside the product repository. Worktrees live under the user's state directory so repository-wide scanners do not traverse nested checkouts. Parallel writers never share a branch or overlapping file ownership.

Each completed task receives a concurrent Sol XHigh review and Luna Max verification. Confirmed findings return to the original implementer. The same reviewer and verifier recheck the corrected task.

The orchestrator integrates accepted commits in dependency order. The integrated change then receives one whole-change Sol XHigh review and one full Luna Max verification concurrently. The orchestrator adjudicates their evidence and reports the outcome.

Temporary worktrees and branches are removed only after their commits are integrated, the final checks pass, and the integration branch is clean. Unmerged or failing work is preserved with an exact recovery path.

## Agent contracts

Subagents receive the minimum task-local context needed to work independently. They read repository instructions that apply to their scope, preserve unrelated changes, and return concise evidence rather than raw logs.

The test engineer describes which production regression would make each test fail. Tests assert observable behavior and cover relevant negative, rollback, concurrency, migration, and compatibility paths.

The implementer does not expand its assigned scope, revert another writer's work, or change the accepted interface without returning the decision to the orchestrator.

The reviewer reports only actionable correctness, security, compatibility, architecture-policy, and test-quality findings. Every finding includes severity, evidence, impact, and a concrete correction. It does not create style-only findings or edit files.

The verifier records exact commands and exit results, checks the working tree for unexpected tracked changes, and distinguishes product failures from environment failures. It does not accept another agent's claim as verification evidence.

## Failure handling

Implementation or verification failures return to the agent that owns the relevant work. Follow-up turns reuse that agent rather than spawning replacements. The orchestrator asks the user immediately when a newly discovered product decision falls outside the accepted design. It asks for help after the same blocker occurs three times.

No agent deletes unmerged work, overwrites an unknown custom-agent profile, rewrites history, or performs an external write that the user did not authorize.

## Validation

The repository validates:

- plugin and marketplace schemas;
- every skill's metadata and structure;
- every custom-agent model, effort, sandbox, name, and instruction contract;
- installer idempotency, conflict refusal, and uninstall ownership;
- route classification against representative Quick, Full, and non-coding prompts;
- orchestration behavior with baseline and skill-enabled forward tests;
- external worktree creation, integration preconditions, preservation on failure, and cleanup after success.

Forward tests use disposable repositories and fresh agents. They do not expose the expected answer to the tested agent.

## Scope exclusions

Version 1 does not provide Claude support, a generic repository-cleaning command, inventory generators, GitHub pull-request automation, committed workflow reports in product repositories, or automatic commits on the Quick route.

After the plugin and linked agents are verified in a fresh Codex task, Expand may remove the project-local agent definitions, cross-runtime synchronization machinery, and orchestration instructions that the plugin replaces. Expand keeps only its repository-specific engineering policies.
