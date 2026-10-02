import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export async function authoringBlock() {
  const base = path.dirname(fileURLToPath(import.meta.url));
  const candidates = process.env.EXPSKILL_HOME
    ? [path.resolve(process.env.EXPSKILL_HOME, "plugins/expskill/content/policies/authoring-runtime.json")]
    : [path.resolve(base, "../assets/authoring-runtime.json"),
       path.resolve(base, "../../content/policies/authoring-runtime.json")];
  let contents;
  for (const candidate of candidates) {
    try {
      contents = await readFile(candidate, "utf8");
      break;
    } catch {
      continue;
    }
  }
  if (contents === undefined) throw new Error("Authoring runtime policy is unavailable");
  const policy = JSON.parse(contents);
  if (policy === null || typeof policy !== "object" ||
      JSON.stringify(Object.keys(policy).sort()) !== JSON.stringify(["instructions", "schema_version"]) ||
      policy.schema_version !== "authoring-runtime.v1" ||
      typeof policy.instructions !== "string" ||
      policy.instructions.trim().length === 0 || policy.instructions.length > 1000) {
    throw new Error("Authoring runtime policy is invalid");
  }
  return `<authoring-scope>\n${policy.instructions}\n</authoring-scope>`;
}

export const AuthoringPlugin = async () => {
  const block = await authoringBlock();
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      if (!Array.isArray(output?.system)) return;
      if (!output.system.some(entry => typeof entry === "string" && entry.includes(block))) {
        output.system.push(block);
      }
    },
    "experimental.session.compacting": async (_input, output) => {
      if (Array.isArray(output?.context) && !output.context.includes(block)) {
        output.context.push(block);
      }
    },
  };
};

export default { id: "expskill.authoring", server: AuthoringPlugin };
