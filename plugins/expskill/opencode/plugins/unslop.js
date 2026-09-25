import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LIMIT = 5000;
const MARKER = "unslop-scope";
const OPEN_MARKER = `<${MARKER}>`;
const CLOSE_MARKER = `</${MARKER}>`;

function skillBody(contents) {
  const lines = contents.split("\n");
  if (lines.length === 0 || lines[0] !== "---") {
    throw new Error("Unslop skill is missing frontmatter");
  }
  const end = lines.indexOf("---", 1);
  if (end < 0) {
    throw new Error("Unslop skill frontmatter is not closed");
  }
  return lines.slice(end + 1).join("\n").trim();
}

function compactSkill(contents) {
  const body = skillBody(contents);
  const soulMarker = "\n## Adding soul\n";
  const patternsMarker = "\n## Patterns to detect and fix\n";
  const soulStart = body.indexOf(soulMarker);
  const patternsStart = body.indexOf(patternsMarker);
  if (soulStart < 0 || patternsStart < soulStart) {
    throw new Error("Unslop skill is missing its compactable sections");
  }

  const introduction = body.slice(0, soulStart).trim();
  const soulSection = body.slice(soulStart + soulMarker.length, patternsStart);
  const soulNames = [...soulSection.matchAll(/^- \*\*([^*]+)\*\*/gm)].map(
    (match) => match[1]
  );
  if (soulNames.length === 0) {
    throw new Error("Unslop skill has no voice rules");
  }

  const rules = [...body.matchAll(/^(\d+)\. \*\*([^*]+)\*\*\s*(.*)$/gm)].map(
    (match) => {
      const sentences = match[3].trim().split(/(?<=[.!?])\s+/u);
      const selected = sentences.length <= 1
        ? sentences
        : [sentences[0], sentences[sentences.length - 1]];
      return `${match[1]}. **${match[2]}** ${selected.join(" ")}`;
    }
  );
  if (rules.length === 0) {
    throw new Error("Unslop skill has no numbered rules");
  }

  return [
    introduction,
    `## Adding soul\n\n${soulNames.join(" ")}`,
    `## Patterns to detect and fix\n\n${rules.join("\n")}`,
  ].join("\n\n");
}

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

export { skillBody, compactSkill, runtimePolicy, buildBlock, resolveSources, readFirst };

function buildBlock(skillContents, policyContents) {
  const policy = runtimePolicy(policyContents);
  const payload = policy.scope + compactSkill(skillContents);
  const block = `${OPEN_MARKER}\n${payload}\n${CLOSE_MARKER}`;
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
        const [skill, policy] = await Promise.all([
          readFirst(sources.skill),
          readFirst(sources.policy),
        ]);
        const { block } = buildBlock(skill, policy);
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
