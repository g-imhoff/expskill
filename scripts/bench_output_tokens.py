#!/usr/bin/env python3
"""Benchmark skill-helper stdout size to track token savings.

Stdlib only. Runs a fixed set of CLI scenarios against scratch repos with
isolated state homes, captures stdout per command, and records bytes, lines,
and estimated tokens (chars/4, labeled as an estimate since no tokenizer
dependency is used).

Usage:
    python3 scripts/bench_output_tokens.py --out baseline.json
    # ... make the output-trimming changes ...
    python3 scripts/bench_output_tokens.py --out after.json --compare baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTENT = ROOT / "plugins" / "expskill" / "content"
DESIGN_STATE = CONTENT / "scripts" / "design_state.py"
PLAN_GRAPH = CONTENT / "scripts" / "plan_graph.py"
WORKTREES = CONTENT / "scripts" / "worktrees.py"
RESEARCH_RECORD = CONTENT / "skills" / "deep-research" / "scripts" / "research_record.py"
RUN_STATE = CONTENT / "skills" / "skill-builder" / "scripts" / "run_state.py"
BOOTSTRAP = CONTENT / "skills" / "test" / "scripts" / "bootstrap_run.py"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def git(repo: Path, *args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo if cwd is None else cwd,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def make_repo(root: Path, branch: str = "feature/bench") -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", branch)
    git(repo, "config", "user.email", "bench@example.invalid")
    git(repo, "config", "user.name", "bench")
    (repo / "f.txt").write_text("bench\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bench")
    return repo, git(repo, "rev-parse", "HEAD")


def check_keys(*keys: str):
    def check(payload: object) -> None:
        assert isinstance(payload, dict), f"expected JSON object, got: {payload!r}"[:300]
        missing = [key for key in keys if key not in payload]
        assert not missing, f"missing keys {missing} in {str(payload)[:300]}"
        assert payload.get("ok", True) is not False, f"error body: {str(payload)[:300]}"
    return check


class Bench:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, int]] = {}

    def record(self, label: str, stdout: bytes, check=None) -> None:
        text = stdout.decode("utf-8", "replace")
        if check is not None:
            check(json.loads(text))
        self.rows[label] = {
            "bytes": len(stdout),
            "lines": text.count("\n"),
            "tokens_est": estimate_tokens(text),
        }

    def run(self, script: Path, *args: str, stdin: object = None,
            cwd: Path | None = None, env: dict | None = None) -> bytes:
        if stdin is None:
            data = None
        elif isinstance(stdin, bytes):
            data = stdin
        else:
            data = json.dumps(stdin).encode("utf-8")
        completed = subprocess.run(
            [sys.executable, str(script), *args], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
            env=env, check=False,
        )
        if completed.returncode not in (0, 1):
            raise RuntimeError(
                f"{script.name} {' '.join(args)} exited {completed.returncode}: "
                f"{completed.stderr.decode()[:500]}"
            )
        return completed.stdout

    def report(self) -> dict:
        total = sum(row["tokens_est"] for row in self.rows.values())
        return {
            "method": "chars/4 estimate, stdlib only, no tokenizer dependency",
            "commands": self.rows,
            "total_tokens_est": total,
            "total_bytes": sum(row["bytes"] for row in self.rows.values()),
        }


def scenario_design_state(bench: Bench, root: Path) -> None:
    from tests.design_state_test_support import passing_technical

    digest = "d" * 64
    repo, head = make_repo(root / "design")
    home = root / "design-home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}

    out = bench.run(DESIGN_STATE, "initialize", "--contract-digest", digest,
                    "--contract-outcome", "bench", "--components", "CheckoutForm",
                    "--routed", cwd=repo, env=env)
    bench.record("design_state.initialize", out, check_keys("workflow_id", "revision"))
    workflow = json.loads(out)["workflow_id"]
    out = bench.run(DESIGN_STATE, "discover", cwd=repo, env=env)
    bench.record("design_state.discover", out, check_keys("workflow_id", "revision"))
    out = bench.run(DESIGN_STATE, "load", workflow, cwd=repo, env=env)
    bench.record("design_state.load", out, check_keys("revision", "lifecycle"))
    out = bench.run(
        DESIGN_STATE, "confirm-brief", cwd=repo, env=env,
        stdin={"workflow_id": workflow, "expected_revision": 0, "brief": {
            "objective": "bench", "requirements": ["r1"],
            "responsive_expectations": {"compact": "c", "intermediate": "i", "wide": "w"},
            "non_goals": [], "source": {"kind": "plan-graph", "workflow_id": "1" * 32,
                                        "revision": 1, "digest": digest}}, "confirmed": True},
    )
    bench.record("design_state.confirm-brief", out, check_keys("revision", "brief_digest"))
    revision = json.loads(out)["revision"]
    (repo / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "candidate")
    candidate = git(repo, "rev-parse", "HEAD")
    out = bench.run(DESIGN_STATE, "checkpoint-candidate", workflow, str(revision),
                    candidate, cwd=repo, env=env)
    bench.record("design_state.checkpoint-candidate", out, check_keys("revision", "brief_digest"))
    revision = json.loads(out)["revision"]

    def inventory_digest(value: dict) -> str:
        import hashlib
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    import hashlib
    brief_body = {
        "objective": "bench", "requirements": ["r1"],
        "responsive_expectations": {"compact": "c", "intermediate": "i", "wide": "w"},
        "non_goals": [], "source": {"kind": "plan-graph", "workflow_id": "1" * 32,
                                    "revision": 1, "digest": digest}, "confirmed": True,
    }
    brief_digest = hashlib.sha256(
        json.dumps(brief_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    candidate_payload = {"files": [{"path": "CheckoutForm.tsx", "digest": digest,
                                    "classification": "component"}]}
    review_evidence = {"files": [{"path": "review.json", "digest": digest,
                                  "classification": "review"}]}
    manifest = {"files": [{"path": "manifest.json", "digest": digest, "classification": "manifest"}]}
    records = {
        "components": {"CheckoutForm": {"id": "CheckoutForm", "code_digest": digest,
                                        "contract_digest": digest, "brief_digest": brief_digest,
                                        "evidence_ids": ["E1"],
                                        "dependency_ids": ["D1"], "approval_id": "A1"}},
        "dependencies": {"D1": {"id": "D1", "digest": digest, "component_ids": ["CheckoutForm"]}},
        "evidence": {"E1": {"id": "E1", "component_id": "CheckoutForm", "digest": digest,
                            "code_digest": digest, "contract_digest": digest,
                            "brief_digest": brief_digest,
                            "widths": ["compact", "intermediate", "wide"],
                            "themes": ["light", "dark"], "states": ["default", "error", "loading"],
                            "technical": passing_technical(digest, "component-check")}},
        "approvals": {"A1": {"id": "A1", "component_id": "CheckoutForm", "code_digest": digest,
                             "contract_digest": digest, "brief_digest": brief_digest,
                             "evidence_ids": ["E1"],
                             "dependency_ids": ["D1"], "decision": "approved"}},
        "delivery": {
            "classifications": {"candidate": "component", "review": "review", "manifest": "manifest"},
            "candidate": {"inventory_digest": inventory_digest(candidate_payload),
                          "files": candidate_payload["files"]},
            "review": {"inventory_digest": inventory_digest(review_evidence),
                       "files": review_evidence["files"]},
            "manifest": {"inventory_digest": inventory_digest(manifest), "files": manifest["files"]}},
    }
    out = bench.run(DESIGN_STATE, "apply", cwd=repo, env=env,
                    stdin={"workflow_id": workflow, "expected_revision": revision,
                           "updates": records})
    bench.record("design_state.apply", out, check_keys("revision"))
    revision = json.loads(out)["revision"]
    out = bench.run(DESIGN_STATE, "pause", workflow, str(revision), cwd=repo, env=env)
    bench.record("design_state.pause", out, check_keys("revision"))
    revision = json.loads(out)["revision"]
    out = bench.run(DESIGN_STATE, "resume", workflow, str(revision), cwd=repo, env=env)
    bench.record("design_state.resume", out, check_keys("revision"))
    revision = json.loads(out)["revision"]
    out = bench.run(DESIGN_STATE, "preflight", workflow, cwd=repo, env=env)
    bench.record("design_state.preflight", out, check_keys("eligible", "problems"))
    out = bench.run(DESIGN_STATE, "deliver", cwd=repo, env=env,
                    stdin={"workflow_id": workflow, "expected_revision": revision,
                           "candidate_payload": candidate_payload,
                           "review_evidence": review_evidence, "manifest": manifest})
    bench.record("design_state.deliver", out, check_keys("workflow_id", "candidate_commit"))


def scenario_plan_graph(bench: Bench, root: Path) -> None:
    from tests.test_plan_graph import minimal_graph

    repo, _ = make_repo(root / "plan", branch="feature/config-validation")
    home = root / "plan-home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    graph = minimal_graph()
    out = bench.run(PLAN_GRAPH, "initialize", "--repo", str(repo),
                    "--branch", "feature/config-validation",
                    "--json", json.dumps(graph), env=env)
    bench.record("plan_graph.initialize", out)
    workflow = json.loads(out)["workflow_id"]
    revision = json.loads(out)["revision"]
    out = bench.run(PLAN_GRAPH, "discover", "--repo", str(repo),
                    "--branch", "feature/config-validation", env=env)
    bench.record("plan_graph.discover", out)
    out = bench.run(PLAN_GRAPH, "load", "--repo", str(repo),
                    "--branch", "feature/config-validation", env=env)
    bench.record("plan_graph.load", out)
    out = bench.run(PLAN_GRAPH, "state", "--repo", str(repo),
                    "--branch", "feature/config-validation", env=env)
    bench.record("plan_graph.state", out)
    out = bench.run(PLAN_GRAPH, "pause", "--repo", str(repo),
                    "--branch", "feature/config-validation",
                    "--workflow-id", workflow, "--revision", str(revision), env=env)
    bench.record("plan_graph.pause", out)


def scenario_worktrees(bench: Bench, root: Path) -> None:
    repo, head = make_repo(root / "wt")
    home = root / "wt-home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    out = bench.run(WORKTREES, "create", "--repo", str(repo), "--base", head,
                    "--run-id", "bench", "--task", "t1", env=env)
    bench.record("worktrees.create", out)


def scenario_research_record(bench: Bench, root: Path) -> None:
    state_home = root / "research-home"
    state_home.mkdir()
    env = {**os.environ, "HOME": str(state_home)}
    brief = {"question": "How do heat pumps perform in cold climates?",
             "initial_branches": [{"title": "Branch 1", "question": "Q1?",
                                   "rationale": "R1."}]}
    def check_keys(*keys: str):
        def check(payload: object) -> None:
            assert isinstance(payload, dict), f"expected JSON object, got: {payload!r}"[:300]
            missing = [key for key in keys if key not in payload]
            assert not missing, f"missing keys {missing} in {str(payload)[:300]}"
            assert payload.get("ok", True) is not False, f"error body: {str(payload)[:300]}"
        return check

    out = bench.run(RESEARCH_RECORD, "init", "--root", str(state_home), stdin=brief, env=env)
    bench.record("research_record.init", out, check_keys("run_dir"))
    run_dir = json.loads(out)["run_dir"]
    out = bench.run(RESEARCH_RECORD, "apply", run_dir,
                    stdin={"ops": [{"op": "start_phase", "kind": "discovery", "worklist": ["B1"]},
                                    {"op": "add", "type": "assignment",
                                     "item": {"id": "A1", "branch_ids": ["B1"],
                                              "question": "Survey B1?", "queries": ["q"],
                                              "budget": {"searches": 3}}}]}, env=env)
    bench.record("research_record.apply", out, check_keys("ok"))
    out = bench.run(RESEARCH_RECORD, "status", run_dir, env=env)
    bench.record("research_record.status", out, check_keys("ok"))
    out = bench.run(RESEARCH_RECORD, "worker-brief", run_dir, "--assignment", "A1", env=env)
    bench.record("research_record.worker-brief-json", out, check_keys("text"))
    out = bench.run(RESEARCH_RECORD, "worker-brief", run_dir, "--assignment", "A1",
                    "--format", "text", env=env)
    text = out.decode("utf-8", "replace")
    assert text.strip(), "empty worker-brief text output"
    bench.rows["research_record.worker-brief-text"] = {
        "bytes": len(out), "lines": text.count("\n"), "tokens_est": estimate_tokens(text),
    }
    out = bench.run(RESEARCH_RECORD, "schema", env=env)
    bench.record("research_record.schema", out, check_keys("ok"))


def scenario_run_state(bench: Bench, root: Path) -> None:
    state_root = root / "builder-home"
    state_root.mkdir()
    env = {**os.environ, "HOME": str(state_root)}
    target = root / "skill"  # create mode requires an absent target
    payload = {
        "host_identity": {"kind": "codex", "canonical_id": "codex:bench-host",
                          "locator": str(root.resolve()),
                          "discovery_evidence": ["bench fixture"]},
        "target_identity": {"requested": "sample-skill", "canonical": "codex:personal:sample-skill",
                            "name": "sample-skill", "invocation_token": "$sample-skill",
                            "locator": str(target.resolve())},
        "mode": "create",
        "authority": {"reads": ["target", "host-registry"], "writes": ["isolated-candidate"],
                      "delegation": [], "candidate_effects": ["isolated-only"], "delivery_effects": []},
        "absence_evidence": {"searched": [str(target)], "exists": False},
        "overlap_map": {"exact": [], "near_neighbours": []},
        "git_identity": {"present": False},
    }
    out = bench.run(RUN_STATE, "initialize", stdin=payload, env=env)
    bench.record("run_state.initialize", out, check_keys("workflow_id", "sequence"))
    workflow = json.loads(out)["workflow_id"]
    out = bench.run(RUN_STATE, "discover", stdin={"host_identity": payload["host_identity"],
                                                 "target_identity": payload["target_identity"]}, env=env)
    bench.record("run_state.discover", out, check_keys("workflow_id", "sequence"))
    out = bench.run(RUN_STATE, "load", stdin={"workflow_id": workflow}, env=env)
    bench.record("run_state.load", out, check_keys("head_sequence", "stage"))


def scenario_bootstrap(bench: Bench, root: Path) -> None:
    repo, _ = make_repo(root / "strap")
    for index in range(20):
        (repo / f"mod{index:02d}.py").write_text(f"# module {index}\nVALUE = {index}\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "bench files")
    home = root / "strap-home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}
    out = bench.run(BOOTSTRAP, cwd=repo, env=env)
    bench.record("bootstrap_run", out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="write the JSON report here")
    parser.add_argument("--compare", default=None, help="baseline JSON to diff against")
    args = parser.parse_args(argv)
    bench = Bench()
    with tempfile.TemporaryDirectory(prefix="bench-tokens-") as tmp:
        root = Path(tmp)
        scenario_design_state(bench, root)
        scenario_plan_graph(bench, root)
        scenario_worktrees(bench, root)
        scenario_research_record(bench, root)
        scenario_run_state(bench, root)
        scenario_bootstrap(bench, root)
    report = bench.report()
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.compare is None:
        print(f"wrote {args.out}: {report['total_tokens_est']} est. tokens, "
              f"{report['total_bytes']} bytes across {len(report['commands'])} commands")
        return 0
    base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    base_rows = base["commands"]
    print(f"{'command':42s} {'before':>8s} {'after':>8s} {'delta':>8s}")
    total_before = total_after = 0
    for label, row in report["commands"].items():
        before = base_rows.get(label, {}).get("tokens_est", 0)
        after = row["tokens_est"]
        total_before += before
        total_after += after
        print(f"{label:42s} {before:8d} {after:8d} {after - before:+8d}")
    saved = total_before - total_after
    pct = 100.0 * saved / total_before if total_before else 0.0
    print(f"{'TOTAL':42s} {total_before:8d} {total_after:8d} {-saved:+8d} ({pct:.1f}% fewer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
