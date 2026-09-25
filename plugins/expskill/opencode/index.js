import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { types as utilTypes } from "node:util";
import { fileURLToPath } from "node:url";

export { ExecutionPolicyPlugin } from "./plugins/execution-policy.js";
export { UnslopPlugin } from "./plugins/unslop.js";

import { ExecutionPolicyPlugin } from "./plugins/execution-policy.js";
import { UnslopPlugin } from "./plugins/unslop.js";
import {
  buildBlock as buildUnslopBlock,
  readFirst as readFirstUnslop,
} from "./plugins/unslop.js";
import {
  TASK_TOOLS as POLICY_TASK_TOOLS,
  createBudgetTracker as createPolicyTracker,
  isExpSkillAgent as isPolicyAgent,
  loadPolicy as loadExecutionPolicy,
  requestedAgent as requestedPolicyAgent,
} from "./plugins/execution-policy.js";

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

// ---------------------------------------------------------------------------
// OpenCode V2 setup (opencode 2.x).
//
// V1 plugin functions return a `server()` hook map with a `config` hook that
// mutates `config.command` / `config.agent` / `config.skills.paths`. V2
// removed that hook: plugins export `setup(ctx)` and register through domain
// transforms and hooks instead. This section is additive; the V1 `server`
// export above is unchanged.
// ---------------------------------------------------------------------------

const V2_SKILL_POLICY_CANDIDATES = () => [
  path.join(PACKAGE_ROOT, "assets", "skill-policies.json"),
  path.join(PACKAGE_ROOT, "..", "content", "policies", "skills.json"),
];

const V2_ACTION_ALIASES = new Map([
  ["bash", "shell"],
  ["task", "subagent"],
  ["write", "edit"],
  ["patch", "edit"],
]);

function v2MapAction(action) {
  return V2_ACTION_ALIASES.get(action) ?? action;
}

function v2ParseSkillFrontmatter(contents, skillID) {
  const lines = contents.split("\n");
  if (lines.length === 0 || lines[0].trim() !== "---") {
    throw new Error(`skill ${skillID} is missing frontmatter`);
  }
  const end = lines.indexOf("---", 1);
  if (end < 0) {
    throw new Error(`skill ${skillID} frontmatter is not closed`);
  }
  let name = null;
  let description = null;
  for (const line of lines.slice(1, end)) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (value.length >= 2 && value.startsWith('"') && value.endsWith('"')) {
      value = JSON.parse(value);
    } else if (value.length >= 2 && value.startsWith("'") && value.endsWith("'")) {
      value = value.slice(1, -1);
    }
    if (key === "name") name = value;
    if (key === "description") description = value;
  }
  if (!name || !description) {
    throw new Error(`skill ${skillID} has no name or description`);
  }
  return { name, description, body: lines.slice(end + 1).join("\n").trim() };
}

async function v2ReadSkillPolicy() {
  for (const candidate of V2_SKILL_POLICY_CANDIDATES()) {
    try {
      const payload = JSON.parse(await readFile(candidate, "utf8"));
      if (payload && typeof payload.allow_implicit_invocation === "object") {
        return payload.allow_implicit_invocation;
      }
    } catch {
      continue;
    }
  }
  return null;
}

async function v2LoadSkills() {
  const policy = await v2ReadSkillPolicy();
  const entries = await readdir(SKILLS_PATH, { withFileTypes: true });
  const skills = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const skillID = entry.name;
    const skillFile = path.join(SKILLS_PATH, skillID, "SKILL.md");
    let contents;
    try {
      contents = await readFile(skillFile, "utf8");
    } catch {
      continue;
    }
    const frontmatter = v2ParseSkillFrontmatter(contents, skillID);
    const skill = {
      id: skillID,
      name: frontmatter.name,
      description: frontmatter.description,
      path: skillFile,
      content: frontmatter.body,
    };
    const implicit = policy ? policy[skillID] : skillID === "use-expskill";
    if (implicit !== true) {
      skill.autoinvoke = false;
    }
    skills.push(skill);
  }
  skills.sort((left, right) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0));
  return skills;
}

function v2FlattenPermissions(permission) {
  const rules = [];
  if (!permission || typeof permission !== "object") return rules;
  for (const [action, value] of Object.entries(permission)) {
    if (typeof value === "string") {
      rules.push({ action: v2MapAction(action), resource: "*", effect: value });
    } else if (value && typeof value === "object") {
      for (const [resource, effect] of Object.entries(value)) {
        rules.push({ action: "shell", resource, effect });
      }
    }
  }
  return rules;
}

function v2ParseModelRef(model) {
  if (typeof model !== "string") return null;
  const [providerID, rest] = model.split("/", 2);
  if (!providerID || !rest) return null;
  const [id, variant] = rest.split("#", 2);
  if (!id) return null;
  const ref = { providerID, id };
  if (variant) ref.variant = variant;
  return ref;
}

async function v2SetupSkills(ctx) {
  const skills = await v2LoadSkills();
  if (skills.length === 0) return;
  await ctx.skill.transform((editor) => {
    for (const skill of skills) {
      try {
        if (editor.get(skill.id)) continue;
      } catch {
        continue;
      }
      editor.add(skill);
    }
  });
}

async function v2SetupCommands(ctx) {
  let catalog;
  try {
    catalog = await readCatalog();
  } catch {
    return;
  }
  const entries = catalog.commands.map(([name, descriptor]) => [name, descriptor.value]);
  if (entries.length === 0) return;
  await ctx.command.transform((editor) => {
    for (const [name, definition] of entries) {
      const template = definition.template;
      const description = definition.description;
      editor.add({
        name,
        description,
        execute: async ({ sessionID, prompt, delivery }) => {
          const args = prompt?.text ?? "";
          const text = template.split("$ARGUMENTS").join(args);
          await ctx.session.prompt({ sessionID, text, delivery });
        },
      });
    }
  });
}

async function v2SetupAgents(ctx) {
  let catalog;
  try {
    catalog = await readCatalog();
  } catch {
    return;
  }
  await ctx.agent.transform((editor) => {
    for (const [name, descriptor] of catalog.agents) {
      const source = descriptor.value;
      let existing;
      try {
        existing = editor.get(name);
      } catch {
        continue;
      }
      if (!existing) continue;
      editor.update(name, (agent) => {
        agent.description = source.description;
        agent.mode = "subagent";
        const ref = v2ParseModelRef(source.model);
        if (ref) agent.model = ref;
        agent.system = source.prompt;
        agent.permissions = v2FlattenPermissions(source.permission);
        const body = {};
        if (typeof source.reasoningEffort === "string") {
          body.reasoningEffort = source.reasoningEffort;
        }
        if (typeof source.temperature === "number") {
          body.temperature = source.temperature;
        }
        agent.request = agent.request ?? { settings: {}, headers: {}, body: {} };
        agent.request.body = { ...(agent.request.body ?? {}), ...body };
      });
    }
  });
}

function v2UnslopSources() {
  const home = process.env?.EXPSKILL_HOME;
  if (typeof home === "string" && home.length > 0) {
    return {
      skill: [path.resolve(home, "plugins", "expskill", "content", "skills", "unslop", "SKILL.md")],
      policy: [path.resolve(home, "plugins", "expskill", "content", "policies", "unslop-runtime.json")],
    };
  }
  return {
    skill: [
      path.join(PACKAGE_ROOT, "skills", "unslop", "SKILL.md"),
      path.join(PACKAGE_ROOT, "..", "content", "skills", "unslop", "SKILL.md"),
    ],
    policy: [
      path.join(PACKAGE_ROOT, "assets", "unslop-runtime.json"),
      path.join(PACKAGE_ROOT, "..", "content", "policies", "unslop-runtime.json"),
    ],
  };
}

function v2SystemText(entry) {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object" && typeof entry.text === "string") return entry.text;
  return null;
}

async function v2SetupUnslop(ctx) {
  const sources = v2UnslopSources();
  await ctx.session.hook("context", async (event) => {
    const system = event?.system;
    if (!Array.isArray(system)) return;
    const marker = "<unslop-scope>";
    if (system.some((entry) => (v2SystemText(entry) ?? "").includes(marker))) return;
    let block;
    try {
      const [skill, policy] = await Promise.all([
        readFirstUnslop(sources.skill),
        readFirstUnslop(sources.policy),
      ]);
      block = buildUnslopBlock(skill, policy).block;
    } catch {
      return;
    }
    system.push({ type: "text", text: block });
  });
  await ctx.session.hook("compaction", async (event) => {
    let reminder;
    try {
      reminder = buildUnslopBlock(
        await readFirstUnslop(sources.skill),
        await readFirstUnslop(sources.policy),
      ).compactionReminder;
    } catch {
      return;
    }
    if (Array.isArray(event?.system)) {
      event.system.push({ type: "text", text: reminder });
    }
  });
}

function v2PolicyCallKey(sessionID, callID) {
  return `${sessionID}\u0000${callID}`;
}

async function v2SetupExecutionPolicy(ctx) {
  const home = process.env?.EXPSKILL_HOME;
  const candidates =
    typeof home === "string" && home.length > 0
      ? [path.resolve(home, "plugins", "expskill", "content", "policies", "execution-policy.json")]
      : [
          path.join(PACKAGE_ROOT, "assets", "execution-policy.json"),
          path.join(PACKAGE_ROOT, "..", "content", "policies", "execution-policy.json"),
        ];
  let policy = null;
  let policyPath = candidates[0];
  let loadDetail = null;
  for (const candidate of candidates) {
    try {
      policy = loadExecutionPolicy(JSON.parse(await readFile(candidate, "utf8")));
      policyPath = candidate;
      break;
    } catch (error) {
      loadDetail = error instanceof Error ? error.message : String(error);
      continue;
    }
  }
  if (!policy) {
    const loadError = new Error(`execution-policy failed to load ${policyPath}: ${loadDetail}`);
    const deny = async (event) => {
      if (!POLICY_TASK_TOOLS.has(event?.tool)) return;
      if (isPolicyAgent(requestedPolicyAgent(event?.input))) throw loadError;
    };
    await ctx.tool.hook("execute.before", deny);
    await ctx.tool.hook("execute.after", async () => {});
    return;
  }
  const trackers = new Map();
  const activeCalls = new Map();
  const trackerFor = (entry) => {
    if (!trackers.has(entry.route)) {
      trackers.set(entry.route, createPolicyTracker(entry.budget));
    }
    return trackers.get(entry.route);
  };
  await ctx.tool.hook("execute.before", async (event) => {
    if (!POLICY_TASK_TOOLS.has(event?.tool)) return;
    const agent = requestedPolicyAgent(event?.input);
    if (!isPolicyAgent(agent)) return;
    if (!policy.profiles.has(agent)) {
      throw new Error(`execution-policy denies undeclared agent: ${agent}`);
    }
    const entry = policy.routeProfiles.get(agent);
    if (!entry) return;
    const sessionID = event?.sessionID;
    const callID = event?.id ?? event?.messageID;
    if (typeof sessionID !== "string" || typeof callID !== "string" || callID.length === 0) {
      throw new Error("execution-policy requires tool hook sessionID and call ID");
    }
    const key = v2PolicyCallKey(sessionID, callID);
    if (activeCalls.has(key)) {
      throw new Error(`execution-policy received duplicate active call ID: ${callID}`);
    }
    trackerFor(entry).beforeCall(sessionID, agent);
    activeCalls.set(key, { sessionID, tracker: trackerFor(entry) });
  });
  await ctx.tool.hook("execute.after", async (event) => {
    if (!POLICY_TASK_TOOLS.has(event?.tool)) return;
    const sessionID = event?.sessionID;
    const callID = event?.id ?? event?.messageID;
    if (typeof sessionID !== "string" || typeof callID !== "string") return;
    const active = activeCalls.get(v2PolicyCallKey(sessionID, callID));
    if (!active) return;
    activeCalls.delete(v2PolicyCallKey(sessionID, callID));
    active.tracker.afterCall(active.sessionID);
  });
}

export const ExpSkillSetup = async (ctx) => {
  await v2SetupSkills(ctx);
  await v2SetupCommands(ctx);
  await v2SetupAgents(ctx);
  await v2SetupUnslop(ctx);
  await v2SetupExecutionPolicy(ctx);
};

export default {
  id: "opencode-expskill",
  setup: ExpSkillSetup,
  server: ExpSkillPlugin,
};
