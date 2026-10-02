import { readFile } from "node:fs/promises";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
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
  exactKeys(policy, new Set(["policy_version", "profiles", "routes", "planner_evidence"]), "execution-policy");
  if (policy.policy_version !== POLICY_VERSION) {
    throw new Error(`unsupported execution-policy version: ${policy.policy_version}`);
  }
  const profiles = validateProfiles(policy);
  const routeProfiles = routeBudgets(policy, profiles);
  const service = policy.planner_evidence;
  if (!isRecord(service)) throw new Error("execution-policy requires planner evidence service metadata");
  exactKeys(service, new Set(["schema_version", "caller_profile", "service_profile", "purposes", "max_prompt_lines"]), "planner evidence service");
  if (service.schema_version !== "plan-evidence-dispatch.v1" || service.caller_profile !== "expskill-planner" || service.service_profile !== "expskill-explorer" || JSON.stringify(service.purposes) !== JSON.stringify(["research", "plan-audit"]) || service.max_prompt_lines !== 300 || profiles.get(service.service_profile)?.sandbox_mode !== "read-only") {
    throw new Error("execution-policy invalid planner evidence service");
  }
  return { profiles, routeProfiles, service };
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


async function actualTaskCaller(ctx, input) {
  if (typeof ctx?.client?.session?.messages !== "function") return null;
  const request = ctx.client.session.messages({path: {id: input.sessionID}, query: {limit: 100}});
  let timer;
  try {
    const response = await Promise.race([request, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("execution-policy caller lookup timed out")), 3000);
    })]);
    if (!Array.isArray(response?.data)) throw new Error("execution-policy caller history is unavailable");
    const matches = response.data.filter((message) => message?.info?.role === "assistant" && message.info.sessionID === input.sessionID && message.parts?.some((part) => part?.type === "tool" && part.tool === input.tool && part.sessionID === input.sessionID && part.callID === input.callID));
    if (matches.length !== 1) throw new Error("execution-policy requires one actual caller tool part");
    const info = matches[0].info;
    const agent = info.agent || info.mode;
    if (typeof agent !== "string" || typeof info.path?.cwd !== "string") throw new Error("execution-policy caller identity is incomplete");
    return {agent, cwd: fs.realpathSync(info.path.cwd)};
  } finally { clearTimeout(timer); }
}

const LOAD_EVIDENCE_RESERVATION = String.raw`
import hashlib,importlib.util,json,os,stat,sys
from pathlib import Path
sys.dont_write_bytecode=True
scripts=Path(sys.argv[1]); envelope=json.loads(sys.argv[2]); repo=Path(sys.argv[3]); locator=Path(envelope['ledger'])
sys.path.insert(0,str(scripts))
name='research_budget' if envelope['purpose']=='research' else 'plan_graph'
spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py')); module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
flags=os.O_RDONLY|os.O_NOFOLLOW
fd=os.open(locator,flags)
try:
 raw=os.read(fd,1048577)
 if len(raw)>1048576: raise ValueError('oversized evidence ledger')
 identity=json.loads(raw)['identity']
finally: os.close(fd)
if name=='research_budget':
 receipt=module.load(None if identity['scope_digest'] else repo,identity['branch'],identity['run_id'],locator=locator,scope_digest=identity['scope_digest'])
 if receipt['lifecycle']!='active' or receipt['remaining']['active_seconds']<=0: raise ValueError('research allowance is inactive or exhausted')
else:
 workflow=json.loads(raw)['workflow_id']
 receipt=module.load_plan_audits(repo,identity['target_branch'],workflow,state_home=locator.parents[3])
if receipt['locator']!=str(locator) or receipt['revision']<envelope['reservation_revision']: raise ValueError('substituted or stale reservation locator')
dispatch=receipt['dispatches'].get(envelope['dispatch_id'])
if not dispatch or dispatch['status']!='reserved': raise ValueError('service requires its retained reserved dispatch')
if name=='plan_graph':
 if dispatch['actor_session_id'] is not None: raise ValueError('audit actor already bound')
 graph=module.load_workflow(repo,identity['target_branch'],state_home=locator.parents[3])
 canonical=json.dumps(graph,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
 if graph['graph_revision']!=dispatch['graph_revision'] or hashlib.sha256(canonical).hexdigest()!=dispatch['graph_digest']: raise ValueError('audit reservation no longer inspects the current graph')
 for key in ('graph_snapshot','accepted_input'):
  artifact=dispatch[key]; afd=os.open(artifact['path'],flags)
  try:
   metadata=os.fstat(afd)
   if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=os.geteuid() or metadata.st_nlink!=1 or (key=='graph_snapshot' and stat.S_IMODE(metadata.st_mode)!=0o600): raise ValueError('unsafe audit artifact')
   content=os.read(afd,1048577)
   if len(content)>1048576 or hashlib.sha256(content).hexdigest()!=artifact['digest']: raise ValueError('changed frozen audit artifact')
  finally: os.close(afd)
print(json.dumps({'dispatch':dispatch,'identity':receipt['identity'],'retained_findings':receipt.get('retained_findings',[]),'limit_source':receipt['limit_source'],'remaining':receipt['remaining']}))
`;

function preparePlannerEvidence(policy, policyPath, caller, input, args) {
  if (requestedAgent(args) !== policy.service.service_profile) throw new Error("execution-policy planner may launch only the read-only evidence service");
  if (!isRecord(args)) throw new Error("execution-policy evidence task arguments must be an object");
  exactKeys(args, new Set(["description", "prompt", "subagent_type"]), "planner evidence task arguments");
  nonEmptyString(args.description, "planner evidence description");
  nonEmptyString(args.prompt, "planner evidence prompt");
  if (args.prompt.length > 32768 || args.prompt.split("\n").length > policy.service.max_prompt_lines) throw new Error("execution-policy evidence prompt exceeds its bound");
  const line = args.prompt.split("\n")[0];
  if (!line.startsWith("EXPSKILL_PLAN_EVIDENCE ")) throw new Error("execution-policy planner requires a typed evidence reservation");
  const envelope = JSON.parse(line.slice("EXPSKILL_PLAN_EVIDENCE ".length));
  if (!isRecord(envelope)) throw new Error("execution-policy invalid evidence envelope");
  exactKeys(envelope, new Set(["schema_version", "purpose", "ledger", "dispatch_id", "reservation_revision"]), "planner evidence envelope");
  if (envelope.schema_version !== policy.service.schema_version || !policy.service.purposes.includes(envelope.purpose) || typeof envelope.ledger !== "string" || !path.isAbsolute(envelope.ledger) || !/^[A-Za-z0-9_-]{1,80}$/.test(envelope.dispatch_id) || !Number.isSafeInteger(envelope.reservation_revision) || envelope.reservation_revision < 1) throw new Error("execution-policy invalid evidence reservation identity");
  const scripts = path.resolve(path.dirname(policyPath), "..", "scripts");
  let binding;
  try {
    binding = JSON.parse(execFileSync("python3", ["-I", "-c", LOAD_EVIDENCE_RESERVATION, scripts, JSON.stringify(envelope), caller.cwd], {timeout: 3000, maxBuffer: 1048576, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"]}));
  } catch (error) { throw new Error(`execution-policy evidence reservation rejected: ${error.stderr?.toString().trim() || error.message}`); }
  const serviceBrief = envelope.purpose === "research"
    ? `No repository access or inherited planner history. Inspect only this reserved external-research question: ${binding.dispatch.question}`
    : `Independently inspect this complete accepted input and frozen Plan Graph for missing outcomes, contradictions, unsupported choices, proof gaps, and unsafe ordering. Report evidence and retained unresolved findings without a desired verdict. Frozen dispatch: ${JSON.stringify(binding.dispatch)}. Retained findings: ${JSON.stringify(binding.retained_findings)}.`;
  args.prompt = `${line}\nRead-only evidence service. No further delegation, product or Git mutations, graph or ledger writes, user interaction, approval authority, or planning role. External private input reads require the host's external-directory approval. ${serviceBrief}\nReturn the dispatch ID, actual actor/session identity, source locators and digests, evidence, findings or blocked status. Original accounting: ${JSON.stringify({identity: binding.identity, limit_source: binding.limit_source, remaining: binding.remaining})}`;
  const directory = path.dirname(envelope.ledger);
  let ancestor = directory;
  while (true) {
    if (fs.lstatSync(ancestor).isSymbolicLink()) throw new Error("execution-policy evidence claim path is a symlink");
    const parent = path.dirname(ancestor);
    if (parent === ancestor) break;
    ancestor = parent;
  }
  const metadata = fs.lstatSync(directory);
  if (!metadata.isDirectory() || (metadata.mode & 0o077) !== 0 || metadata.uid !== process.getuid()) throw new Error("execution-policy evidence claim directory must be private and owned");
  const claim = path.join(directory, `.service-${createHash("sha256").update(`${envelope.purpose}\0${envelope.dispatch_id}`).digest("hex")}.claim`);
  let fd;
  try { fd = fs.openSync(claim, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600); }
  catch (error) { if (error.code === "EEXIST") throw new Error("execution-policy evidence reservation already dispatched"); throw error; }
  try {
    fs.writeFileSync(fd, JSON.stringify({schema_version: "plan-evidence-launch.v1", envelope, caller_session_id: input.sessionID, call_id: input.callID}));
    fs.fsyncSync(fd);
  } finally { fs.closeSync(fd); }
  const parentFD = fs.openSync(directory, "r");
  try { fs.fsyncSync(parentFD); } finally { fs.closeSync(parentFD); }
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
      const caller = await actualTaskCaller(_ctx, input);
      if (caller?.agent === policy.service.caller_profile) {
        hookCall(input);
        preparePlannerEvidence(policy, policyPath, caller, input, output?.args);
        return;
      }
      if (output?.args?.prompt?.startsWith("EXPSKILL_PLAN_EVIDENCE ")) throw new Error("execution-policy evidence dispatch requires the actual planner caller");
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
