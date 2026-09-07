import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TASK_TOOLS = new Set(["task", "subagent"]);

function resolvePolicyPath(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (home) {
    return path.resolve(home, "packages", "codex", "assets", "execution-policy.json");
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return path.resolve(base, "..", "assets", "execution-policy.json");
}

function routePolicy(policy, route, lane) {
  const node = policy?.routes?.[route]?.[lane];
  if (!node || typeof node !== "object" || Array.isArray(node)) {
    throw new Error(`unknown execution-policy route: ${route}.${lane}`);
  }
  for (const field of ["max_depth", "max_retries"]) {
    if (field in node) {
      throw new Error(`execution-policy route ${route}.${lane} advertises unsupported field: ${field}`);
    }
  }
  const allowedProfiles = node.allowed_profiles;
  if (
    !Array.isArray(allowedProfiles) ||
    allowedProfiles.length === 0 ||
    allowedProfiles.some((profile) => typeof profile !== "string" || profile.length === 0) ||
    new Set(allowedProfiles).size !== allowedProfiles.length
  ) {
    throw new Error(`execution-policy route ${route}.${lane} has invalid allowed_profiles`);
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
  return {
    allowedProfiles: [...allowedProfiles],
    maxAgentCalls,
    maxConcurrency,
    maxElapsedMs,
  };
}

function routeBudgets(policy) {
  if (policy?.policy_version !== "execution-budget-policy.v1") {
    throw new Error("unsupported execution-policy version");
  }
  const table = Object.create(null);
  const add = (route, lane) => {
    const budget = routePolicy(policy, route, lane);
    for (const agent of budget.allowedProfiles) {
      if (Object.hasOwn(table, agent)) {
        throw new Error(`execution-policy assigns agent to multiple routes: ${agent}`);
      }
      table[agent] = { route: `${route}.${lane}`, budget };
    }
  };
  add("implement", "standard");
  add("use-expskill", "parallel-plan-design");
  return table;
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

export const ExecutionPolicyPlugin = async (_ctx) => {
  const policyPath = resolvePolicyPath(process.env, import.meta.url);
  let table;
  try {
    const policy = JSON.parse(await readFile(policyPath, "utf8"));
    table = routeBudgets(policy);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    throw new Error(`execution-policy failed to load ${policyPath}: ${detail}`);
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
      if (typeof agent !== "string" || agent.length === 0) {
        throw new Error("execution-policy denies task call without an agent");
      }
      const entry = table[agent];
      if (!entry) {
        throw new Error(`execution-policy denies agent: ${agent}`);
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
