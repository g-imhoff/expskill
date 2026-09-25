import { readFile } from "node:fs/promises";
import path from "node:path";
import { types as utilTypes } from "node:util";
import { fileURLToPath } from "node:url";

export { ExecutionPolicyPlugin } from "./plugins/execution-policy.js";
export { UnslopPlugin } from "./plugins/unslop.js";

import { ExecutionPolicyPlugin } from "./plugins/execution-policy.js";
import { UnslopPlugin } from "./plugins/unslop.js";

const PACKAGE_ROOT = path.dirname(fileURLToPath(import.meta.url));
const CATALOG_PATH = path.join(PACKAGE_ROOT, "catalog.json");
const SKILLS_PATH = path.join(PACKAGE_ROOT, "skills");
const CATALOG_SCHEMA_VERSION = "opencode-runtime.v1";
const REQUIRED_COMMANDS = [
  "autonomous-run",
  "brainstorm",
  "correct",
  "design",
  "grill-me",
  "implement",
  "plan",
  "review",
  "review-loop",
  "setup-ui-testing",
  "skill-builder",
  "test",
  "unslop",
  "use-expskill",
];
const REQUIRED_AGENTS = [
  "expskill-designer",
  "expskill-explorer",
  "expskill-implementer",
  "expskill-planner",
  "expskill-review",
  "expskill-spec",
  "expskill-test-engineer",
];

const CATALOG_KEYS = ["agents", "commands", "schema_version"];
const COMMAND_KEYS = ["description", "template"];
const DESCRIPTION_MAX_LENGTH = 160;
const AGENT_KEYS = [
  "description",
  "mode",
  "model",
  "permission",
  "prompt",
  "reasoningEffort",
];
const AGENT_KEYS_WITH_TEMPERATURE = [...AGENT_KEYS, "temperature"];
const UNSAFE_NAMES = new Set(["__proto__", "constructor", "prototype"]);
const PERMISSION_DECISIONS = new Set(["allow", "ask", "deny"]);
const hasOwn = (value, name) => Object.prototype.hasOwnProperty.call(value, name);

function isProxy(value) {
  try {
    return utilTypes.isProxy(value);
  } catch {
    return true;
  }
}

function isPlainObject(value) {
  if (typeof value !== "object" || value === null || Array.isArray(value) || isProxy(value)) {
    return false;
  }
  try {
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  } catch {
    return false;
  }
}

function ownEnumerableDataEntries(value, label) {
  let keys;
  try {
    keys = Reflect.ownKeys(value);
  } catch (error) {
    throw new Error(`${label} cannot be inspected`, { cause: error });
  }
  const entries = [];
  for (const key of keys) {
    if (typeof key !== "string") {
      throw new Error(`${label} may only contain string keys`);
    }
    let descriptor;
    try {
      descriptor = Object.getOwnPropertyDescriptor(value, key);
    } catch (error) {
      throw new Error(`${label}.${key} cannot be inspected`, { cause: error });
    }
    if (!descriptor || !descriptor.enumerable || !Object.prototype.hasOwnProperty.call(descriptor, "value")) {
      throw new Error(`${label}.${key} must be an enumerable data property`);
    }
    entries.push([
      key,
      {
        value: descriptor.value,
        writable: descriptor.writable,
        enumerable: descriptor.enumerable,
        configurable: descriptor.configurable,
      },
    ]);
  }
  return entries;
}

function exactDataEntries(value, expected, label) {
  if (!isPlainObject(value)) {
    throw new Error(`${label} must be a plain object`);
  }
  const entries = ownEnumerableDataEntries(value, label);
  const expectedNames = new Set(expected);
  if (entries.length !== expected.length || entries.some(([name]) => !expectedNames.has(name))) {
    throw new Error(`${label} has an unsupported shape`);
  }
  return entries;
}

function nonEmptyString(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
}

function validatePermission(value, label, seen = new Set()) {
  if (!isPlainObject(value) || seen.has(value)) {
    throw new Error(`${label} must be a plain object`);
  }
  seen.add(value);
  const entries = ownEnumerableDataEntries(value, label);
  if (entries.length === 0) throw new Error(`${label} must be non-empty`);
  for (const [name, descriptor] of entries) {
    if (UNSAFE_NAMES.has(name)) {
      throw new Error(`${label}.${name} has an unsafe name`);
    }
    const child = descriptor.value;
    if (typeof child === "string") {
      if (!PERMISSION_DECISIONS.has(child)) {
        throw new Error(`${label}.${name} must be allow, ask, or deny`);
      }
    } else if (isPlainObject(child)) {
      validatePermission(child, `${label}.${name}`, seen);
    } else {
      throw new Error(`${label}.${name} must be a string or plain object`);
    }
  }
  seen.delete(value);
}

function validateCommand(name, value) {
  const entries = exactDataEntries(value, COMMAND_KEYS, `catalog.commands.${name}`);
  const fields = new Map(entries.map(([field, descriptor]) => [field, descriptor.value]));
  nonEmptyString(fields.get("description"), `catalog.commands.${name}.description`);
  if (fields.get("description").length > DESCRIPTION_MAX_LENGTH) {
    throw new Error(`catalog.commands.${name}.description is too long`);
  }
  nonEmptyString(fields.get("template"), `catalog.commands.${name}.template`);
}

function validateAgent(name, value) {
  if (!isPlainObject(value)) throw new Error(`catalog.agents.${name} must be a plain object`);
  const expected = hasOwn(value, "temperature") ? AGENT_KEYS_WITH_TEMPERATURE : AGENT_KEYS;
  const exact = exactDataEntries(value, expected, `catalog.agents.${name}`);
  const fields = new Map(exact.map(([field, descriptor]) => [field, descriptor.value]));
  for (const field of ["description", "mode", "model", "reasoningEffort", "prompt"]) {
    nonEmptyString(fields.get(field), `catalog.agents.${name}.${field}`);
  }
  if (fields.get("description").length > DESCRIPTION_MAX_LENGTH) {
    throw new Error(`catalog.agents.${name}.description is too long`);
  }
  if (fields.get("mode") !== "subagent") {
    throw new Error(`catalog.agents.${name}.mode must be subagent`);
  }
  validatePermission(fields.get("permission"), `catalog.agents.${name}.permission`);
  if (fields.has("temperature") &&
      (typeof fields.get("temperature") !== "number" || !Number.isFinite(fields.get("temperature")))) {
    throw new Error(`catalog.agents.${name}.temperature must be a finite number`);
  }
}

function validateCatalog(value) {
  const top = exactDataEntries(value, CATALOG_KEYS, "catalog");
  const fields = new Map(top.map(([field, descriptor]) => [field, descriptor.value]));
  if (fields.get("schema_version") !== CATALOG_SCHEMA_VERSION) {
    throw new Error("invalid OpenCode runtime catalog schema");
  }
  const commandEntries = exactDataEntries(fields.get("commands"), REQUIRED_COMMANDS, "catalog.commands");
  const agentEntries = exactDataEntries(fields.get("agents"), REQUIRED_AGENTS, "catalog.agents");
  for (const [name, descriptor] of commandEntries) validateCommand(name, descriptor.value);
  for (const [name, descriptor] of agentEntries) validateAgent(name, descriptor.value);
  return { commands: commandEntries, agents: agentEntries };
}

async function readCatalog() {
  return validateCatalog(JSON.parse(await readFile(CATALOG_PATH, "utf8")));
}

function validateRoot(config) {
  if (!isPlainObject(config)) return null;
  let extensible;
  try {
    extensible = Object.isExtensible(config);
  } catch (error) {
    throw new Error("config root cannot be inspected", { cause: error });
  }
  ownEnumerableDataEntries(config, "config");
  const slots = new Map();
  for (const name of ["command", "agent", "skills"]) {
    let descriptor;
    try {
      descriptor = Object.getOwnPropertyDescriptor(config, name);
    } catch (error) {
      throw new Error(`config.${name} cannot be inspected`, { cause: error });
    }
    if (descriptor) {
      if (!descriptor.enumerable || !Object.prototype.hasOwnProperty.call(descriptor, "value")) {
        throw new Error(`config.${name} must be an enumerable data property`);
      }
      if (!descriptor.writable) throw new Error(`config.${name} is not writable`);
      slots.set(name, { descriptor, value: descriptor.value });
      continue;
    }
    if (name in config) throw new Error(`config.${name} has inherited state`);
    if (!extensible) throw new Error(`config.${name} cannot be added`);
    slots.set(name, { descriptor: null, value: undefined });
  }
  return { config, slots };
}

function validateUserContainer(value, label) {
  if (!isPlainObject(value)) throw new Error(`${label} must be a plain object`);
  let extensible;
  try {
    extensible = Object.isExtensible(value);
  } catch (error) {
    throw new Error(`${label} cannot be inspected`, { cause: error });
  }
  if (!extensible) throw new Error(`${label} must be extensible`);
  return ownEnumerableDataEntries(value, label);
}

function validatePaths(value, label) {
  if (!Array.isArray(value) || isProxy(value)) throw new Error(`${label} must be an array`);
  let prototype;
  try {
    prototype = Object.getPrototypeOf(value);
  } catch (error) {
    throw new Error(`${label} cannot be inspected`, { cause: error });
  }
  if (prototype !== Array.prototype && prototype !== null) {
    throw new Error(`${label} must use the built-in array prototype`);
  }
  let extensible;
  try {
    extensible = Object.isExtensible(value);
  } catch (error) {
    throw new Error(`${label} cannot be inspected`, { cause: error });
  }
  if (!extensible) throw new Error(`${label} must be extensible`);
  let keys;
  let length;
  try {
    keys = Reflect.ownKeys(value);
    const descriptor = Object.getOwnPropertyDescriptor(value, "length");
    if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, "value") ||
        descriptor.enumerable || descriptor.value < 0 || !Number.isInteger(descriptor.value)) {
      throw new Error(`${label} has an invalid length`);
    }
    length = descriptor.value;
  } catch (error) {
    throw new Error(`${label} cannot be inspected`, { cause: error });
  }
  const paths = [];
  for (const key of keys) {
    if (key === "length") continue;
    if (typeof key !== "string" || !/^\d+$/.test(key) || String(Number(key)) !== key || Number(key) >= 2 ** 32 - 1) {
      throw new Error(`${label} may only contain array entries`);
    }
  }
  for (let index = 0; index < length; index += 1) {
    const name = String(index);
    const descriptor = Object.getOwnPropertyDescriptor(value, name);
    if (!descriptor || !descriptor.enumerable || !Object.prototype.hasOwnProperty.call(descriptor, "value")) {
      throw new Error(`${label}.${name} must be an enumerable data property`);
    }
    if (typeof descriptor.value !== "string") throw new Error(`${label}.${name} must be a string`);
    paths.push(descriptor.value);
  }
  return paths;
}

function defineOwnData(target, name, descriptor) {
  if (!Reflect.defineProperty(target, name, {
    value: descriptor.value,
    writable: descriptor.writable,
    enumerable: descriptor.enumerable,
    configurable: descriptor.configurable,
  })) {
    throw new Error(`cannot define ${name}`);
  }
}

function defaultDataDescriptor(value) {
  return {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  };
}

function normalizeCatalogAgent(value) {
  const runtime = structuredClone(value);
  const { reasoningEffort } = runtime;
  delete runtime.reasoningEffort;
  runtime.options = { reasoningEffort };
  return runtime;
}

function buildReplacement(userEntries, catalogEntries, clone = structuredClone) {
  const replacement = {};
  for (const [name, descriptor] of userEntries) defineOwnData(replacement, name, descriptor);
  for (const [name, entry] of catalogEntries) {
    if (!hasOwn(replacement, name)) {
      defineOwnData(replacement, name, defaultDataDescriptor(clone(entry.value)));
    }
  }
  return replacement;
}

function normalizePathForComparison(value) {
  const root = path.parse(value).root;
  const separators = path.sep === "\\" ? /[\\/]+$/ : /\/+$/;
  let normalized = value;
  while (normalized.length > root.length && separators.test(normalized)) {
    normalized = normalized.slice(0, -1);
  }
  return normalized;
}

function isBundledSkillsPath(value) {
  return typeof value === "string" &&
    normalizePathForComparison(value) === normalizePathForComparison(SKILLS_PATH);
}

function buildSkillsReplacement(userEntries, paths) {
  const replacement = {};
  let pathsDescriptor = null;
  for (const [name, descriptor] of userEntries) {
    if (name !== "paths") defineOwnData(replacement, name, descriptor);
    else pathsDescriptor = descriptor;
  }
  const nextPaths = [];
  let bundledAdded = false;
  for (const entry of paths) {
    if (isBundledSkillsPath(entry)) {
      if (bundledAdded) continue;
      bundledAdded = true;
    }
    nextPaths.push(entry);
  }
  if (!bundledAdded) nextPaths.push(SKILLS_PATH);
  defineOwnData(replacement, "paths", {
    value: nextPaths,
    writable: pathsDescriptor?.writable ?? true,
    enumerable: pathsDescriptor?.enumerable ?? true,
    configurable: pathsDescriptor?.configurable ?? true,
  });
  return replacement;
}

function prepareConfig(config, catalog) {
  const root = validateRoot(config);
  if (!root) return null;
  const commandSlot = root.slots.get("command");
  const agentSlot = root.slots.get("agent");
  const skillsSlot = root.slots.get("skills");
  const commandEntries = commandSlot.descriptor
    ? validateUserContainer(commandSlot.value, "config.command")
    : [];
  const agentEntries = agentSlot.descriptor
    ? validateUserContainer(agentSlot.value, "config.agent")
    : [];
  let skillsEntries = [];
  let paths = [];
  if (skillsSlot.descriptor) {
    skillsEntries = validateUserContainer(skillsSlot.value, "config.skills");
    const pathsEntry = skillsEntries.find(([name]) => name === "paths");
    if (pathsEntry) paths = validatePaths(pathsEntry[1].value, "config.skills.paths");
  }
  return {
    root,
    replacements: {
      command: buildReplacement(commandEntries, catalog.commands),
      agent: buildReplacement(agentEntries, catalog.agents, normalizeCatalogAgent),
      skills: buildSkillsReplacement(skillsEntries, paths),
    },
  };
}

function publishConfig(prepared) {
  const { config, slots } = prepared.root;
  const names = ["command", "agent", "skills"];
  const previous = new Map(names.map((name) => [name, slots.get(name).descriptor]));
  const published = [];
  try {
    for (const name of names) {
      const oldDescriptor = slots.get(name).descriptor;
      const descriptor = oldDescriptor
        ? {
            value: prepared.replacements[name],
            writable: oldDescriptor.writable,
            enumerable: oldDescriptor.enumerable,
            configurable: oldDescriptor.configurable,
          }
        : {
            value: prepared.replacements[name],
            writable: true,
            enumerable: true,
            configurable: true,
          };
      if (!Reflect.defineProperty(config, name, descriptor)) {
        throw new Error(`config.${name} publication was rejected`);
      }
      published.push(name);
    }
  } catch (error) {
    for (const name of published.reverse()) {
      const oldDescriptor = previous.get(name);
      if (oldDescriptor) Reflect.defineProperty(config, name, oldDescriptor);
      else Reflect.deleteProperty(config, name);
    }
    throw error;
  }
}

function addCatalog(config, catalog) {
  const prepared = prepareConfig(config, catalog);
  if (prepared) publishConfig(prepared);
}

export const ExpSkillPlugin = async (input, options) => {
  const [unslop, executionPolicy] = await Promise.all([
    UnslopPlugin(input, options),
    ExecutionPolicyPlugin(input, options),
  ]);
  return {
    ...unslop,
    ...executionPolicy,
    config: async (config) => {
      try {
        const catalog = await readCatalog();
        addCatalog(config, catalog);
      } catch {
        return;
      }
    },
  };
};

const V2_TASK_TOOLS = new Set(["task", "subagent"]);
const V2_UNSLPO_LIMIT = 5000;
const V2_UNSLPO_OPEN = "<unslop-scope>";
const V2_UNSLPO_CLOSE = "</unslop-scope>";

async function v2ReadFirst(paths) {
  for (const candidate of paths) {
    try {
      return await readFile(candidate, "utf8");
    } catch {
      continue;
    }
  }
  throw new Error("expskill content unavailable");
}

function v2PackagePaths() {
  const skillCandidates = [
    path.join(PACKAGE_ROOT, "skills", "unslop", "SKILL.md"),
    path.join(PACKAGE_ROOT, "..", "content", "skills", "unslop", "SKILL.md"),
  ];
  const policyCandidates = [
    path.join(PACKAGE_ROOT, "assets", "unslop-runtime.json"),
    path.join(PACKAGE_ROOT, "..", "content", "policies", "unslop-runtime.json"),
  ];
  const execPolicyCandidates = [
    path.join(PACKAGE_ROOT, "assets", "execution-policy.json"),
    path.join(PACKAGE_ROOT, "..", "content", "policies", "execution-policy.json"),
  ];
  return { skillCandidates, policyCandidates, execPolicyCandidates };
}

function v2CompactSkill(contents) {
  const lines = contents.split("\n");
  if (lines.length === 0 || lines[0] !== "---") throw new Error("unslop skill frontmatter missing");
  const end = lines.indexOf("---", 1);
  if (end < 0) throw new Error("unslop skill frontmatter unclosed");
  const body = lines.slice(end + 1).join("\n").trim();
  const soulMarker = "\n## Adding soul\n";
  const patternsMarker = "\n## Patterns to detect and fix\n";
  const soulStart = body.indexOf(soulMarker);
  const patternsStart = body.indexOf(patternsMarker);
  if (soulStart < 0 || patternsStart < soulStart) throw new Error("unslop skill sections missing");
  const introduction = body.slice(0, soulStart).trim();
  const soulSection = body.slice(soulStart + soulMarker.length, patternsStart);
  const soulNames = [...soulSection.matchAll(/^- \*\*([^*]+)\*\*/gm)].map((m) => m[1]);
  if (soulNames.length === 0) throw new Error("unslop skill has no voice rules");
  const rules = [...body.matchAll(/^(\d+)\. \*\*([^*]+)\*\*\s*(.*)$/gm)].map((m) => {
    const sentences = m[3].trim().split(/(?<=[.!?])\s+/u);
    const selected = sentences.length <= 1 ? sentences : [sentences[0], sentences[sentences.length - 1]];
    return `${m[1]}. **${m[2]}** ${selected.join(" ")}`;
  });
  if (rules.length === 0) throw new Error("unslop skill has no numbered rules");
  return [introduction, `## Adding soul\n\n${soulNames.join(" ")}`, `## Patterns to detect and fix\n\n${rules.join("\n")}`].join("\n\n");
}

function v2RuntimePolicy(contents) {
  const payload = JSON.parse(contents);
  const keys = Object.keys(payload).sort();
  if (JSON.stringify(keys) !== JSON.stringify(["compaction_reminder", "schema_version", "scope"]) ||
      payload.schema_version !== "unslop-runtime.v1" ||
      typeof payload.scope !== "string" || payload.scope.trim().length === 0 ||
      typeof payload.compaction_reminder !== "string" || payload.compaction_reminder.trim().length === 0) {
    throw new Error("unslop runtime policy invalid");
  }
  return payload;
}

async function v2UnslopBlock() {
  const { skillCandidates, policyCandidates } = v2PackagePaths();
  const [skill, policyContents] = await Promise.all([
    v2ReadFirst(skillCandidates),
    v2ReadFirst(policyCandidates),
  ]);
  const policy = v2RuntimePolicy(policyContents);
  const payload = policy.scope + v2CompactSkill(skill);
  const block = `${V2_UNSLPO_OPEN}\n${payload}\n${V2_UNSLPO_CLOSE}`;
  if (block.length > V2_UNSLPO_LIMIT) throw new Error("unslop block exceeds limit");
  return { block, compactionReminder: policy.compaction_reminder };
}

function v2HasUnslop(systems) {
  return systems.some((entry) => {
    if (typeof entry === "string") return entry.includes(V2_UNSLPO_OPEN);
    if (entry && typeof entry.text === "string") return entry.text.includes(V2_UNSLPO_OPEN);
    return false;
  });
}

function v2PushSystem(systems, block) {
  if (systems.length > 0 && typeof systems[0] === "string") {
    systems[0] += `\n\n${block}`;
    return;
  }
  if (systems.length > 0 && systems[0] && typeof systems[0].text === "string") {
    systems[0].text += `\n\n${block}`;
    return;
  }
  systems.push({ type: "text", text: block });
}

function v2ExtractToolCall(event) {
  const tool = event?.tool ?? event?.input?.tool ?? event?.name ?? null;
  const args = event?.args ?? event?.input?.args ?? event?.output?.args ?? event?.payload?.args ?? {};
  const sessionID = event?.sessionID ?? event?.input?.sessionID ?? event?.sessionId ?? null;
  const callID = event?.callID ?? event?.input?.callID ?? event?.callId ?? null;
  return { tool, args, sessionID, callID };
}

function v2RequestedAgent(args) {
  if (args && typeof args === "object") {
    return args.subagent_type ?? args.agent ?? args.subagentType ?? null;
  }
  return null;
}

function v2IsExpSkillAgent(value) {
  return typeof value === "string" && value.startsWith("expskill-") && value.length > "expskill-".length;
}

const ExpSkillSetup = async (ctx) => {
  try {
    try {
      const catalog = await readCatalog();
      if (ctx?.command?.transform) {
        const entries = Array.isArray(catalog?.commands) ? catalog.commands : [];
        const defs = entries.map(([name, entry]) => [name, entry?.value ?? entry]).filter(([, d]) => d && typeof d.template === "string");
        if (defs.length > 0) {
          await ctx.command.transform((editor) => {
            for (const [name, def] of defs) {
              try {
                editor.add({
                  name,
                  description: typeof def.description === "string" ? def.description.slice(0, 160) : name,
                  execute: async (invocation) => {
                    try {
                      const promptText = invocation?.prompt?.text ?? invocation?.text ?? "";
                      const text = def.template.split("$ARGUMENTS").join(promptText);
                      await ctx.session.prompt({
                        ...(invocation?.prompt ?? {}),
                        sessionID: invocation?.sessionID,
                        text,
                        delivery: invocation?.delivery,
                      });
                    } catch {
                      return;
                    }
                  },
                });
              } catch {
                continue;
              }
            }
          });
        }
      }
    } catch {
      // commands are additive; hooks below still register
    }

    try {
      if (ctx?.session?.hook) {
        await ctx.session.hook("context", async (event) => {
          try {
            const systems = event?.system;
            if (!Array.isArray(systems) || v2HasUnslop(systems)) return;
            const { block } = await v2UnslopBlock();
            v2PushSystem(systems, block);
          } catch {
            return;
          }
        });
        for (const kind of ["compaction", "generate"]) {
          try {
            await ctx.session.hook(kind, async (event) => {
              try {
                const systems = event?.system;
                if (Array.isArray(systems)) {
                  if (v2HasUnslop(systems)) return;
                  const { block } = await v2UnslopBlock();
                  v2PushSystem(systems, block);
                  return;
                }
                const context = event?.context;
                if (Array.isArray(context)) {
                  const { compactionReminder } = await v2UnslopBlock();
                  context.push(compactionReminder);
                }
              } catch {
                return;
              }
            });
          } catch {
            continue;
          }
        }
      }
    } catch {
      // session hooks are best-effort
    }

    try {
      let policyHooks = null;
      try {
        policyHooks = await ExecutionPolicyPlugin({});
      } catch {
        policyHooks = null;
      }
      if (ctx?.tool?.hook && policyHooks) {
        const before = policyHooks["tool.execute.before"];
        const after = policyHooks["tool.execute.after"];
        await ctx.tool.hook("execute.before", async (event) => {
          try {
            const { tool, args, sessionID, callID } = v2ExtractToolCall(event);
            const agent = v2RequestedAgent(args);
            const callsTask = tool === null || tool === undefined || V2_TASK_TOOLS.has(tool) || agent !== null;
            if (!callsTask) return;
            if (!v2IsExpSkillAgent(agent)) return;
            if (typeof sessionID !== "string" || sessionID.length === 0 ||
                typeof callID !== "string" || callID.length === 0) {
              return;
            }
            await before({ tool: "task", sessionID, callID }, { args });
          } catch (error) {
            throw error;
          }
        });
        await ctx.tool.hook("execute.after", async (event) => {
          try {
            const { tool, args, sessionID, callID } = v2ExtractToolCall(event);
            if (!V2_TASK_TOOLS.has(tool) && tool !== null && tool !== undefined) return;
            if (typeof sessionID !== "string" || typeof callID !== "string") return;
            await after({ tool: tool ?? "task", sessionID, callID, args }, {});
          } catch {
            return;
          }
        });
      }
    } catch {
      // tool hooks are best-effort
    }
  } catch {
    return;
  }
};

export default {
  id: "opencode-expskill",
  server: ExpSkillPlugin,
  setup: ExpSkillSetup,
};
