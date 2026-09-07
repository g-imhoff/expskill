import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCOPE = `Apply the following Unslop rules to natural-language user-facing prose you author, including commentary and final messages. Preserve code, commands, machine-readable data, logs, identifiers, API names, quotations, citations, source excerpts, approved copy, and project-required terminology exactly. Higher-priority instructions and explicit user formatting or tone choices win. Before sending user-facing prose, perform the included self-audit.

`;

const LIMIT = 5000;
const MARKER = "<unslop-scope>";

export function skillBody(contents) {
  const lines = contents.split("\n");
  if (lines.length === 0 || lines[0] !== "---") {
    throw new Error("Unslop skill is missing frontmatter");
  }
  const end = lines.indexOf("---", 1);
  if (end < 0) {
    throw new Error("Unslop skill frontmatter is not closed");
  }
  return lines.slice(end + 1).join("\n").trim() + "\n";
}

export function buildBlock(contents) {
  const block = SCOPE + skillBody(contents);
  return block.length > LIMIT ? block.slice(0, LIMIT) : block;
}

export function resolveSkillPath(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (home) {
    return path.resolve(home, "packages", "codex", "skills", "unslop", "SKILL.md");
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return path.resolve(base, "..", "skills", "unslop", "SKILL.md");
}

export const UnslopPlugin = async (ctx) => {
  const seen = new Set();
  const skillPath = resolveSkillPath(process.env, import.meta.url);
  return {
    "experimental.chat.system.transform": async (input, output) => {
      const system = output?.system;
      if (!Array.isArray(system)) {
        return;
      }
      if (system.some((entry) => typeof entry === "string" && entry.includes(MARKER))) {
        return;
      }
      const sessionID = input?.sessionID;
      if (sessionID && seen.has(sessionID)) {
        return;
      }
      let block;
      try {
        block = buildBlock(await readFile(skillPath, "utf8"));
      } catch {
        return;
      }
      const text = `<${MARKER}>\n${block}\n</${MARKER}>`;
      if (system.length > 0 && typeof system[0] === "string") {
        system[0] += `\n\n${text}`;
      } else {
        system.push(text);
      }
      if (sessionID) {
        seen.add(sessionID);
      }
    },
    "experimental.session.compacting": async (_input, output) => {
      try {
        output?.context?.push?.(
          "Preserve the Unslop prose-style rules across the compaction summary."
        );
      } catch {
        return;
      }
    },
  };
};
