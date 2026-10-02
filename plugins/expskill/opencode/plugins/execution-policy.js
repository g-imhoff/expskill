import { readFile } from "node:fs/promises";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import { performance } from "node:perf_hooks";

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
  "max_depth",
  "max_retries",
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
    return path.resolve(home, "plugins", "expskill", "content", "policies", "execution-policy.json");
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
  const maxDepth = positiveInteger("max_depth");
  const nonNegativeInteger = (field) => {
    const value = node[field];
    if (!Number.isSafeInteger(value) || value < 0) {
      throw new Error(`execution-policy route ${route}.${lane} has invalid ${field}`);
    }
    return value;
  };
  const maxRetries = nonNegativeInteger("max_retries");
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
    maxDepth,
    maxRetries,
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

function runLease(directory, filename, runID, route) {
  const deadline = performance.now() + 3000;
  const pause = () => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 10);
  const syncDirectory = () => {
    const descriptor = fs.openSync(directory, "r");
    try { fs.fsyncSync(descriptor); } finally { fs.closeSync(descriptor); }
  };
  const readOwner = (resource, expectedResource = resource) => {
    const metadata = fs.lstatSync(resource);
    if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size > 4096 || (metadata.mode & 0o077) !== 0) throw new Error("execution-policy unsafe run lock");
    const serialized = fs.readFileSync(resource, "utf8").trim();
    const owner = JSON.parse(serialized);
    if (serialized !== JSON.stringify(owner) || !isRecord(owner) || Object.keys(owner).sort().join(",") !== "host,pid,resource,route,run_id,schema_version,token" || owner.schema_version !== "execution-run-lock.v1" || owner.run_id !== runID || owner.route !== route || owner.resource !== path.basename(expectedResource) || !/^[0-9a-f]{32}$/.test(owner.token) || !Number.isSafeInteger(owner.pid) || owner.pid <= 0 || typeof owner.host !== "string" || !owner.host) throw new Error("execution-policy ambiguous run lock owner");
    return owner;
  };
  const dead = (owner) => {
    if (owner.host !== os.hostname()) throw new Error("execution-policy ambiguous remote run lock owner");
    try { process.kill(owner.pid, 0); return false; }
    catch (error) {
      if (error.code === "ESRCH") return true;
      throw new Error("execution-policy ambiguous live run lock owner");
    }
  };
  const claimPath = (token) => `${filename}.claim.${token}`;
  const recover = (resource, owner, depth) => {
    const claim = acquire(claimPath(owner.token), depth + 1);
    try {
      let current;
      try { current = readOwner(resource); }
      catch (error) { if (error.code === "ENOENT") return; throw error; }
      if (current.token === owner.token && dead(current)) {
        fs.unlinkSync(resource);
        syncDirectory();
      }
    } finally { claim.release(); }
  };
  const acquire = (resource, depth = 0) => {
    if (depth > 16) throw new Error("execution-policy run lock recovery depth exhausted");
    const token = randomBytes(16).toString("hex");
    const temporaryOwner = `${filename}.owner-${token}.tmp`;
    const owner = { schema_version: "execution-run-lock.v1", token, pid: process.pid, host: os.hostname(), resource: path.basename(resource), run_id: runID, route };
    const descriptor = fs.openSync(temporaryOwner, "wx", 0o600);
    try { fs.writeFileSync(descriptor, `${JSON.stringify(owner)}\n`); fs.fsyncSync(descriptor); }
    finally { fs.closeSync(descriptor); }
    try {
      while (performance.now() < deadline) {
        try {
          fs.linkSync(temporaryOwner, resource);
          syncDirectory();
          return { token, release() {
            const current = readOwner(resource);
            if (current.token !== token || current.pid !== process.pid) throw new Error("execution-policy run lock lease changed");
            fs.unlinkSync(resource);
            syncDirectory();
          } };
        } catch (error) { if (error.code !== "EEXIST") throw error; }
        let existing;
        try { existing = readOwner(resource); }
        catch (error) { if (error.code === "ENOENT") continue; throw error; }
        if (dead(existing)) recover(resource, existing, depth);
        else pause();
      }
      throw new Error("execution-policy run lock busy with live ownership");
    } finally {
      try { fs.unlinkSync(temporaryOwner); } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
  };
  const lease = acquire(`${filename}.lock`);
  try {
    let inspected = 0;
    for (const basename of fs.readdirSync(directory).sort()) {
      if (inspected >= 16 || performance.now() >= deadline) break;
      if (basename.startsWith(`${path.basename(filename)}.claim.`)) {
        inspected += 1;
        const resource = path.join(directory, basename);
        let owner;
        try { owner = readOwner(resource); }
        catch (error) { if (error.code === "ENOENT") continue; throw error; }
        if (dead(owner)) recover(resource, owner, 0);
      } else if (basename.startsWith(`${path.basename(filename)}.owner-`) && basename.endsWith(".tmp")) {
        inspected += 1;
        const temporaryOwner = path.join(directory, basename);
        let owner;
        try {
          const metadata = fs.lstatSync(temporaryOwner);
          if (!metadata.isFile() || metadata.isSymbolicLink() || metadata.size > 4096 || (metadata.mode & 0o077) !== 0) continue;
          const value = JSON.parse(fs.readFileSync(temporaryOwner, "utf8"));
          owner = readOwner(temporaryOwner, path.join(directory, value.resource || ""));
          if (basename !== `${path.basename(filename)}.owner-${owner.token}.tmp`) continue;
        } catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) continue; throw error; }
        if (dead(owner)) fs.unlinkSync(temporaryOwner);
      }
    }
    return lease;
  } catch (error) { lease.release(); throw error; }
}

function persistentRunCounter(env) {
  const runID = env.EXPSKILL_RUN_ID;
  if (runID === undefined || runID === "") return null;
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(runID)) {
    throw new Error("execution-policy requires a safe EXPSKILL_RUN_ID");
  }
  const directory = path.resolve(env.EXPSKILL_RUN_BUDGET_DIR || path.join(env.XDG_STATE_HOME || path.join(os.homedir(), ".local", "state"), "expskill", "execution-budget"));
  let ancestor = directory;
  while (true) {
    try {
      if (fs.lstatSync(ancestor).isSymbolicLink()) throw new Error("execution-policy run budget path is a symlink");
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const parent = path.dirname(ancestor);
    if (parent === ancestor) break;
    ancestor = parent;
  }
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const metadata = fs.lstatSync(directory);
  if (!metadata.isDirectory() || (metadata.mode & 0o077) !== 0) {
    throw new Error("execution-policy run budget directory must be private");
  }
  return (entry) => {
    const filename = path.join(directory, `${runID}-${encodeURIComponent(entry.route)}.json`);
    const lease = runLease(directory, filename, runID, entry.route);
    const now = Date.now();
    const temporary = `${filename}.${process.pid}.${lease.token}.tmp`;
    try {
      let state = { schema_version: "execution-run-budget.v1", run_id: runID, route: entry.route, calls: 0, started_at: now };
      try {
        const current = fs.lstatSync(filename);
        if (!current.isFile() || current.isSymbolicLink() || current.size > 4096 || (current.mode & 0o077) !== 0) {
          throw new Error("execution-policy run budget file is unsafe");
        }
        const serialized = fs.readFileSync(filename, "utf8").trim();
        state = JSON.parse(serialized);
        if (serialized !== JSON.stringify(state)) throw new Error("execution-policy run budget encoding is invalid");
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      if (!isRecord(state) || Object.keys(state).sort().join(",") !== "calls,route,run_id,schema_version,started_at" || state.schema_version !== "execution-run-budget.v1" || state.run_id !== runID || state.route !== entry.route || !Number.isSafeInteger(state.calls) || state.calls < 0 || !Number.isSafeInteger(state.started_at) || state.started_at < 0 || now < state.started_at) {
        throw new Error("execution-policy run budget state is invalid");
      }
      if (state.calls + 1 > entry.budget.maxAgentCalls) {
        throw new Error("execution-policy cumulative run budget exhausted");
      }
      if (now - state.started_at > entry.budget.maxElapsedMs) {
        throw new Error("execution-policy cumulative elapsed budget exhausted");
      }
      state.calls += 1;
      const descriptor = fs.openSync(temporary, "wx", 0o600);
      try {
        fs.writeFileSync(descriptor, `${JSON.stringify(state)}\n`);
        fs.fsyncSync(descriptor);
      } finally {
        fs.closeSync(descriptor);
      }
      fs.renameSync(temporary, filename);
      const parentFD = fs.openSync(directory, "r");
      try { fs.fsyncSync(parentFD); } finally { fs.closeSync(parentFD); }
    } finally {
      lease.release();
      try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
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
  let reserveRunCall;
  try {
    reserveRunCall = persistentRunCounter(process.env);
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
      try {
        if (reserveRunCall) reserveRunCall(entry);
      } catch (error) {
        tracker.afterCall(call.sessionID);
        throw error;
      }
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

export default {
  id: "expskill.execution-policy",
  server: ExecutionPolicyPlugin,
};
