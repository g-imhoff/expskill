import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCOPE = `Apply the following Unslop rules to natural-language user-facing prose you author, including commentary and final messages. Preserve code, commands, machine-readable data, logs, identifiers, API names, quotations, citations, source excerpts, approved copy, and project-required terminology exactly. Higher-priority instructions and explicit user formatting or tone choices win. Before sending user-facing prose, perform the included self-audit.

`;

const LIMIT = 5000;
const MARKER = "unslop-scope";
const OPEN_MARKER = `<${MARKER}>`;
const CLOSE_MARKER = `</${MARKER}>`;

const RULES = [
  ["Puffery.", "Cut grand claims and state what happened."],
  [
    "Name-dropping.",
    "Do not list media outlets without context. Name one relevant source and say what it reported.",
  ],
  [
    "Superficial -ing phrases.",
    "Delete dangling claims such as highlighting, ensuring, reflecting, showcasing, or fostering, or support them with real sources.",
  ],
  [
    "Promotional language.",
    "Replace sales language such as vibrant, breathtaking, groundbreaking, renowned, stunning, or must-visit with neutral descriptions.",
  ],
  [
    "Vague attributions.",
    "Name the source behind claims attributed to experts, reports, or critics, or delete the claim.",
  ],
  [
    "Formulaic challenges.",
    'Replace templates such as "despite challenges, it continues to thrive" with specific facts.',
  ],
  [
    "AI vocabulary.",
    "Replace additionally, crucial, delve, enduring, enhance, fostering, garner, interplay, intricate, abstract landscape, pivotal, showcase, abstract tapestry, testament, underscore, and vibrant with plain words.",
  ],
  [
    'Fancy ways to say "is".',
    "Replace serves as, stands as, boasts, and features with is or has.",
  ],
  ["\"Not just X, but Y.\"", "State the point directly."],
  [
    "Rule of three.",
    "Do not force ideas into groups of three. Use the natural number.",
  ],
  [
    "Synonym cycling.",
    "Pick one term for a thing and repeat it instead of cycling synonyms.",
  ],
  [
    "False ranges.",
    "Use from X to Y only for a meaningful scale. Otherwise list the topics directly.",
  ],
  [
    "Em dash overuse.",
    "Avoid em dashes entirely. Use periods or commas, not parentheses, en dashes, or hyphens as substitute dashes.",
  ],
  [
    "Colon overuse.",
    "Use colons before lists or examples, not as generic mid-sentence connectors.",
  ],
  ["Boldface overuse.", "Do not bold every proper noun or acronym."],
  [
    "Inline-header lists.",
    "Remove bold labels that merely repeat a line. A bold lead-in is acceptable only when the following text adds new detail.",
  ],
  ["Title case headings.", "Use sentence case."],
  ["Decorative emojis.", "Remove them from headings and bullets."],
  ["Curly quotes.", "Use straight quotes."],
  [
    "Chatbot phrases.",
    'Remove canned phrases such as "I hope this helps", "Let me know if", "Of course", and "Certainly".',
  ],
  [
    "Cutoff disclaimers.",
    "For claims introduced with disclaimers about limited details, find sources or remove the claim.",
  ],
  [
    "Sycophantic tone.",
    'Skip praise such as "Great question" or "You\'re absolutely right" and answer directly.',
  ],
  [
    "Filler phrases.",
    'Shorten wordy phrases: use "to" for "in order to", "because" for "due to the fact that", and delete "it is important to note that".',
  ],
  ["Excessive hedging.", "Replace stacked qualifiers with one accurate qualifier."],
  ["Generic conclusions.", "Replace empty optimism with specific plans or facts."],
  [
    "Abstract metaphor nouns.",
    "Use concrete words instead of substrate, wedge, vector, locus, vantage, nexus, noun-form primitive, metaphorical harness or surface, bedrock, metaphorical scaffolding, modality, paradigm, gold-plating, metaphorical ratchet, evacuate for moving code, endgame, north star, or flywheel.",
  ],
  [
    "Say what it does, not how it feels.",
    "Give a concrete instruction, fact, mechanism, or number. Cut a sentence if it could describe any project unchanged.",
  ],
  [
    "Shorten or split dense sentences.",
    "Use one idea per sentence so readers do not need to backtrack.",
  ],
  [
    "Active voice.",
    "Name the actor. Use passive voice only when the actor is unknown or does not matter.",
  ],
  [
    "Cut adverbs, or use a stronger verb.",
    "Replace weak verb-adverb pairs with a stronger verb or a measured result.",
  ],
  [
    "Prefer the plain word.",
    "Use plain words such as use, help, many, and if instead of utilize, leverage, facilitate, numerous, and in the event that. The fancier synonym is rarely clearer.",
  ],
];

const RUNTIME_SKILL = `# Unslop

Edit text to remove AI patterns and add human voice without changing its meaning or intended tone.

## Process

1. Scan for every pattern below.
2. Rewrite while preserving meaning and tone.
3. Add voice: have opinions, vary sentence rhythm, acknowledge complexity, use \"I\" when it fits, allow natural imperfection, and be specific.
4. Self-audit: \"What makes this obviously AI generated?\" Fix remaining tells.

## Patterns to detect and fix

${RULES.map(([name, instruction], index) => `${index + 1}. **${name}** ${instruction}`).join("\n")}`;

function skillBody(contents) {
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

function buildBlock(contents) {
  const body = skillBody(contents);
  const sourceRuleNames = [...body.matchAll(/^\d+\. \*\*([^*]+)\*\*/gm)].map(
    (match) => match[1]
  );
  const runtimeRuleNames = RULES.map(([name]) => name);
  if (JSON.stringify(sourceRuleNames) !== JSON.stringify(runtimeRuleNames)) {
    throw new Error("Unslop runtime rules do not match the shared skill");
  }
  if (!body.includes('Self-audit: "What makes this obviously AI generated?"')) {
    throw new Error("Unslop skill is missing its self-audit");
  }
  const payload = SCOPE + RUNTIME_SKILL;
  const block = `${OPEN_MARKER}\n${payload}\n${CLOSE_MARKER}`;
  if (block.length > LIMIT) {
    throw new Error(`Unslop runtime instructions exceed ${LIMIT} characters`);
  }
  return block;
}

function resolveSkillPath(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (home) {
    return path.resolve(home, "packages", "expskill", "skills", "unslop", "SKILL.md");
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return path.resolve(base, "..", "skills", "unslop", "SKILL.md");
}

export const UnslopPlugin = async () => {
  const skillPath = resolveSkillPath(process.env, import.meta.url);
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
      let text;
      try {
        text = buildBlock(await readFile(skillPath, "utf8"));
      } catch {
        return;
      }
      if (system.length > 0 && typeof system[0] === "string") {
        system[0] += `\n\n${text}`;
      } else {
        system.push(text);
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

function hasUnslopMarker(system) {
  return system.some((entry) => {
    if (typeof entry === "string") {
      return entry.includes(OPEN_MARKER);
    }
    if (entry && typeof entry.text === "string") {
      return entry.text.includes(OPEN_MARKER);
    }
    return false;
  });
}

function injectUnslopBlock(system, text) {
  if (system.length > 0 && typeof system[0] === "string") {
    system[0] += "\n\n" + text;
    return;
  }
  if (system.length > 0 && system[0] && typeof system[0].text === "string") {
    system[0].text += "\n\n" + text;
    return;
  }
  system.push({ type: "text", text });
}

export default {
  id: "expskill.unslop",
  setup: async (ctx) => {
    const skillPath = resolveSkillPath(process.env, import.meta.url);
    await ctx.session.hook("context", async (event) => {
      const system = event?.system;
      if (!Array.isArray(system)) {
        return;
      }
      if (hasUnslopMarker(system)) {
        return;
      }
      let text;
      try {
        text = buildBlock(await readFile(skillPath, "utf8"));
      } catch {
        return;
      }
      injectUnslopBlock(system, text);
    });
  },
};
