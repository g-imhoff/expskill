import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LIMIT = 5000;
const MARKER = "unslop-scope";
const OPEN_MARKER = `<${MARKER}>`;
const CLOSE_MARKER = `</${MARKER}>`;

function runtimePolicy(contents) {
  const payload = JSON.parse(contents);
  const keys = Object.keys(payload).sort();
  const expected = ["compaction_reminder", "schema_version", "scope"];
  if (
    JSON.stringify(keys) !== JSON.stringify(expected) ||
    payload.schema_version !== "unslop-runtime.v1" ||
    typeof payload.scope !== "string" ||
    payload.scope.trim().length === 0 ||
    typeof payload.compaction_reminder !== "string" ||
    payload.compaction_reminder.trim().length === 0
  ) {
    throw new Error("Unslop runtime policy is invalid");
  }
  return payload;
}

function buildBlock(policyContents) {
  const policy = runtimePolicy(policyContents);
  const block = `${OPEN_MARKER}\n${policy.scope}\n${CLOSE_MARKER}`;
  if (block.length > LIMIT) {
    throw new Error(`Unslop runtime instructions exceed ${LIMIT} characters`);
  }
  return { block, compactionReminder: policy.compaction_reminder };
}

function resolveSources(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (typeof home === "string" && home.length > 0) {
    const content = path.resolve(home, "plugins", "expskill", "content");
    return {
      skill: [path.join(content, "skills", "unslop", "SKILL.md")],
      policy: [path.join(content, "policies", "unslop-runtime.json")],
    };
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return {
    skill: [
      path.resolve(base, "..", "skills", "unslop", "SKILL.md"),
      path.resolve(base, "..", "..", "content", "skills", "unslop", "SKILL.md"),
    ],
    policy: [
      path.resolve(base, "..", "assets", "unslop-runtime.json"),
      path.resolve(base, "..", "..", "content", "policies", "unslop-runtime.json"),
    ],
  };
}

async function readFirst(paths) {
  for (const candidate of paths) {
    try {
      return await readFile(candidate, "utf8");
    } catch {
      continue;
    }
  }
  throw new Error("Unslop canonical content is unavailable");
}

export const UnslopPlugin = async () => {
  const sources = resolveSources(process.env, import.meta.url);
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      const system = output?.system;
      if (!Array.isArray(system)) {
        return;
      }
      if (
        system.some(
          (entry) =>
            typeof entry === "string" &&
            (entry.startsWith(`${OPEN_MARKER}\n`) || entry.includes(`\n${OPEN_MARKER}\n`))
        )
      ) {
        return;
      }
      try {
        const [, policy] = await Promise.all([
          readFirst(sources.skill),
          readFirst(sources.policy),
        ]);
        const { block } = buildBlock(policy);
        if (system.length > 0 && typeof system[0] === "string") {
          system[0] += `\n\n${block}`;
        } else {
          system.push(block);
        }
      } catch {
        return;
      }
    },
    "experimental.session.compacting": async (_input, output) => {
      try {
        const policy = runtimePolicy(await readFirst(sources.policy));
        output?.context?.push?.(policy.compaction_reminder);
      } catch {
        return;
      }
    },
  };
};

export default {
  id: "expskill.unslop",
  server: UnslopPlugin,
};
