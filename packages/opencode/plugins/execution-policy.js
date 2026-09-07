import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const TASK_TOOLS = new Set(["task", "subagent"]);

export function resolvePolicyPath(env, pluginFile) {
  const home = env?.EXPSKILL_HOME;
  if (home) {
    return path.resolve(home, "packages", "codex", "assets", "execution-policy.json");
  }
  const base = path.dirname(fileURLToPath(pluginFile));
  return path.resolve(base, "..", "..", "codex", "assets", "execution-policy.json");
}

export function routePolicy(policy, route, lane) {
  const node = policy?.routes?.[route]?.[lane];
  if (!node) {
    throw new Error(`unknown execution-policy route: ${route}.${lane}`);
  }
  return {
    allowedProfiles: [...(node.allowed_profiles ?? [])],
    maxAgentCalls: node.max_agent_calls,
    maxConcurrency: node.max_concurrency,
    maxDepth: node.max_depth,
    maxRetries: node.max_retries,
    maxElapsedMs: node.max_elapsed_ms,
  };
}

export function routeBudgets(policy) {
  const table = {};
  const add = (route, lane) => {
    const budget = routePolicy(policy, route, lane);
    for (const agent of budget.allowedProfiles) {
      table[agent] = { route: `${route}.${lane}`, budget };
    }
  };
  add("implement", "standard");
  add("use-expskill", "parallel-plan-design");
  return table;
}

export function createBudgetTracker(budget) {
  const sessions = new Map();
  const stateFor = (sessionID) => {
    let state = sessions.get(sessionID);
    if (!state) {
      state = { calls: 0, inFlight: 0, maxInFlight: 0, startedAt: null, retries: new Map() };
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
      state.maxInFlight = Math.max(state.maxInFlight, state.inFlight);
      return { calls: state.calls, inFlight: state.inFlight };
    },
    afterCall(sessionID) {
      const state = sessions.get(sessionID);
      if (state && state.inFlight > 0) {
        state.inFlight -= 1;
      }
    },
    recordRetry(sessionID, key) {
      const state = stateFor(sessionID);
      const count = (state.retries.get(key) ?? 0) + 1;
      state.retries.set(key, count);
      if (count > budget.maxRetries) {
        throw new Error(
          `execution-policy budget exhausted: ${count} retries exceed max ${budget.maxRetries}`
        );
      }
      return count;
    },
    snapshot(sessionID) {
      const state = sessions.get(sessionID);
      if (!state) {
        return { calls: 0, inFlight: 0, maxInFlight: 0 };
      }
      return { calls: state.calls, inFlight: state.inFlight, maxInFlight: state.maxInFlight };
    },
  };
}

function requestedAgent(input) {
  const args = input?.args ?? input?.input ?? {};
  if (typeof args === "object" && args !== null) {
    return args.subagent_type ?? args.agent ?? args.subagentType ?? null;
  }
  return null;
}

export const ExecutionPolicyPlugin = async (ctx) => {
  const policyPath = resolvePolicyPath(process.env, import.meta.url);
  let table;
  try {
    const policy = JSON.parse(await readFile(policyPath, "utf8"));
    table = routeBudgets(policy);
  } catch {
    return {};
  }
  const trackers = new Map();
  const trackerFor = (agent) => {
    const entry = table[agent];
    if (!entry) {
      return null;
    }
    if (!trackers.has(entry.route)) {
      trackers.set(entry.route, createBudgetTracker(entry.budget));
    }
    return trackers.get(entry.route);
  };
  const sessionOf = (input) => input?.sessionID ?? input?.sessionId ?? "default";
  return {
    "tool.execute.before": async (input, _output) => {
      if (!TASK_TOOLS.has(input?.tool)) {
        return;
      }
      const agent = requestedAgent(input);
      if (!agent) {
        return;
      }
      const tracker = trackerFor(agent);
      if (!tracker) {
        return;
      }
      tracker.beforeCall(sessionOf(input), agent);
    },
    "tool.execute.after": async (input, _output) => {
      if (!TASK_TOOLS.has(input?.tool)) {
        return;
      }
      const agent = requestedAgent(input);
      if (!agent) {
        return;
      }
      const tracker = trackerFor(agent);
      if (!tracker) {
        return;
      }
      tracker.afterCall(sessionOf(input));
    },
  };
};
