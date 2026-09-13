import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TASK_TOOLS = new Set(["task", "subagent"]);
const POLICY_VERSION = "execution-budget-policy.v1";
const EXP_AGENT_PREFIX = "expskill-";
const PROFILE_FIELDS = new Set([
  "agent_type",
  "role",
  "model",
  "effort",
  "sandbox_mode",
  "escalation",
]);
const ROUTE_FIELDS = new Set([
  "allowed_profiles",
  "selected",
  "max_agent_calls",
  "max_concurrency",
  "max_elapsed_ms",
]);
const SELECTED_FIELDS = new Set(["role", "profile", "agent_type"]);
const REQUIRED_ROUTE_PROFILES = new Map([
  [
    "use-expskill.parallel-plan-design",
    new Set(["expskill-planner", "expskill-designer"]),
  ],
  [
    "implement.standard",
    new Set(["expskill-implementer", "expskill-review", "expskill-spec"]),
  ],
]);

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isExpSkillAgent(value) {
  return typeof value === "string" && value.startsWith(EXP_AGENT_PREFIX) && value.length > EXP_AGENT_PREFIX.length;
}

function exactKeys(value, expected, label) {
  const actual = Object.keys(value);
  if (actual.length !== expected.size || actual.some((key) => !expected.has(key))) {
    throw new Error(`${label} must declare exactly: ${[...expected].join(", ")}`);
  }
}

function nonEmptyString(value, label) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} must be a non-empty string`);
  }
}

function resolvePolicyPath(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (typeof home === "string" && home.length > 0) {
    return path.resolve(home, "packages", "expskill", "assets", "execution-policy.json");
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return path.resolve(base, "..", "assets", "execution-policy.json");
}

function validateProfiles(policy) {
  const profiles = policy?.profiles;
  if (!isRecord(profiles) || Object.keys(profiles).length === 0) {
    throw new Error("execution-policy profiles must be a non-empty object");
  }
  const known = new Map();
  for (const [name, profile] of Object.entries(profiles)) {
    if (!isExpSkillAgent(name)) {
      throw new Error(`execution-policy profile name is not an expskill-* agent: ${name}`);
    }
    if (!isRecord(profile)) {
      throw new Error(`execution-policy profile ${name} must be an object`);
    }
    exactKeys(profile, PROFILE_FIELDS, `execution-policy profile ${name}`);
    if (profile.agent_type !== name) {
      throw new Error(`execution-policy profile ${name} agent_type must match its name`);
    }
    for (const field of ["agent_type", "role", "model", "effort", "sandbox_mode"]) {
      nonEmptyString(profile[field], `execution-policy profile ${name}.${field}`);
    }
    if (profile.escalation !== null) {
      throw new Error(`execution-policy profile ${name}.escalation must be null`);
    }
    known.set(name, profile);
  }
  return known;
}

function routeBudget(node, route, lane, profiles, routeProfiles) {
  if (!isRecord(node)) {
    throw new Error(`execution-policy route ${route}.${lane} must be an object`);
  }
  exactKeys(node, ROUTE_FIELDS, `execution-policy route ${route}.${lane}`);
  const allowedProfiles = node.allowed_profiles;
  if (
    !Array.isArray(allowedProfiles) ||
    allowedProfiles.length === 0 ||
    allowedProfiles.some((profile) => typeof profile !== "string" || profile.length === 0) ||
    new Set(allowedProfiles).size !== allowedProfiles.length
  ) {
    throw new Error(`execution-policy route ${route}.${lane} has invalid allowed_profiles`);
  }
  const selected = node.selected;
  if (!Array.isArray(selected) || selected.length !== allowedProfiles.length) {
    throw new Error(`execution-policy route ${route}.${lane} has invalid selected entries`);
  }
  const selectedProfiles = new Set();
  for (const entry of allowedProfiles) {
    if (!profiles.has(entry)) {
      throw new Error(`execution-policy route ${route}.${lane} references undeclared profile: ${entry}`);
    }
    if (routeProfiles.has(entry)) {
      throw new Error(`execution-policy assigns agent to multiple routes: ${entry}`);
    }
  }
  for (const [index, entry] of selected.entries()) {
    if (!isRecord(entry)) {
      throw new Error(`execution-policy route ${route}.${lane} selected[${index}] must be an object`);
    }
    exactKeys(entry, SELECTED_FIELDS, `execution-policy route ${route}.${lane} selected[${index}]`);
    if (!profiles.has(entry.profile)) {
      throw new Error(
        `execution-policy route ${route}.${lane} selected[${index}] references undeclared profile: ${entry.profile}`
      );
    }
    if (!allowedProfiles.includes(entry.profile) || selectedProfiles.has(entry.profile)) {
      throw new Error(
        `execution-policy route ${route}.${lane} selected[${index}] is not a unique allowed profile`
      );
    }
    const profile = profiles.get(entry.profile);
    if (entry.role !== profile.role || entry.agent_type !== profile.agent_type) {
      throw new Error(
        `execution-policy route ${route}.${lane} selected[${index}] disagrees with profile ${entry.profile}`
      );
    }
    selectedProfiles.add(entry.profile);
  }
  if (selectedProfiles.size !== allowedProfiles.length) {
    throw new Error(`execution-policy route ${route}.${lane} selected entries do not cover allowed_profiles`);
  }
  const positiveInteger = (field) => {
    const value = node[field];
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new Error(`execution-policy route ${route}.${lane} has invalid ${field}`);
    }
    return value;
  };
  const maxAgentCalls = positiveInteger("max_agent_calls");
  const maxConcurrency = positiveInteger("max_concurrency");
  const maxElapsedMs = positiveInteger("max_elapsed_ms");
  if (maxConcurrency > maxAgentCalls) {
    throw new Error(
      `execution-policy route ${route}.${lane} max_concurrency exceeds max_agent_calls`
    );
  }
  const budget = {
    allowedProfiles: [...allowedProfiles],
    maxAgentCalls,
    maxConcurrency,
    maxElapsedMs,
  };
  for (const profile of allowedProfiles) {
    routeProfiles.set(profile, { route: `${route}.${lane}`, budget });
  }
}

function routeBudgets(policy, profiles) {
  const routes = policy?.routes;
  if (!isRecord(routes) || Object.keys(routes).length === 0) {
    throw new Error("execution-policy routes must be a non-empty object");
  }
  const table = new Map();
  for (const [route, lanes] of Object.entries(routes)) {
    nonEmptyString(route, "execution-policy route name");
    if (!isRecord(lanes) || Object.keys(lanes).length === 0) {
      throw new Error(`execution-policy route ${route} must declare a non-empty lane object`);
    }
    for (const [lane, node] of Object.entries(lanes)) {
      nonEmptyString(lane, `execution-policy route ${route} lane name`);
      routeBudget(node, route, lane, profiles, table);
    }
  }
  for (const [routeKey, expectedProfiles] of REQUIRED_ROUTE_PROFILES) {
    const separator = routeKey.indexOf(".");
    const route = routeKey.slice(0, separator);
    const lane = routeKey.slice(separator + 1);
    const node = routes?.[route]?.[lane];
    if (!isRecord(node)) {
      throw new Error(`execution-policy is missing required route: ${routeKey}`);
    }
    const actualProfiles = node.allowed_profiles;
    if (
      !Array.isArray(actualProfiles) ||
      actualProfiles.length !== expectedProfiles.size ||
      actualProfiles.some((profile) => !expectedProfiles.has(profile))
    ) {
      throw new Error(`execution-policy route ${routeKey} has an invalid profile assignment`);
    }
  }
  return table;
}

function loadPolicy(policy) {
  if (!isRecord(policy)) {
    throw new Error("execution-policy must be a JSON object");
  }
  exactKeys(policy, new Set(["policy_version", "profiles", "routes"]), "execution-policy");
  if (policy.policy_version !== POLICY_VERSION) {
    throw new Error(`unsupported execution-policy version: ${policy.policy_version}`);
  }
  const profiles = validateProfiles(policy);
  const routeProfiles = routeBudgets(policy, profiles);
  return { profiles, routeProfiles };
}

function createBudgetTracker(budget) {
  const sessions = new Map();
  const stateFor = (sessionID) => {
    let state = sessions.get(sessionID);
    if (!state) {
      state = { calls: 0, inFlight: 0, startedAt: null };
      sessions.set(sessionID, state);
    }
    return state;
  };
  const checkElapsed = (state, now) => {
    if (state.startedAt !== null && now - state.startedAt > budget.maxElapsedMs) {
      throw new Error(
        `execution-policy budget exhausted: elapsed ${now - state.startedAt}ms exceeds ${budget.maxElapsedMs}ms`
      );
    }
  };
  return {
    beforeCall(sessionID, agent, now = Date.now()) {
      if (!budget.allowedProfiles.includes(agent)) {
        throw new Error(`execution-policy denies agent: ${agent}`);
      }
      const state = stateFor(sessionID);
      if (state.startedAt === null) {
        state.startedAt = now;
      }
      checkElapsed(state, now);
      if (state.calls + 1 > budget.maxAgentCalls) {
        throw new Error(
          `execution-policy budget exhausted: ${state.calls + 1} calls exceed max ${budget.maxAgentCalls}`
        );
      }
      if (state.inFlight + 1 > budget.maxConcurrency) {
        throw new Error(
          `execution-policy budget exhausted: ${state.inFlight + 1} concurrent exceed max ${budget.maxConcurrency}`
        );
      }
      state.calls += 1;
      state.inFlight += 1;
    },
    afterCall(sessionID) {
      const state = sessions.get(sessionID);
      if (state && state.inFlight > 0) {
        state.inFlight -= 1;
      }
    },
  };
}

function requestedAgent(args) {
  if (typeof args === "object" && args !== null) {
    return args.subagent_type ?? args.agent ?? args.subagentType ?? null;
  }
  return null;
}

function hookCall(input) {
  const sessionID = input?.sessionID;
  const callID = input?.callID;
  if (typeof sessionID !== "string" || sessionID.length === 0) {
    throw new Error("execution-policy requires tool hook sessionID");
  }
  if (typeof callID !== "string" || callID.length === 0) {
    throw new Error("execution-policy requires tool hook callID");
  }
  return { sessionID, callID, key: `${sessionID}\u0000${callID}` };
}

function loadFailureHooks(loadError) {
  const deny = async (input, output) => {
    if (!TASK_TOOLS.has(input?.tool)) {
      return;
    }
    const agent = requestedAgent(output?.args);
    if (isExpSkillAgent(agent)) {
      throw loadError;
    }
  };
  return {
    "tool.execute.before": deny,
    "tool.execute.after": async () => {},
  };
}

export const ExecutionPolicyPlugin = async (_ctx) => {
  const policyPath = resolvePolicyPath(process.env, import.meta.url);
  let policy;
  try {
    policy = loadPolicy(JSON.parse(await readFile(policyPath, "utf8")));
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return loadFailureHooks(
      new Error(`execution-policy failed to load ${policyPath}: ${detail}`),
    );
  }
  const trackers = new Map();
  const activeCalls = new Map();
  const trackerFor = (entry) => {
    if (!trackers.has(entry.route)) {
      trackers.set(entry.route, createBudgetTracker(entry.budget));
    }
    return trackers.get(entry.route);
  };
  return {
    "tool.execute.before": async (input, output) => {
      if (!TASK_TOOLS.has(input?.tool)) {
        return;
      }
      const agent = requestedAgent(output?.args);
      if (!isExpSkillAgent(agent)) {
        return;
      }
      if (!policy.profiles.has(agent)) {
        throw new Error(`execution-policy denies undeclared agent: ${agent}`);
      }
      const entry = policy.routeProfiles.get(agent);
      if (!entry) {
        return;
      }
      const call = hookCall(input);
      if (activeCalls.has(call.key)) {
        throw new Error(`execution-policy received duplicate active callID: ${call.callID}`);
      }
      const tracker = trackerFor(entry);
      tracker.beforeCall(call.sessionID, agent);
      activeCalls.set(call.key, { sessionID: call.sessionID, tracker });
    },
    "tool.execute.after": async (input, _output) => {
      if (!TASK_TOOLS.has(input?.tool)) {
        return;
      }
      const agent = requestedAgent(input?.args);
      const entry = isExpSkillAgent(agent) ? policy.routeProfiles.get(agent) : null;
      if (!entry && (typeof input?.sessionID !== "string" || typeof input?.callID !== "string")) {
        return;
      }
      const call = hookCall(input);
      const active = activeCalls.get(call.key);
      if (!active) {
        return;
      }
      activeCalls.delete(call.key);
      active.tracker.afterCall(active.sessionID);
    },
  };
};

function v2HookCall(event) {
  const sessionID = event?.sessionID;
  const id = event?.id;
  if (typeof sessionID !== "string" || sessionID.length === 0) {
    throw new Error("execution-policy requires tool hook sessionID");
  }
  if (typeof id !== "string" || id.length === 0) {
    throw new Error("execution-policy requires tool hook id");
  }
  return { sessionID, callID: id, key: sessionID + "::" + id };
}

export default {
  id: "expskill.execution-policy",
  setup: async (ctx) => {
    const policyPath = resolvePolicyPath(process.env, import.meta.url);
    let policy = null;
    let loadError = null;
    try {
      policy = loadPolicy(JSON.parse(await readFile(policyPath, "utf8")));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      loadError = new Error("execution-policy failed to load " + policyPath + ": " + detail);
    }
    if (loadError || !policy) {
      const failure = loadError ?? new Error("execution-policy failed to load " + policyPath);
      await ctx.tool.hook("execute.before", (event) => {
        if (!TASK_TOOLS.has(event?.tool)) {
          return;
        }
        const agent = requestedAgent(event?.input);
        if (isExpSkillAgent(agent)) {
          throw failure;
        }
      });
      return;
    }
    const activePolicy = policy;
    const trackers = new Map();
    const activeCalls = new Map();
    const trackerFor = (entry) => {
      if (!trackers.has(entry.route)) {
        trackers.set(entry.route, createBudgetTracker(entry.budget));
      }
      return trackers.get(entry.route);
    };
    await ctx.tool.hook("execute.before", (event) => {
      if (!TASK_TOOLS.has(event?.tool)) {
        return;
      }
      const agent = requestedAgent(event?.input);
      if (!isExpSkillAgent(agent)) {
        return;
      }
      if (!activePolicy.profiles.has(agent)) {
        throw new Error("execution-policy denies undeclared agent: " + agent);
      }
      const entry = activePolicy.routeProfiles.get(agent);
      if (!entry) {
        return;
      }
      const call = v2HookCall(event);
      if (activeCalls.has(call.key)) {
        throw new Error("execution-policy received duplicate active callID: " + call.callID);
      }
      const tracker = trackerFor(entry);
      tracker.beforeCall(call.sessionID, agent);
      activeCalls.set(call.key, { sessionID: call.sessionID, tracker });
    });
    await ctx.tool.hook("execute.after", (event) => {
      if (!TASK_TOOLS.has(event?.tool)) {
        return;
      }
      if (typeof event?.sessionID !== "string" || typeof event?.id !== "string") {
        return;
      }
      const key = event.sessionID + "::" + event.id;
      const active = activeCalls.get(key);
      if (!active) {
        return;
      }
      activeCalls.delete(key);
      active.tracker.afterCall(active.sessionID);
    });
  },
};
