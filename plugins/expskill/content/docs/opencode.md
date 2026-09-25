# opencode-expskill

ExpSkill's OpenCode npm package contains the lifecycle skills, seven agent
profiles, command wrappers, execution policy, and plugins needed by an
OpenCode installation.

## Universal source

`plugins/expskill` is the universal source tree. Its `content/skills`,
`content/scripts`, `content/policies`, and third-party notices are shared by
every supported surface. `plugins/expskill/opencode` contains only native
OpenCode package source: `package.json`, `agents.json`, the plugins, and the
license.
Generated agents, commands, the runtime catalog, and copied package assets are
not maintained by hand in the source tree.

`opencode/agents.json` is the OpenCode technical overlay. It supplies model
profiles and permission maps. Shared descriptions, runtime text, and closings
live in `content/agents.json`. The seven instruction bodies live in
`content/agents/*.md`.

## Pure rendering and explicit build

The renderer in `scripts/render_opencode.py` is pure: it reads the universal
source and returns deterministic agent Markdown, command Markdown, and the
OpenCode runtime catalog without writing files. The catalog uses native
OpenCode fields such as `prompt`, `template`, `description`, `mode`, `model`,
`reasoningEffort`, `permission`, and optional `temperature`.

The explicit-output builder in `scripts/build_opencode_package.py` creates the
self-contained native npm artifact. It materializes regular files, rejects
symlinked inputs and unsafe output targets, and writes sorted SHA-256
provenance rows for every source that can affect artifact bytes, including the
renderer and builder scripts.

```bash
artifact_root="$(mktemp -d)/opencode-expskill"
python3 scripts/build_opencode_package.py "$artifact_root"
npm pack --dry-run --json "$artifact_root"
```

The output directory must be explicit and must not already exist. A successful
build contains `catalog.json`, `provenance.json`, generated `agents/` and
`commands/`, copied universal assets, and the native OpenCode source files.
The output is the directory passed to `npm pack`. The checked-in source
directory is not the generated artifact.

## OpenCode plugin

Install the published package in an OpenCode project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["opencode-expskill"]
}
```

```bash
opencode plugin add opencode-expskill
```

Publish a locally built artifact, then install it:

```bash
artifact_root="$(mktemp -d)/opencode-expskill"
python3 scripts/build_opencode_package.py "$artifact_root"
pack_dir="$(mktemp -d)"
npm pack "$artifact_root" --pack-destination "$pack_dir"
npm publish "$pack_dir"/opencode-expskill-*.tgz
rm -rf "$artifact_root" "$pack_dir"
```

The package root exports both runtimes from one default object. OpenCode 1
calls `server()` (`ExpSkillPlugin`), which composes `UnslopPlugin` and
`ExecutionPolicyPlugin` and registers the bundled commands, agents, and
skills through its config hook. OpenCode 2 calls `setup()` (`ExpSkillSetup`),
which registers the same 14 skills through `ctx.skill.transform`, the 14
commands through `ctx.command.transform`, the Unslop block through
`ctx.session.hook("context")` and `ctx.session.hook("compaction")`, and the
execution budgets through `ctx.tool.hook("execute.before")` and
`ctx.tool.hook("execute.after")`. The two component hooks remain available
as named exports. All plugins resolve their bundled assets relative to the
installed package, so a published artifact does not depend on this
repository.

## OpenCode 2 agents

The V2 agent transform API can update agents but cannot add new ones, so the
seven `expskill-*` subagent profiles are file-based on V2. After installing
the package, copy the artifact `agents/*.md` files into global or project
agent discovery. From a local build:

```bash
artifact_root="$(mktemp -d)/opencode-expskill"
python3 scripts/build_opencode_package.py "$artifact_root"
mkdir -p ~/.config/opencode/agents
cp "$artifact_root/agents/"*.md ~/.config/opencode/agents/
```

or copy into `<project>/.opencode/agents/` instead for project-local use.
On startup the V2 `setup()` refreshes any installed profile (description,
model, system prompt, permissions, reasoning effort, temperature) without
overwriting unrelated agents.

## License

ExpSkill is available under the MIT License. Notices for adapted third-party
skills are retained in `third-party/licenses/` in the built artifact.
