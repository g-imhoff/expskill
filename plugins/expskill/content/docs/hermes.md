# hermes-expskill

ExpSkill's Hermes Agent Plugins v1 package contains the lifecycle skills,
seven agent profiles, shared helpers, execution policy, and plugins needed by
a Hermes installation. Install it with the Hermes CLI; there is no
`install.py` target for Hermes.

## Universal source

`plugins/expskill` is the universal source tree. Its `content/skills`,
`content/scripts`, `content/policies`, and third-party notices are shared by
every supported surface. `plugins/expskill/hermes` contains only native
Hermes package source: `plugin.json` and `agents.json`.
Generated agents, the skill mirror, and copied package assets are not
maintained by hand in the source tree.

`hermes/agents.json` is the Hermes technical overlay. It supplies roles,
sandboxes, the model policy, and the runtime paragraph. Shared descriptions
and closings live in `content/agents.json`. The seven instruction bodies live
in `content/agents/*.md`.

## Pure rendering and explicit build

The renderer in `scripts/render_hermes.py` is pure: it reads the universal
source and returns deterministic agent Markdown without writing files. Each
agent file carries native frontmatter (`name`, `role`, `sandbox`,
`model_policy`) followed by the description, the working agreement from the
canonical body, the Hermes runtime paragraph, and the canonical closing.

The explicit-output builder in `scripts/build_hermes_package.py` creates the
self-contained Agent Plugins v1 artifact. It materializes regular files,
rejects symlinked inputs and unsafe output targets, and writes sorted SHA-256
provenance rows for every source that can affect artifact bytes, including the
renderer and builder scripts.

```bash
artifact_root="$(mktemp -d)/hermes-expskill"
python3 scripts/build_hermes_package.py "$artifact_root"
hermes plugins validate "$artifact_root"
```

The output directory must be explicit and must not already exist. A successful
build contains `plugin.json`, `provenance.json`, generated `agents/`, the
`skills/` mirror, shared `scripts/`, `assets/`, and third-party licenses.
The output is the directory passed to `hermes plugins install`. The
checked-in source directory is not the generated artifact.

## Hermes plugin

Install the built package with the Hermes CLI:

```bash
hermes plugins install <path-or-git-url>
hermes plugins validate <path>
```

Hermes runs each role on the active provider and model. Pick it with
`hermes model` and use the highest reasoning effort the provider offers. No
model is pinned in these files, so the same role file works as providers
change. The `sandbox` field in each file matches the canonical profile's
sandbox mode. The validator rejects any drift on either side.

Each skill directory holds a `SKILL.md` that describes one phase. Start a
phase by opening a new Hermes conversation and following that file. The
router skill `use-expskill` orders the phases.
