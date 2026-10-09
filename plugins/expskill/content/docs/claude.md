# claude-expskill

ExpSkill's Claude Code marketplace contains the lifecycle skills, seven agent
profiles, shared helpers, execution policy, and hooks needed by a Claude Code
installation. Install it with the Claude Code CLI, or pick provider `4` in the
README launcher, which tracks the published `claude-dist` branch.

## Universal source

`plugins/expskill` is the universal source tree. Its `content/skills`,
`content/scripts`, `content/policies`, and third-party notices are shared by
every supported surface. `plugins/expskill/claude` contains only native
Claude Code source: the `.claude-plugin/plugin.json` manifest template, the
`agents.json` overlay, and the `hooks/` sources.
Generated agents, the skill mirror, and copied package assets are not
maintained by hand in the source tree.

`claude/agents.json` is the Claude technical overlay. It supplies the model
family per agent, the tool allowlist per agent, and the runtime paragraph.
Shared descriptions and closings live in `content/agents.json`. The seven
instruction bodies live in `content/agents/*.md`. Codex luna roles
(designer, explorer, implementer, planner, test-engineer) map to the haiku
family and Codex sol roles (review, spec) map to opus. Read-only OpenCode
permission maps collapse to the read tool set (`Read, Grep, Glob, Bash`);
workspace-write maps add `Edit` and `Write`. Bash is retained so agents keep
the read-only git inspection entry points the OpenCode maps allow.

## Pure rendering and explicit build

The renderer in `scripts/render_claude.py` is pure: it reads the universal
source and returns deterministic agent Markdown without writing files. Each
agent file carries native frontmatter (`name`, `description`, `tools`,
`model`) followed by the canonical body, the authoring instructions, the
Claude runtime paragraph, and the canonical closing. Canonical `SKILL.md`
files are copied verbatim; no `allowed-tools` augment is needed.

The explicit-output builder in `scripts/build_claude_package.py` creates the
self-contained marketplace artifact: the plugin under `plugins/expskill`
plus the marketplace manifest. It materializes regular files, rejects
symlinked inputs and unsafe output targets, and writes sorted SHA-256
provenance rows for every source that can affect artifact bytes, including
the renderer and builder scripts.

```bash
artifact_root="$(mktemp -d)/claude-marketplace"
python3 scripts/build_claude_package.py "$artifact_root"
claude plugin validate "$artifact_root/plugins/expskill" --strict
claude plugin validate "$artifact_root" --strict
```

The output directory must be explicit and must not already exist. A successful
build contains `.claude-plugin/marketplace.json`, the plugin with
`plugin.json`, `provenance.json`, generated `agents/`, the `skills/` mirror,
shared `scripts/`, `hooks/`, `assets/`, and third-party licenses.
The checked-in source directory is not the generated artifact.

## Claude Code hooks

The plugin wires `SessionStart` and `PreCompact` to the same authoring-scope
and unslop-scope payloads. `SessionStart` receives the documented JSON
`additionalContext` envelope. `PreCompact` receives the same payload as plain
stdout text because the CLI rejects a `hookSpecificOutput` envelope for that
event. Compaction-adjacent events (a `compact` source or any PreCompact
trigger) carry the compaction reminder; every other session start carries the
session scope.

## Claude Code plugin

Validate a locally built marketplace, then install a published release (each
release publishes the built marketplace to the `claude-dist` branch):

```bash
claude plugin marketplace add https://github.com/g-imhoff/expskill.git#claude-dist
claude plugin install expskill@expskill
```

Each skill directory holds a `SKILL.md` that describes one phase. Start a
phase by invoking the skill and following that file. The router skill
`use-expskill` orders the phases.
