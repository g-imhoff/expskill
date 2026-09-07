# opencode-expskill

ExpSkill lifecycle skills, agents, commands, and hooks for OpenCode, distributed as the npm package `opencode-expskill`.

## npm plugin

Add the package to `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["opencode-expskill"]
}
```

OpenCode installs npm plugins with Bun and loads the package root. This root
exports exactly two OpenCode plugin functions: `UnslopPlugin` and
`ExecutionPolicyPlugin`. The installed package contains its skills, helper
scripts, execution policy, and license files as regular files, so neither hook
needs the source checkout.

OpenCode's npm plugin loader activates hooks. It does not copy a package's
skills, commands, or agents into the user's config directories. Use the
repository-link installer below when you want those full lifecycle surfaces to
be discovered as local OpenCode configuration.

## Shared base

The canonical source for all ten skills is `../codex/skills`. This package keeps
a byte-identical regular-file mirror because npm tarballs do not include the
repository's former directory symlink. The package smoke test rejects missing
or changed mirrored files. Regenerate all mirrored package assets after a
canonical change:

```bash
python3 scripts/sync_opencode_package.py
python3 scripts/sync_opencode_package.py --check
```

Each shared frontmatter carries `metadata` with
`opencode/slash` and `opencode/autoinvoke` alongside the Codex `name` and
`description` fields. Nine skills are explicit only. `use-expskill` is the only
skill that may activate without an explicit invocation.

## Layout

- `index.js` package root exporting the two OpenCode plugin functions
- `skills/` regular-file mirror of the shared skill base, references, and helpers
- `scripts/` shared Design, Plan Graph, and worktree helpers used across skills
- `assets/execution-policy.json` byte-identical policy mirror used when
  `EXPSKILL_HOME` is unset
- `commands/` ten thin `/name` wrappers that load a skill through the skill tool
- `agents/` seven subagent ports of the Codex agent profiles with permission frontmatter
- `agents.json` single source of truth for the opencode agent layer: model
  profiles, temperature, permission matrices, and runtime paragraphs
- `plugins/unslop.js` model-request injector, ported from the Codex SessionStart hook
- `plugins/execution-policy.js` agent allowlists plus call, concurrency, and
  elapsed-time counters driven by the shared execution policy JSON
- `third-party/licenses/` notices for the adapted third-party skills

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

## Repository-link install

From the repository root:

```bash
python3 scripts/install.py --target opencode
python3 scripts/install.py --target opencode --dry-run
python3 scripts/install.py --target opencode --uninstall
```

The installer symlinks skills, commands, agents, and plugins into the opencode config directory and records ownership in a receipt. It never merges `opencode.json`. Agent permissions already live in the rendered agent frontmatter.

The repository installer continues to link skills directly from the canonical
`packages/codex/skills` tree. The package mirror exists only so a published npm
tarball is self-contained.

## Package verification

From the repository root:

```bash
python3 -m unittest -v tests.test_opencode_package
python3 scripts/sync_opencode_package.py --check
npm pack --dry-run --json ./packages/opencode
```

The smoke test packs the package, checks every shared asset against its
canonical source, installs the tarball into a clean temporary project, imports
`opencode-expskill`, and runs both plugin constructors without `EXPSKILL_HOME`.
It checks Unslop injection and the installed execution policy's call limit.

## License

ExpSkill is available under the MIT License in `LICENSE`. Required notices for
adapted third-party skills are retained in `third-party/licenses/`.

## Unslop hook divergence

Codex reviews a new hook through a trust prompt before running it. opencode loads local plugins at startup without that prompt. Installing this package activates the Unslop injector immediately. Before every model request, the injector checks the shared `unslop` skill and adds one complete, compact instruction block to that request's system output. The tagged block stays within 5000 characters, and the plugin reasserts the rules across compaction.
