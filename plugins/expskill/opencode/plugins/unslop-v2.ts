import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
// OpenCode v2 plugin API ("@opencode-ai/plugin/v2"; the marketing site
// aliases it as "@opencode/plugin"). `ctx.session.hook("request", ...)`
// exposes mutable `system`/`messages`/`tools` immediately before model
// dispatch, which is the hook that provably delivers system text
// (https://dev.opencode.ai/v2/docs/build/plugins/ "Runtime hooks",
// reviewed 2026-10-01). The v1 `experimental.chat.system.transform`
// plugin in ./unslop.js stays the default for OpenCode v1.
import { Plugin } from "@opencode-ai/plugin/v2";

const MARKER = "unslop-scope";
const OPEN_MARKER = `<${MARKER}>`;
const CLOSE_MARKER = `</${MARKER}>`;

function skillBody(contents: string): string {
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

function runtimeScope(contents: string): string {
  const payload = JSON.parse(contents) as Record<string, unknown>;
  if (
    payload?.schema_version !== "unslop-runtime.v1" ||
    typeof payload.scope !== "string" ||
    payload.scope.trim().length === 0
  ) {
    throw new Error("Unslop runtime policy is invalid");
  }
  return payload.scope as string;
}

function resolveSources(
  env: NodeJS.ProcessEnv,
  pluginFile: string
): { skill: string[]; policy: string[] } {
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
      path.resolve(base, "..", "..", "content", "skills", "unslop", "SKILL.md"),
      path.resolve(base, "..", "skills", "unslop", "SKILL.md"),
    ],
    policy: [
      path.resolve(base, "..", "..", "content", "policies", "unslop-runtime.json"),
      path.resolve(base, "..", "assets", "unslop-runtime.json"),
    ],
  };
}

async function readFirst(paths: string[]): Promise<string> {
  for (const candidate of paths) {
    try {
      return await readFile(candidate, "utf8");
    } catch {
      continue;
    }
  }
  throw new Error("Unslop canonical content is unavailable");
}

function systemEntry(block: string, system: unknown[]): string | { text: string } {
  // The request-hook `system` array may carry plain strings or text parts
  // depending on the release; match the prevailing entry shape.
  for (const entry of system) {
    if (typeof entry === "string") return `${OPEN_MARKER}\n${block}\n${CLOSE_MARKER}`;
    if (typeof entry === "object" && entry !== null) {
      return { text: `${OPEN_MARKER}\n${block}\n${CLOSE_MARKER}` };
    }
  }
  return `${OPEN_MARKER}\n${block}\n${CLOSE_MARKER}`;
}

export default Plugin.define({
  id: "expskill-unslop",
  async setup(ctx) {
    const sources = resolveSources(process.env, import.meta.url);
    await ctx.session.hook("request", async (event) => {
      try {
        const system = (event as { system?: unknown }).system;
        if (!Array.isArray(system)) return;
        const joined = system
          .map((entry) =>
            typeof entry === "string"
              ? entry
              : typeof entry === "object" && entry !== null && "text" in entry
                ? String((entry as { text: unknown }).text)
                : ""
          )
          .join("\n");
        if (joined.includes(OPEN_MARKER)) return;
        const [skill, policy] = await Promise.all([
          readFirst(sources.skill),
          readFirst(sources.policy),
        ]);
        // Full scope + body, identical to the Codex/Hermes hooks: v2
        // documents no system-text budget for this hook, so every rule
        // ships instead of the v1 compacted subset.
        const block = runtimeScope(policy) + skillBody(skill);
        system.push(systemEntry(block, system));
      } catch {
        return;
      }
    });
  },
});
