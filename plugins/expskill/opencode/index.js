import { readFile } from "node:fs/promises";
import path from "node:path";
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

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeCatalogName(name) {
  return name !== "__proto__" && name !== "constructor" && name !== "prototype";
}

function validCommand(value) {
  return (
    isRecord(value) &&
    typeof value.description === "string" &&
    value.description.length > 0 &&
    typeof value.template === "string" &&
    value.template.length > 0
  );
}

function validAgent(value) {
  return (
    isRecord(value) &&
    typeof value.description === "string" &&
    value.description.length > 0 &&
    typeof value.mode === "string" &&
    value.mode.length > 0 &&
    typeof value.model === "string" &&
    value.model.length > 0 &&
    typeof value.reasoningEffort === "string" &&
    value.reasoningEffort.length > 0 &&
    typeof value.prompt === "string" &&
    value.prompt.length > 0 &&
    isRecord(value.permission)
  );
}

function validateCatalog(value) {
  if (!isRecord(value) || value.schema_version !== CATALOG_SCHEMA_VERSION) {
    throw new Error("invalid OpenCode runtime catalog");
  }
  const commands = value.commands;
  const agents = value.agents;
  if (
    !isRecord(commands) ||
    !isRecord(agents) ||
    Object.keys(commands).length === 0 ||
    Object.keys(agents).length === 0
  ) {
    throw new Error("invalid OpenCode runtime catalog inventory");
  }
  if (
    REQUIRED_COMMANDS.some((name) => !Object.prototype.hasOwnProperty.call(commands, name)) ||
    REQUIRED_AGENTS.some((name) => !Object.prototype.hasOwnProperty.call(agents, name))
  ) {
    throw new Error("incomplete OpenCode runtime catalog inventory");
  }
  for (const [name, entry] of Object.entries(commands)) {
    if (!safeCatalogName(name) || !validCommand(entry)) {
      throw new Error(`invalid OpenCode runtime catalog command: ${name}`);
    }
  }
  for (const [name, entry] of Object.entries(agents)) {
    if (!safeCatalogName(name) || !validAgent(entry)) {
      throw new Error(`invalid OpenCode runtime catalog agent: ${name}`);
    }
  }
  return { commands, agents };
}

async function readCatalog() {
  return validateCatalog(JSON.parse(await readFile(CATALOG_PATH, "utf8")));
}

function addCatalog(config, catalog) {
  if (!isRecord(config)) return;
  const commands = isRecord(config.command) ? config.command : (config.command = {});
  const agents = isRecord(config.agent) ? config.agent : (config.agent = {});
  for (const [name, entry] of Object.entries(catalog.commands)) {
    if (!Object.prototype.hasOwnProperty.call(commands, name)) {
      commands[name] = structuredClone(entry);
    }
  }
  for (const [name, entry] of Object.entries(catalog.agents)) {
    if (!Object.prototype.hasOwnProperty.call(agents, name)) {
      agents[name] = structuredClone(entry);
    }
  }

  const skills = isRecord(config.skills) ? config.skills : (config.skills = {});
  const paths = Array.isArray(skills.paths) ? skills.paths : (skills.paths = []);
  if (!paths.includes(SKILLS_PATH)) paths.push(SKILLS_PATH);
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
      let catalog;
      try {
        catalog = await readCatalog();
      } catch {
        return;
      }
      addCatalog(config, catalog);
    },
  };
};

export default {
  id: "opencode-expskill",
  server: ExpSkillPlugin,
};
