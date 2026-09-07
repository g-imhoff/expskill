# opencode-expskill

ExpSkill lifecycle skills, agents, commands, and hooks for opencode, distributed as the npm package `opencode-expskill`.

## Shared base

The ten skills are not copied here. `skills` is a symlink to `../codex/skills`, so the exact same `SKILL.md` file serves the Codex installation and the opencode installation. Each shared frontmatter carries `metadata` with `opencode/slash` and `opencode/autoinvoke` alongside the Codex `name` and `description` fields. Nine skills are explicit only. `use-expskill` is the only skill that may activate without an explicit invocation.

## Layout

- `skills/` symlink to the shared skill base with references and Python helpers
- `commands/` ten thin `/name` wrappers that load a skill through the skill tool
- `agents/` seven subagent ports of the Codex agent profiles with permission frontmatter
- `agents.json` single source of truth for the opencode agent layer: model
  profiles, temperature, permission matrices, and runtime paragraphs
- `plugins/unslop.js` session start injector, ported from the Codex SessionStart hook
- `plugins/execution-policy.js` agent allowlists plus call, concurrency, and
  elapsed-time counters driven by the shared execution policy JSON

The plugin enforces only limits observable through opencode's task hook. Nested
subagent depth is outside the plugin and governed by opencode's agent
permissions and `subagent_depth` setting. Workflow correction retries remain
bounded by the coordinating skill. Neither limit is advertised as a
plugin-enforced policy field.

## Agent sources

Do not edit `agents/*.md` by hand. Descriptions and instructions come from the
canonical Codex profiles in `../codex/assets/agents/*.toml`. Everything
opencode specific comes from `agents.json`. Re-render after any change on
either side:

```bash
python3 scripts/sync_opencode_agents.py
python3 scripts/sync_opencode_agents.py --check
```

All seven agents pin the model and reasoning effort of the active model profile
in `agents.json`. To switch provider, change `default_model_profile` or edit a
profile, re-render, and reinstall. Validation rejects any agent file that
differs from its rendered source.

## Install

From the repository root:

```bash
python3 scripts/install.py --target opencode
python3 scripts/install.py --target opencode --dry-run
python3 scripts/install.py --target opencode --uninstall
```

The installer symlinks skills, commands, agents, and plugins into the opencode config directory and records ownership in a receipt. It never merges `opencode.json`. Agent permissions already live in the rendered agent frontmatter.

## Unslop hook divergence

Codex reviews a new hook through a trust prompt before running it. opencode loads local plugins at startup without that prompt. Installing this package activates the Unslop injector immediately. The injector reads the shared `unslop` skill at session start, caps the injected block at 5000 characters, and reasserts the rules across compaction.
