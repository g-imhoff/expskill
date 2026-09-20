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
  "brainstorm",
  "correct",
  "design",
  "grill-me",
  "implement",
  "plan",
  "review",
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
    entries.push([key, descriptor.value]);
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
  for (const [name, child] of ownEnumerableDataEntries(value, label)) {
    if (UNSAFE_NAMES.has(name)) {
      throw new Error(`${label}.${name} has an unsafe name`);
    }
    if (typeof child === "string") {
      if (child.length === 0) throw new Error(`${label}.${name} must be non-empty`);
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
  const fields = new Map(entries);
  nonEmptyString(fields.get("description"), `catalog.commands.${name}.description`);
  nonEmptyString(fields.get("template"), `catalog.commands.${name}.template`);
}

function validateAgent(name, value) {
  if (!isPlainObject(value)) throw new Error(`catalog.agents.${name} must be a plain object`);
  const expected = hasOwn(value, "temperature") ? AGENT_KEYS_WITH_TEMPERATURE : AGENT_KEYS;
  const exact = exactDataEntries(value, expected, `catalog.agents.${name}`);
  const fields = new Map(exact);
  for (const field of ["description", "mode", "model", "reasoningEffort", "prompt"]) {
    nonEmptyString(fields.get(field), `catalog.agents.${name}.${field}`);
  }
  validatePermission(fields.get("permission"), `catalog.agents.${name}.permission`);
  if (fields.has("temperature") &&
      (typeof fields.get("temperature") !== "number" || !Number.isFinite(fields.get("temperature")))) {
    throw new Error(`catalog.agents.${name}.temperature must be a finite number`);
  }
}

function validateCatalog(value) {
  const top = exactDataEntries(value, CATALOG_KEYS, "catalog");
  const fields = new Map(top);
  if (fields.get("schema_version") !== CATALOG_SCHEMA_VERSION) {
    throw new Error("invalid OpenCode runtime catalog schema");
  }
  const commandEntries = exactDataEntries(fields.get("commands"), REQUIRED_COMMANDS, "catalog.commands");
  const agentEntries = exactDataEntries(fields.get("agents"), REQUIRED_AGENTS, "catalog.agents");
  for (const [name, entry] of commandEntries) validateCommand(name, entry);
  for (const [name, entry] of agentEntries) validateAgent(name, entry);
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

function defineOwnData(target, name, value) {
  if (!Reflect.defineProperty(target, name, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  })) {
    throw new Error(`cannot define ${name}`);
  }
}

function buildReplacement(userEntries, catalogEntries) {
  const replacement = {};
  for (const [name, value] of userEntries) defineOwnData(replacement, name, value);
  for (const [name, entry] of catalogEntries) {
    if (!hasOwn(replacement, name)) defineOwnData(replacement, name, structuredClone(entry));
  }
  return replacement;
}

function buildSkillsReplacement(userEntries, paths) {
  const replacement = {};
  for (const [name, value] of userEntries) {
    if (name !== "paths") defineOwnData(replacement, name, value);
  }
  const nextPaths = [...paths];
  if (!nextPaths.includes(SKILLS_PATH)) nextPaths.push(SKILLS_PATH);
  defineOwnData(replacement, "paths", nextPaths);
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
    if (pathsEntry) paths = validatePaths(pathsEntry[1], "config.skills.paths");
  }
  return {
    root,
    replacements: {
      command: buildReplacement(commandEntries, catalog.commands),
      agent: buildReplacement(agentEntries, catalog.agents),
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

export default {
  id: "opencode-expskill",
  server: ExpSkillPlugin,
};
