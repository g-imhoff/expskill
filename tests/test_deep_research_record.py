from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "expskill"
    / "content"
    / "skills"
    / "deep-research"
    / "scripts"
    / "research_record.py"
)
REPLY = b"## Coverage\nAll assigned branches.\n## Findings\n- finding\n"


def add(record_type: str, **item: object) -> dict[str, object]:
    return {"op": "add", "type": record_type, "item": item}


def update(record_type: str, record_id: str, **fields: object) -> dict[str, object]:
    return {"op": "update", "type": record_type, "id": record_id, "set": fields}


def start(kind: str, worklist: list[str], **extra: object) -> dict[str, object]:
    return {"op": "start_phase", "kind": kind, "worklist": worklist, **extra}


def complete(phase_id: str, duplicates: int = 0, **extra: object) -> dict[str, object]:
    return {"op": "complete_phase", "id": phase_id, "duplicates": duplicates, **extra}


def support(source_id: str) -> list[dict[str, str]]:
    return [{"source_id": source_id, "locator": "section 2", "excerpt": "measured output held"}]


def source(source_id: str, access: str = "opened", found_by: str = "coordinator") -> dict[str, object]:
    return add(
        "source",
        id=source_id,
        url=f"https://example.org/{source_id.lower()}",
        title=f"Report {source_id}",
        source_type="primary",
        access=access,
        found_by=found_by,
    )


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def fact(claim_id: str, branch: str, source_id: str, **extra: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": claim_id,
        "statement": f"Statement {claim_id}.",
        "epistemic": "fact",
        "branch_ids": [branch],
        "status": "accepted",
        "support": support(source_id),
    }
    item.update(extra)
    return add("claim", **item)


class ResearchRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "records"
        self.env = dict(os.environ, XDG_STATE_HOME=str(self.base / "xdg-state"))

    def run_script(self, *args: str, stdin: object = None, code: int = 0) -> tuple[bytes, str]:
        if stdin is None:
            data = b""
        elif isinstance(stdin, bytes):
            data = stdin
        else:
            data = json.dumps(stdin).encode("utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            check=False,
            timeout=60,
        )
        detail = completed.stdout.decode("utf-8", "replace") + completed.stderr.decode("utf-8", "replace")
        self.assertEqual(completed.returncode, code, detail)
        return completed.stdout, detail

    def cli(self, *args: str, stdin: object = None, code: int = 0) -> dict:
        stdout, detail = self.run_script(*args, stdin=stdin, code=code)
        self.assertTrue(stdout.endswith(b"\n"), detail)
        result = json.loads(stdout)
        self.assertEqual(result["ok"], code == 0, detail)
        return result

    def init(self, branches: int = 2, **brief: object) -> Path:
        payload: dict[str, object] = {"question": "How do heat pumps perform in cold climates?"}
        payload.update(brief)
        payload["initial_branches"] = [
            {"title": f"Branch {n}", "question": f"Question {n}?", "rationale": f"Rationale {n}."}
            for n in range(1, branches + 1)
        ]
        return Path(self.cli("init", "--root", str(self.root), stdin=payload)["run_dir"])

    def apply(self, run: Path, *ops: dict[str, object], code: int = 0) -> dict:
        return self.cli("apply", str(run), stdin={"ops": list(ops)}, code=code)

    def state(self, run: Path) -> dict:
        return json.loads((run / "state.json").read_bytes())

    def status(self, run: Path) -> dict:
        return self.cli("status", str(run))

    def errors(self, result: dict) -> str:
        return "\n".join(result["errors"])

    def capture(self, run: Path, assignment: str, reply: bytes = REPLY, *flags: str, code: int = 0) -> dict:
        return self.cli("capture", str(run), "--assignment", assignment, *flags, stdin=reply, code=code)

    def finish_assignment(self, run: Path, assignment: str) -> None:
        self.apply(run, update("assignment", assignment, status="running"))
        self.capture(run, assignment)
        self.apply(run, update("assignment", assignment, status="complete"))

    def covered_run(self) -> Path:
        run = self.init(branches=1)
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Survey B1"))
        self.finish_assignment(run, "A1")
        self.apply(
            run,
            source("S1", found_by="A1"),
            fact("C1", "B1", "S1"),
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="First finding."),
            update("branch", "B1", status="covered"),
            complete("P1"),
        )
        return run

    def zero_phase(self, run: Path, kind: str) -> dict:
        worklist = [b["id"] for b in self.state(run)["branches"] if b["status"] != "out_of_scope"]
        methods = ["counterexample search"] if kind == "challenge" else []
        result = self.apply(
            run,
            start(kind, worklist, methods=methods),
            add("assignment", branch_ids=worklist, question="Re-check every branch."),
        )
        phase_id, assignment_id = result["ids"]
        self.finish_assignment(run, assignment_id)
        uncovered = [b["id"] for b in self.state(run)["branches"] if b["status"] in ("queued", "active")]
        self.apply(run, *[update("branch", bid, status="covered") for bid in uncovered], complete(phase_id))
        return self.state(run)["phases"][-1]

    def saturated_run(self) -> Path:
        run = self.covered_run()
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        self.cli("finish", str(run), "--status", "saturated", "--reason", "Two quiet phases.")
        return run

    def test_init_creates_private_layout_and_first_checkpoint(self) -> None:
        run = self.init(branches=2)
        self.assertEqual(run.parent, self.root.resolve())
        self.assertTrue(run.name.startswith("how-do-heat-pumps-perform-in-col-"))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for directory in (run, run / "checkpoints", run / "returns"):
            self.assertEqual(stat.S_IMODE(directory.lstat().st_mode), 0o700)
        self.assertEqual(sorted(path.name for path in run.iterdir()),
                         ["CURRENT", "checkpoints", "lock", "owner.json", "returns", "state.json"])
        for name in ("owner.json", "state.json", "CURRENT", "lock", "checkpoints/0000.json"):
            self.assertEqual(stat.S_IMODE((run / name).lstat().st_mode), 0o600, name)
        owner = json.loads((run / "owner.json").read_bytes())
        self.assertEqual(
            sorted(owner), ["canonical_root", "created_at", "run_dir", "run_id", "schema", "uid"]
        )
        self.assertEqual(owner["run_dir"], str(run))
        self.assertEqual(owner["uid"], os.getuid())
        state = self.state(run)
        self.assertEqual(state["schema"], "deep-research.v1")
        self.assertEqual(state["brief"]["scope_id"], "S1")
        self.assertEqual(state["brief"]["inclusions"], [])
        self.assertEqual(state["brief"]["browsing_method"], "")
        self.assertEqual([(b["id"], b["status"], b["discovered_in"]) for b in state["branches"]],
                         [("B1", "queued", None), ("B2", "queued", None)])
        current = json.loads((run / "CURRENT").read_bytes())
        payload = (run / "checkpoints" / "0000.json").read_bytes()
        self.assertEqual(current, {"sequence": 0, "sha256": hashlib.sha256(payload).hexdigest()})
        checkpoint = json.loads(payload)
        self.assertIsNone(checkpoint["prior_sha256"])
        self.assertEqual(checkpoint["state"], state)
        self.assertEqual(self.cli("verify", str(run))["problems"], [])

    def test_init_refuses_root_inside_git_repository(self) -> None:
        repository = self.base / "repository"
        (repository / ".git").mkdir(parents=True)
        root = repository / "nested" / "records"
        result = self.cli("init", "--root", str(root), stdin={"question": "Q?"}, code=1)
        self.assertIn("inside a git repository", self.errors(result))
        self.assertFalse((repository / "nested").exists())

    def test_init_refuses_shared_root_and_invalid_brief(self) -> None:
        shared = self.base / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        result = self.cli("init", "--root", str(shared), stdin={"question": "Q?"}, code=1)
        self.assertIn("group- or world-writable", self.errors(result))
        result = self.cli("init", "--root", str(self.root), stdin={"topic": "Q?"}, code=1)
        self.assertIn("unknown field 'topic'", self.errors(result))
        self.assertFalse(self.root.exists())

    def test_apply_is_all_or_nothing(self) -> None:
        run = self.init()
        before = (run / "state.json").read_bytes()
        result = self.apply(
            run,
            source("S1"),
            start("discovery", ["B1"]),
            fact("C1", "B1", "S9"),
            code=1,
        )
        self.assertIn("ops[2]: claim C1: field support references unknown source S9", self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)
        result = self.apply(run, source("S1"), update("branch", "B7", status="active"), code=1)
        self.assertIn("ops[1] (update branch B7): no branch with id 'B7'", self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)

    def test_unknown_fields_and_loose_json_are_rejected(self) -> None:
        run = self.init()
        before = (run / "state.json").read_bytes()
        result = self.apply(run, add("branch", titel="X", question="Q?", rationale="R."), code=1)
        message = self.errors(result)
        self.assertIn("ops[0] (add branch): unknown field 'titel', allowed fields:", message)
        self.assertIn("missing required field title", message)
        result = self.apply(run, update("branch", "B1", colour="blue"), code=1)
        self.assertIn("unknown field 'colour'", self.errors(result))
        result = self.apply(run, add("branch", title="X", question="Q?", rationale="R.", scope_id="S1"), code=1)
        self.assertIn("field scope_id is set by the script, remove it", self.errors(result))
        result = self.apply(run, {"op": "start_phase", "kind": "discovery", "worklist": ["B1"], "colour": 1}, code=1)
        self.assertIn("start_phase op has unknown key 'colour'", self.errors(result))
        result = self.cli("apply", str(run), stdin={"ops": [start("discovery", ["B1"])], "extra": 1}, code=1)
        self.assertIn("apply input has unknown key 'extra'", self.errors(result))
        result = self.cli("apply", str(run), stdin=b'{"ops": [], "ops": []}', code=1)
        self.assertIn("duplicate key 'ops'", self.errors(result))
        result = self.cli("apply", str(run), stdin=b'{"ops": [{"op": "add", "type": "source", "item": {"x": NaN}}]}', code=1)
        self.assertIn("NaN is not allowed", self.errors(result))
        result = self.cli("apply", str(run), stdin={"ops": [{"op": ["add"]}, {"op": {"x": 1}}]}, code=1)
        self.assertIn('op must be an object with "op" one of add, update', self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)

    def test_claim_acceptance_rules(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1", "B2"]), source("S1"), source("S2", access="unverified"))
        self.assertIsNotNone(self.state(run)["sources"][0]["retrieved_at"])
        self.assertIsNone(self.state(run)["sources"][1]["retrieved_at"])
        for epistemic in ("fact", "uncertain"):
            result = self.apply(run, fact("C1", "B1", "S2", epistemic=epistemic), code=1)
            self.assertIn(f"an accepted {epistemic} claim needs a support item whose source has access opened",
                          self.errors(result))
        result = self.apply(run, fact("C1", "B1", "S2", epistemic="inference"), code=1)
        self.assertIn("an accepted inference needs based_on", self.errors(result))
        self.apply(run, fact("C1", "B1", "S1"), fact("C2", "B1", "S2", epistemic="inference", based_on=["C1"]))
        result = self.apply(run, fact("C3", "B1", "S1", status="duplicate"), code=1)
        self.assertIn("status duplicate needs duplicate_of naming another accepted claim", self.errors(result))
        result = self.apply(run, fact("C3", "B1", "S1", status="rejected"), code=1)
        self.assertIn("rejection_reason is required when rejected", self.errors(result))
        self.apply(run, fact("C3", "B1", "S1", status="rejected", rejection_reason="Off topic."))
        result = self.apply(run, fact("C4", "B1", "S1", status="duplicate", duplicate_of="C3"), code=1)
        self.assertIn("status duplicate needs duplicate_of", self.errors(result))
        self.apply(run, fact("C4", "B1", "S2", status="duplicate", duplicate_of="C1"))
        result = self.apply(run, update("source", "S1", access="inaccessible"), code=1)
        message = self.errors(result)
        self.assertIn("after all ops: claim C1: an accepted fact claim needs a support item", message)

    def test_novelty_rules_and_complete_phase_hard_errors(self) -> None:
        run = self.init()
        result = self.apply(run, add("novelty", kind="new_claim", reason="x"), code=1)
        self.assertIn("no phase is running", self.errors(result))
        self.apply(run, start("discovery", ["B1", "B2"]), add("assignment", id="A1", branch_ids=["B1", "B2"], question="Q"))
        self.finish_assignment(run, "A1")
        self.apply(
            run,
            source("S1"),
            fact("C1", "B1", "S1"),
            fact("C2", "B2", "S1", status="rejected", rejection_reason="Wrong."),
            add("branch", id="B3", title="Branch 3", question="Q3?", rationale="Found in S1."),
        )
        result = self.apply(run, complete("P1"), code=1)
        message = self.errors(result)
        self.assertIn("accepted claim C1 has no novelty event in phase P1, record novelty or mark it duplicate/rejected", message)
        self.assertIn("branch B3 was discovered in phase P1 but has no new_branch novelty event", message)
        result = self.apply(run, add("novelty", kind="new_claim", claim_ids=["C2"], reason="x"), code=1)
        self.assertIn("claim C2 must be accepted and recorded in phase P1", self.errors(result))
        result = self.apply(run, add("novelty", kind="new_claim", reason="x"), code=1)
        self.assertIn("needs at least one claim id", self.errors(result))
        result = self.apply(run, add("novelty", kind="new_branch", branch_ids=["B1"], reason="x"), code=1)
        self.assertIn("branch B1 must be discovered_in P1", self.errors(result))
        result = self.apply(run, add("novelty", kind="contradiction", claim_ids=["C1"], reason="x"), code=1)
        self.assertIn("non-empty contradicts", self.errors(result))
        result = self.apply(
            run,
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="x"),
            add("novelty", kind="new_mechanism", claim_ids=["C1"], reason="y"),
            code=1,
        )
        self.assertIn("claim C1 already appears in novelty N1", self.errors(result))
        self.apply(
            run,
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="x"),
            add("novelty", kind="new_branch", branch_ids=["B3"], reason="y"),
            update("branch", "B1", status="covered"),
            complete("P1"),
        )
        phase = self.state(run)["phases"][0]
        self.assertEqual((phase["novelty_count"], phase["zero_novelty"]), (2, False))
        result = self.apply(run, update("novelty", "N1", reason="edited"), code=1)
        self.assertIn("completed phases are immutable", self.errors(result))

    def test_completion_records_eligibility_blockers(self) -> None:
        run = self.init(branches=3)
        self.apply(
            run,
            start("challenge", ["B1", "B2"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q1"),
            add("assignment", id="A2", branch_ids=["B2"], question="Q2", status="running"),
        )
        self.finish_assignment(run, "A1")
        result = self.apply(
            run,
            add("failure", id="F1", assignment_id="A2", kind="tool", detail="Search tool crashed."),
            update("assignment", "A2", status="failed"),
            update("branch", "B1", status="covered"),
            complete("P1"),
        )
        phase = self.state(run)["phases"][0]
        self.assertEqual(result["completed_phases"], [
            {key: phase[key] for key in ("id", "eligible", "zero_novelty", "novelty_count", "blockers")}
        ])
        self.assertIn("branch B2 is active at completion, set it covered before complete_phase once a complete "
                      "assignment researched it", phase["blockers"])
        self.assertFalse(phase["eligible"])
        self.assertFalse(phase["zero_novelty"])
        self.assertEqual(phase["novelty_count"], 0)
        blockers = "\n".join(phase["blockers"])
        for expected in (
            "worklist branch B2 has no complete assignment in phase P1",
            "frontier branch B3 existed at phase start but is not in the worklist",
            "branch B2 is active at completion",
            "branch B3 is queued at completion",
            "assignment A2 is failed",
            "failure F1 of this phase is unresolved",
            "challenge phase lists no methods",
        ):
            self.assertIn(expected, blockers)
        self.assertNotIn("branch B1", blockers)

    def test_saturation_requires_two_zero_novelty_phases_ending_with_challenge(self) -> None:
        run = self.covered_run()
        self.assertEqual(self.status(run)["streak"], 0)
        self.assertTrue(self.zero_phase(run, "challenge")["zero_novelty"])
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (1, False))
        result = self.cli("finish", str(run), "--status", "saturated", "--reason", "quiet", code=1)
        self.assertIn("zero-novelty streak is 1", self.errors(result))
        self.zero_phase(run, "investigation")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (2, False))
        self.assertIn("the most recent completed phase P3 is investigation, it must be a challenge phase",
                      report["reasons"])
        self.cli("finish", str(run), "--status", "saturated", "--reason", "quiet", code=1)
        self.zero_phase(run, "challenge")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"], report["reasons"]), (3, True, []))
        result = self.cli("finish", str(run), "--status", "saturated", "--reason", "Three quiet phases.")
        self.assertEqual(result["summary"]["status"], "saturated")

    def test_novelty_resets_streak(self) -> None:
        run = self.covered_run()
        self.zero_phase(run, "investigation")
        self.assertEqual(self.status(run)["streak"], 1)
        result = self.apply(
            run,
            start("challenge", ["B1"], methods=["opposing sources"]),
            add("assignment", branch_ids=["B1"], question="Find counterevidence."),
        )
        self.finish_assignment(run, result["ids"][1])
        self.apply(
            run,
            source("S2"),
            fact("C2", "B1", "S2", contradicts=["C1"]),
            add("novelty", kind="contradiction", claim_ids=["C2"], reason="Field data disagrees."),
            complete("P3"),
        )
        phase = self.state(run)["phases"][-1]
        self.assertEqual((phase["eligible"], phase["zero_novelty"]), (True, False))
        self.assertEqual(self.status(run)["streak"], 0)
        self.zero_phase(run, "investigation")
        self.assertEqual(self.status(run)["streak"], 1)
        self.zero_phase(run, "challenge")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (2, True))

    def test_failed_and_truncated_assignments_block_eligibility_until_resolved(self) -> None:
        run = self.covered_run()
        self.apply(
            run,
            start("investigation", ["B1"]),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", status="running"),
            add("assignment", id="A3", branch_ids=["B1"], question="Q", status="running"),
        )
        self.capture(run, "A3", b"partial reply", "--truncated")
        self.apply(
            run,
            add("failure", id="F1", assignment_id="A2", kind="tool", detail="Fetch failed."),
            update("assignment", "A2", status="failed"),
            update("assignment", "A3", status="truncated"),
            complete("P2"),
        )
        blockers = "\n".join(self.state(run)["phases"][-1]["blockers"])
        self.assertIn("assignment A2 is failed", blockers)
        self.assertIn("assignment A3 is truncated", blockers)
        self.assertIn("failure F1 of this phase is unresolved", blockers)
        self.apply(
            run,
            start("investigation", ["B1"]),
            add("assignment", id="A4", branch_ids=["B1"], question="Q", status="running"),
            add("assignment", id="A5", branch_ids=["B1"], question="Q", status="running"),
            add("failure", id="F2", assignment_id="A4", kind="tool", detail="Fetch failed."),
            update("assignment", "A4", status="failed"),
            add("assignment", id="A6", branch_ids=["B1"], question="Q", retry_of="A4"),
        )
        self.capture(run, "A5", b"cut off", "--truncated")
        self.apply(run, update("assignment", "A5", status="truncated"),
                   add("assignment", id="A7", branch_ids=["B1"], question="Q", continuation_of="A5"))
        self.finish_assignment(run, "A6")
        self.apply(run, update("assignment", "A7", status="running"))
        result = self.apply(run, complete("P3"), code=1)
        self.assertIn("assignments A7 are still planned or running", self.errors(result))
        self.capture(run, "A7", b"rest of reply")
        self.apply(run, update("assignment", "A7", status="complete"), complete("P3"))
        blockers = self.state(run)["phases"][-1]["blockers"]
        self.assertEqual(blockers, ["failure F2 of this phase is unresolved"])
        self.apply(
            run,
            start("investigation", ["B1"]),
            add("assignment", id="A8", branch_ids=["B1"], question="Q", status="running"),
            add("assignment", id="A9", branch_ids=["B1"], question="Q", status="running"),
            add("failure", id="F3", assignment_id="A8", kind="source", detail="Paywall."),
            update("assignment", "A8", status="failed"),
        )
        self.finish_assignment(run, "A9")
        self.apply(
            run,
            update("failure", "F2", resolved=True, resolution="Retry A6 completed.", resolved_by="A6"),
            update("failure", "F3", resolved=True, resolution="A9 covered the branch.", resolved_by="A9"),
            complete("P4"),
        )
        phase = self.state(run)["phases"][-1]
        self.assertEqual((phase["blockers"], phase["eligible"], phase["zero_novelty"]), ([], True, True))
        returns = self.state(run)["returns"]
        self.assertEqual([r["truncated"] for r in returns if r["assignment_id"] in ("A3", "A5")], [True, True])

    def test_one_corrective_retry_per_lineage(self) -> None:
        run = self.init()
        self.apply(
            run,
            start("discovery", ["B1"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q", status="running"),
            add("failure", id="F1", assignment_id="A1", kind="tool", detail="Crashed."),
            update("assignment", "A1", status="failed"),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", retry_of="A1", status="running"),
        )
        result = self.apply(run, add("assignment", id="A3", branch_ids=["B1"], question="Q", retry_of="A1"), code=1)
        self.assertIn("A1 already has retry_of A2, only one per assignment", self.errors(result))
        self.apply(
            run,
            add("failure", id="F2", assignment_id="A2", kind="tool", detail="Crashed again."),
            update("assignment", "A2", status="failed"),
        )
        result = self.apply(run, add("assignment", id="A3", branch_ids=["B1"], question="Q", retry_of="A2"), code=1)
        self.assertIn("A2 is itself a retry or continues one, one corrective retry per lineage", self.errors(result))
        self.apply(run, add("assignment", id="A4", branch_ids=["B1"], question="Q"))
        result = self.apply(run, add("assignment", id="A5", branch_ids=["B1"], question="Q", retry_of="A4"), code=1)
        self.assertIn("field retry_of needs A4 to have status failed (it is planned)", self.errors(result))
        result = self.apply(run, add("assignment", id="A5", branch_ids=["B1"], question="Q", continuation_of="A1"), code=1)
        self.assertIn("needs A1 to have status truncated or interrupted", self.errors(result))
        result = self.apply(
            run, add("assignment", id="A5", branch_ids=["B1"], question="Q", retry_of="A1", continuation_of="A1"), code=1
        )
        self.assertIn("retry_of and continuation_of are mutually exclusive", self.errors(result))
        result = self.apply(run, update("assignment", "A2", retry_of=None), code=1)
        self.assertIn("field retry_of cannot be updated", self.errors(result))
        self.apply(
            run,
            add("assignment", id="A6", branch_ids=["B1"], question="Q", status="running"),
            add("failure", id="F3", assignment_id="A6", kind="tool", detail="Crashed."),
            update("assignment", "A6", status="failed"),
            add("assignment", id="A7", branch_ids=["B1"], question="Q", retry_of="A6", status="running"),
            update("assignment", "A7", status="truncated"),
            add("assignment", id="A8", branch_ids=["B1"], question="Q", continuation_of="A7", status="running"),
            add("failure", id="F4", assignment_id="A8", kind="tool", detail="Crashed again."),
            update("assignment", "A8", status="failed"),
        )
        result = self.apply(run, add("assignment", id="A9", branch_ids=["B1"], question="Q", retry_of="A8"), code=1)
        self.assertIn("A8 is itself a retry or continues one, one corrective retry per lineage", self.errors(result))

    def test_resolving_a_failure_requires_a_different_complete_assignment(self) -> None:
        run = self.init()
        self.apply(
            run,
            start("discovery", ["B1"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q", status="running"),
            add("failure", id="F1", assignment_id="A1", kind="tool", detail="Crashed."),
            update("assignment", "A1", status="failed"),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", retry_of="A1"),
            add("assignment", id="A3", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            add("failure", id="F2", assignment_id="A3", kind="source", detail="Paywall."),
            update("assignment", "A3", status="complete"),
        )
        before = (run / "state.json").read_bytes()
        expected = "ops[0]: failure {}: a resolved failure needs resolved_by naming a different complete assignment"
        for failure_id, resolver in (("F1", None), ("F1", "A2"), ("F1", "A1"), ("F2", "A3")):
            result = self.apply(
                run, update("failure", failure_id, resolved=True, resolution="Recovered.", resolved_by=resolver), code=1
            )
            self.assertIn(expected.format(failure_id), self.errors(result), (failure_id, resolver))
            self.assertEqual((run / "state.json").read_bytes(), before)
        result = self.apply(run, update("failure", "F1", resolved=True, resolution="Recovered."), code=1)
        self.assertIn(expected.format("F1"), self.errors(result))
        self.finish_assignment(run, "A2")
        self.apply(
            run,
            update("failure", "F1", resolved=True, resolution="Retry A2 completed.", resolved_by="A2"),
            update("failure", "F2", resolved=True, resolution="A2 found an open copy.", resolved_by="A2"),
        )
        self.assertEqual([f["resolved"] for f in self.state(run)["failures"]], [True, True])

    def test_assignment_status_transitions_are_forward_only(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        result = self.apply(run, update("assignment", "A1", status="complete"), code=1)
        self.assertIn("status cannot go from planned to complete", self.errors(result))
        result = self.apply(run, add("assignment", branch_ids=["B1"], question="Q", status="complete"), code=1)
        self.assertIn("a new assignment starts as planned or running", self.errors(result))
        self.apply(run, update("assignment", "A1", status="running"))
        result = self.apply(run, update("assignment", "A1", status="complete"), code=1)
        self.assertIn("run capture first", self.errors(result))
        result = self.apply(run, update("assignment", "A1", status="failed"), code=1)
        self.assertIn("status failed needs a failure record with assignment_id A1", self.errors(result))
        self.apply(run, update("assignment", "A1", status="interrupted"))
        result = self.apply(run, update("assignment", "A1", status="running"), code=1)
        self.assertIn("terminal statuses never change", self.errors(result))
        self.apply(
            run,
            add("assignment", id="A2", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A2", status="complete"),
        )
        self.apply(run, add("assignment", id="A3", branch_ids=["B1"], question="Q"),
                   update("assignment", "A3", status="interrupted"))

    def test_capture_stores_verbatim_bytes_and_digest(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        reply = "## Coverage\r\nAll of it — résumé\n".encode("utf-8") + b"\x00\xff trailing"
        result = self.capture(run, "A1", reply)
        self.assertEqual(result["return_id"], "R1")
        self.assertEqual(result["file"], "returns/R1.txt")
        self.assertEqual(result["sha256"], hashlib.sha256(reply).hexdigest())
        self.assertEqual(result["bytes"], len(reply))
        stored = run / "returns" / "R1.txt"
        self.assertEqual(stored.read_bytes(), reply)
        self.assertEqual(stat.S_IMODE(stored.lstat().st_mode), 0o600)
        reply_file = self.base / "reply.txt"
        reply_file.write_bytes(b"second reply")
        result = self.cli("capture", str(run), "--assignment", "A1", "--file", str(reply_file), "--truncated")
        self.assertEqual((result["return_id"], result["truncated"]), ("R2", True))
        self.assertNotIn("inbox_copy_removed", result)
        self.assertTrue(reply_file.exists())
        state = self.state(run)
        self.assertEqual(state["assignments"][0]["return_ids"], ["R1", "R2"])
        self.assertEqual(state["returns"][1]["sha256"], hashlib.sha256(b"second reply").hexdigest())
        missing = self.cli("capture", str(run), "--assignment", "A1", "--file", str(self.base / "missing.txt"), code=1)
        self.assertIn(f"cannot read --file {self.base / 'missing.txt'}: No such file or directory, pass a readable "
                      "file or send the reply on stdin in a quoted heredoc", self.errors(missing))
        self.assertEqual(len(self.state(run)["returns"]), 2)
        verified = self.cli("verify", str(run))
        self.assertEqual((verified["problems"], verified["warnings"]), ([], []))
        self.assertIn("refusing empty input", self.errors(self.capture(run, "A1", b"", code=1)))
        self.assertIn("no assignment 'A9'", self.errors(self.capture(run, "A9", code=1)))
        self.apply(run, update("assignment", "A1", status="running"), update("assignment", "A1", status="complete"))
        self.assertIn("already complete", self.errors(self.capture(run, "A1", code=1)))

    def test_checkpoint_chain_links_digests_and_skips_unchanged_state(self) -> None:
        run = self.init()
        self.assertTrue(self.cli("checkpoint", str(run))["unchanged"])
        self.assertFalse(self.status(run)["uncheckpointed_changes"])
        self.apply(run, source("S1"))
        self.assertTrue(self.status(run)["uncheckpointed_changes"])
        result = self.cli("checkpoint", str(run), "--note", "after sources")
        self.assertEqual((result["unchanged"], result["sequence"]), (False, 1))
        first = (run / "checkpoints" / "0000.json").read_bytes()
        second = (run / "checkpoints" / "0001.json").read_bytes()
        document = json.loads(second)
        self.assertEqual(document["prior_sha256"], hashlib.sha256(first).hexdigest())
        self.assertEqual(document["note"], "after sources")
        self.assertEqual(document["state"], self.state(run))
        self.assertEqual(json.loads((run / "CURRENT").read_bytes())["sha256"], hashlib.sha256(second).hexdigest())
        self.assertEqual(self.cli("checkpoint", str(run))["unchanged"], True)
        verified = self.cli("verify", str(run))
        self.assertEqual((verified["sequence"], verified["uncheckpointed_changes"]), (1, False))

    def test_verify_detects_tampered_checkpoint(self) -> None:
        run = self.init()
        self.apply(run, source("S1"))
        self.cli("checkpoint", str(run))
        path = run / "checkpoints" / "0000.json"
        document = json.loads(path.read_bytes())
        document["note"] = "rewritten"
        path.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        result = self.cli("verify", str(run), code=1)
        self.assertIn("checkpoint 0001 prior_sha256 does not match the previous checkpoint", result["problems"])
        self.apply(run, source("S2"))
        before = (run / "state.json").read_bytes()
        for command in (("checkpoint", str(run)), ("finish", str(run), "--status", "incomplete", "--reason", "x")):
            message = self.errors(self.cli(*command, code=1))
            self.assertIn("refusing to publish a checkpoint, run verify", message)
            self.assertIn("checkpoint 0001 prior_sha256 does not match the previous checkpoint", message)
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.assertFalse((run / "checkpoints" / "0002.json").exists())
        latest = run / "checkpoints" / "0001.json"
        latest.write_bytes(latest.read_bytes().replace(b'"note":null', b'"note":"x"'))
        result = self.cli("verify", str(run), code=1)
        self.assertIn("CURRENT sha256 does not match checkpoint 0001", result["problems"])

    def test_verify_detects_tampered_return(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        self.capture(run, "A1")
        (run / "returns" / "R1.txt").write_bytes(REPLY + b"injected")
        result = self.cli("verify", str(run), code=1)
        self.assertIn("returns/R1.txt does not match its recorded sha256/bytes", result["problems"])

    def test_verify_detects_symlinks(self) -> None:
        run = self.init()
        target = self.base / "outside.txt"
        target.write_bytes(b"x")
        (run / "returns" / "R9.txt").symlink_to(target)
        result = self.cli("verify", str(run), code=1)
        self.assertIn("returns/R9.txt is a symlink, symlinks are never allowed in a run directory", result["problems"])

    def test_verify_detects_bad_modes(self) -> None:
        run = self.init()
        (run / "state.json").chmod(0o644)
        (run / "returns").chmod(0o755)
        result = self.cli("verify", str(run), code=1)
        self.assertIn("state.json has mode 644, expected 600", result["problems"])
        self.assertIn("returns has mode 755, expected 700", result["problems"])

    def test_resume_refuses_corrupt_state(self) -> None:
        run = self.init()
        self.cli("finish", str(run), "--status", "paused", "--reason", "User break.")
        (run / "state.json").write_bytes(b'{"schema": "deep-research.v1"}\n')
        before = (run / "state.json").read_bytes()
        result = self.cli("resume", str(run), "--reason", "Back.", code=1)
        self.assertIn("resume refused: verify found problems", self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.assertFalse((run / "checkpoints" / "0002.json").exists())

    def test_resume_refuses_cancelled_runs(self) -> None:
        run = self.init()
        self.cli("finish", str(run), "--status", "cancelled", "--reason", "User cancelled.")
        result = self.cli("resume", str(run), "--reason", "Retry.", code=1)
        self.assertIn("cancelled, which is terminal", self.errors(result))
        self.assertIn("terminal", self.errors(self.apply(run, source("S1"), code=1)))
        self.cli("finish", str(run), "--status", "incomplete", "--reason", "x", code=1)

    def test_resume_after_saturation_requires_flag_and_resets_closure(self) -> None:
        run = self.saturated_run()
        result = self.cli("resume", str(run), "--reason", "User wants more.", code=1)
        self.assertIn("pass --after-saturation", self.errors(result))
        result = self.cli("resume", str(run), "--reason", "User wants more.", "--after-saturation")
        self.assertTrue(result["closure_reset"])
        state = self.state(run)
        self.assertEqual(state["status"], "researching")
        self.assertIsNone(state["stop_reason"])
        self.assertEqual(state["closure_resets"][-1]["kind"], "continuation")
        self.assertEqual(state["closure_resets"][-1]["after_phase_index"], 3)
        self.assertEqual(state["resumes"][-1]["prior_status"], "saturated")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (0, False))
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        self.assertTrue(self.status(run)["saturation_allowed"])
        self.cli("finish", str(run), "--status", "paused", "--reason", "Break.")
        result = self.cli("resume", str(run), "--reason", "Back.", "--after-saturation", code=1)
        self.assertIn("--after-saturation applies only to saturated runs", self.errors(result))

    def test_change_scope_resets_closure_and_flags_old_scope_claims(self) -> None:
        run = self.covered_run()
        result = self.apply(run, add("branch", id="B2", title="Costs", question="Q?", rationale="R."), code=1)
        self.assertIn("no phase is running, add a newly found branch while a phase runs", self.errors(result))
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        self.assertTrue(self.status(run)["saturation_allowed"])
        result = self.apply(
            run,
            {
                "op": "change_scope",
                "reason": "User narrowed to residential units.",
                "brief": {"exclusions": ["commercial units"]},
                "affected_branch_ids": ["B1"],
            },
        )
        self.assertEqual(result["ids"], [])
        state = self.state(run)
        self.assertEqual(state["brief"]["scope_id"], "S2")
        self.assertEqual(state["brief"]["exclusions"], ["commercial units"])
        self.assertEqual(state["scope_history"][0]["scope_id"], "S1")
        self.assertEqual(state["branches"][0]["status"], "queued")
        self.assertEqual(state["branches"][0]["scope_id"], "S2")
        self.assertEqual(state["closure_resets"][-1]["kind"], "scope_change")
        self.assertEqual(state["closure_resets"][-1]["after_phase_index"], 3)
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (0, False))
        self.assertEqual(report["claims_needing_revalidation"], ["C1"])
        self.assertTrue(any("C1" in reason and "earlier scope" in reason for reason in report["reasons"]))
        result = self.apply(run, update("claim", "C1", scope_id="S1"), code=1)
        self.assertIn("scope_id may only be set to the current scope S2", self.errors(result))
        self.apply(run, update("claim", "C1", scope_id="S2"))
        self.assertEqual(self.status(run)["claims_needing_revalidation"], [])
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        self.assertTrue(self.status(run)["saturation_allowed"])
        self.apply(
            run,
            {"op": "change_scope", "reason": "Drop B1.", "out_of_scope": [{"id": "B1", "reason": "Excluded."}]},
            add("branch", id="B2", title="Costs", question="Q?", rationale="R."),
        )
        state = self.state(run)
        self.assertEqual(state["branches"][0]["status"], "out_of_scope")
        self.assertEqual(state["branches"][0]["out_of_scope_reason"], "Excluded.")
        self.assertEqual(state["brief"]["scope_id"], "S3")
        report = self.status(run)
        self.assertFalse(report["saturation_allowed"])
        self.assertIn("in-scope branches not covered: B2 (queued)", report["reasons"])

    def test_change_scope_requires_no_running_phase(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1"]))
        result = self.apply(run, {"op": "change_scope", "reason": "Narrow."}, code=1)
        self.assertIn("phase P1 is running, complete_phase it before change_scope", self.errors(result))

    def test_old_scope_claims_on_out_of_scope_branches_need_no_revalidation(self) -> None:
        run = self.init(branches=2)
        self.apply(run, start("discovery", ["B1", "B2"]), add("assignment", id="A1", branch_ids=["B1", "B2"], question="Q"))
        self.finish_assignment(run, "A1")
        self.apply(
            run,
            source("S1"),
            fact("C1", "B1", "S1"),
            fact("C2", "B2", "S1"),
            fact("C3", "B1", "S1", branch_ids=["B1", "B2"]),
            add("novelty", kind="new_claim", claim_ids=["C1", "C2", "C3"], reason="First findings."),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1"),
        )
        self.apply(run, {"op": "change_scope", "reason": "Drop B2.", "out_of_scope": [{"id": "B2", "reason": "Excluded."}]})
        self.assertEqual(self.status(run)["claims_needing_revalidation"], ["C1", "C3"])
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (2, False))
        self.assertEqual(report["claims_needing_revalidation"], ["C1", "C3"])
        self.assertEqual(
            report["reasons"],
            ["accepted claims from an earlier scope need revalidation (set scope_id S2 or reject them): C1, C3"],
        )
        self.apply(run, update("claim", "C1", scope_id="S2"))
        report = self.status(run)
        self.assertEqual(report["claims_needing_revalidation"], ["C3"])
        self.assertFalse(report["saturation_allowed"])
        self.apply(run, update("claim", "C3", scope_id="S2"))
        report = self.status(run)
        self.assertEqual((report["claims_needing_revalidation"], report["saturation_allowed"], report["reasons"]),
                         ([], True, []))
        self.assertEqual({c["id"]: c["scope_id"] for c in self.state(run)["claims"]}, {"C1": "S2", "C2": "S1", "C3": "S2"})
        self.cli("finish", str(run), "--status", "saturated", "--reason", "Two quiet phases in scope S2.")

    def test_revalidation_can_move_an_old_claim_onto_in_scope_branches(self) -> None:
        run = self.init(branches=2)
        self.apply(run, start("discovery", ["B1", "B2"]), add("assignment", id="A1", branch_ids=["B1", "B2"], question="Q"))
        self.finish_assignment(run, "A1")
        self.apply(
            run,
            source("S1"),
            fact("C1", "B2", "S1"),
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="First finding."),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1"),
        )
        self.apply(run, {"op": "change_scope", "reason": "Narrow to B1.", "affected_branch_ids": ["B1"],
                         "out_of_scope": [{"id": "B2", "reason": "Excluded."}]})
        before = (run / "state.json").read_bytes()
        message = self.errors(self.apply(run, update("claim", "C1", branch_ids=["B1"]), code=1))
        self.assertIn("set branch_ids together with scope_id S2 in one update", message)
        self.assertIn("out_of_scope", self.errors(
            self.apply(run, update("claim", "C1", scope_id="S2", branch_ids=["B2"]), code=1)))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.apply(run, update("claim", "C1", scope_id="S2", branch_ids=["B1"]))
        claim = self.state(run)["claims"][0]
        self.assertEqual((claim["scope_id"], claim["branch_ids"]), ("S2", ["B1"]))
        self.assertIn("frozen", self.errors(self.apply(run, update("claim", "C1", branch_ids=["B1", "B2"]), code=1)))
        self.apply(run, start("investigation", ["B1"]), add("assignment", id="A2", branch_ids=["B1"], question="Q2"))
        text = self.run_script("worker-brief", str(run), "--assignment", "A2", "--format", "text")[0].decode()
        self.assertIn("Statement C1.", text)

    def test_open_branch_next_questions_block_saturation_until_cleared(self) -> None:
        run = self.covered_run()
        self.apply(run, update("branch", "B1", next_questions=["Does it hold below -25 C?"]))
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"]), (2, False))
        self.assertEqual(len(report["reasons"]), 1)
        self.assertIn("in-scope branches with open next_questions", report["reasons"][0])
        self.assertIn("B1", report["reasons"][0])
        refused = self.cli("finish", str(run), "--status", "saturated", "--reason", "Quiet.", code=1)
        self.assertIn("open next_questions", " ".join(refused["errors"]))
        self.apply(run, update("branch", "B1", next_questions=[], notes="Answered by the challenge assignment."))
        self.assertTrue(self.status(run)["saturation_allowed"])
        self.cli("finish", str(run), "--status", "saturated", "--reason", "Two quiet phases.")

    def test_finish_paused_marks_running_assignments_interrupted(self) -> None:
        run = self.init()
        self.apply(
            run,
            start("discovery", ["B1"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q", status="running"),
            add("assignment", id="A2", branch_ids=["B1"], question="Q"),
        )
        result = self.cli("finish", str(run), "--status", "paused", "--reason", "User stepped away.")
        self.assertEqual((result["interrupted"], result["sequence"]), (["A1"], 1))
        state = self.state(run)
        self.assertEqual([a["status"] for a in state["assignments"]], ["interrupted", "planned"])
        self.assertEqual((state["status"], state["stop_reason"]), ("paused", "User stepped away."))
        self.assertIn("run status is paused", self.errors(self.apply(run, source("S1"), code=1)))
        result = self.cli("resume", str(run), "--reason", "User is back.")
        self.assertEqual((result["prior_status"], result["closure_reset"]), ("paused", False))
        self.apply(run, add("assignment", branch_ids=["B1"], question="Q", continuation_of="A1"))
        self.assertEqual(self.cli("verify", str(run))["problems"], [])

    def test_limits_block_start_phase_and_assignments(self) -> None:
        run = self.init(limits={"max_phases": 1, "max_assignments": 1, "max_sources": 1})
        self.apply(run, start("discovery", ["B1", "B2"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        result = self.apply(run, add("assignment", branch_ids=["B2"], question="Q"), code=1)
        self.assertIn("limits.max_assignments (1) is reached", self.errors(result))
        self.apply(run, update("assignment", "A1", status="interrupted"), complete("P1"))
        result = self.apply(run, start("investigation", ["B1"]), code=1)
        self.assertIn("limits.max_phases (1) is reached, finish the run as incomplete", self.errors(result))
        self.apply(run, source("S1"))
        result = self.apply(run, source("S2"), code=1)
        self.assertIn("ops[0] (add source S2): limits.max_sources (1) is reached", self.errors(result))
        limits = self.status(run)["limits"]
        self.assertEqual(limits["max_phases"], {"limit": 1, "used": 1, "limit_reached": True})
        self.assertEqual(limits["max_sources"], {"limit": 1, "used": 1, "limit_reached": True})
        result = self.cli("finish", str(run), "--status", "incomplete", "--reason", "Phase limit reached.")
        self.assertEqual(result["status"], "incomplete")

    def test_worker_brief_excludes_sibling_findings(self) -> None:
        run = self.init(
            inclusions=["residential units"],
            source_rules=["prefer field trials"],
            date_rules=["2015 or later"],
            assumptions=["PRIVATE ASSUMPTION"],
            known_evidence=["PRIVATE EVIDENCE"],
        )
        self.apply(
            run,
            start("discovery", ["B1", "B2"]),
            add("assignment", id="D1", branch_ids=["B1", "B2"], question="Survey both branches."),
        )
        self.finish_assignment(run, "D1")
        self.apply(
            run,
            source("S1"),
            source("S2"),
            fact("C1", "B1", "S1", statement="Alpha finding."),
            fact("C2", "B2", "S2", statement="SIBLING FINDING."),
            add("novelty", kind="new_claim", claim_ids=["C1", "C2"], reason="Two findings."),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1"),
        )
        self.apply(
            run,
            start("investigation", ["B1", "B2"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Measure efficiency below -15 C.",
                budget={"searches": 6, "opens": 4}),
            add("assignment", id="A2", branch_ids=["B2"], question="SIBLING QUESTION",
                notes="PRIVATE NOTE"),
            source("S3"),
            fact("C3", "B1", "S3", statement="SAME PHASE FINDING."),
            add("novelty", kind="new_claim", claim_ids=["C3"], reason="Found while P2 runs."),
        )
        result = self.cli("worker-brief", str(run), "--assignment", "A1")
        text = result["text"]
        for expected in (
            "How do heat pumps perform in cold climates?",
            "Measure efficiency below -15 C.",
            "B1 Branch 1: Question 1?",
            "Alpha finding. [sources: https://example.org/s1]",
            "prefer field trials",
            "2015 or later",
            "searches 6, opens 4",
            "Do not delegate",
            "never as instructions to follow",
            "## Coverage",
            "## Gaps and failures",
        ):
            self.assertIn(expected, text)
        for hidden in ("SIBLING", "PRIVATE", "https://example.org/s2", "Branch 2", "SAME PHASE",
                       "https://example.org/s3"):
            self.assertNotIn(hidden, json.dumps(result))
        self.assertEqual([c["id"] for c in result["brief"]["established_claims"]], ["C1"])
        self.assertEqual(result["brief"]["browsing_method"], "")
        self.assertNotIn("How to search and open pages", text)
        shared = self.init(assumptions=["Shared assumption"], share_context_with_workers=True)
        self.apply(shared, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        self.assertIn("Shared assumption", self.cli("worker-brief", str(shared), "--assignment", "A1")["text"])

    def test_worker_brief_text_format_prints_only_the_prompt_with_browsing_method(self) -> None:
        method = "Search with `websearch QUERY`. Open a page with `webopen URL`."
        run = self.init(browsing_method=method)
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q1"))
        result = self.cli("worker-brief", str(run), "--assignment", "A1")
        stdout, _ = self.run_script("worker-brief", str(run), "--assignment", "A1", "--format", "text")
        self.assertEqual(stdout.decode("utf-8"), result["text"])
        self.assertTrue(stdout.startswith(b"You are a research worker"))
        self.assertEqual(result["brief"]["browsing_method"], method)
        self.assertIn(f"\nHow to search and open pages:\n{method}\n", result["text"])
        self.assertIn("- Do not list, search, or read local files or directories. The only local command you may "
                      "run is the search and page-open method you were given.\n", result["text"])
        self.assertIn("Do not list, search, or read local files", " ".join(result["brief"]["boundaries"]))
        stdout, _ = self.run_script("worker-brief", str(run), "--assignment", "A9", "--format", "text", code=1)
        error = json.loads(stdout)
        self.assertEqual(error["ok"], False)
        self.assertIn("no assignment 'A9', add it with apply first", self.errors(error))
        self.apply(
            run,
            update("assignment", "A1", status="interrupted"),
            complete("P1"),
            {"op": "change_scope", "reason": "Host browser changed.", "brief": {"browsing_method": "Use the browse tool."}},
            start("investigation", ["B1"]),
            add("assignment", id="A2", branch_ids=["B1"], question="Q2"),
        )
        self.assertEqual(self.state(run)["scope_history"][0]["brief"]["browsing_method"], method)
        stdout, _ = self.run_script("worker-brief", str(run), "--assignment", "A2", "--format", "text")
        self.assertIn(b"How to search and open pages:\nUse the browse tool.\n", stdout)
        self.assertNotIn(b"websearch", stdout)

    def test_worker_brief_includes_parent_branch_claims_one_level_up(self) -> None:
        run = self.init(branches=2)
        self.apply(run, start("discovery", ["B1", "B2"]), add("assignment", id="D1", branch_ids=["B1", "B2"], question="Q"))
        self.finish_assignment(run, "D1")
        self.apply(
            run,
            source("S1"),
            fact("C1", "B1", "S1", statement="SIBLING FINDING."),
            fact("C2", "B2", "S1", statement="Parent finding."),
            add("branch", id="B3", title="Child", question="Child?", rationale="From C2.", parent_ids=["B2"]),
            add("novelty", kind="new_claim", claim_ids=["C1", "C2"], reason="Findings."),
            add("novelty", kind="new_branch", branch_ids=["B3"], reason="Child branch."),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1"),
        )
        self.apply(
            run,
            start("investigation", ["B1", "B2", "B3"]),
            add("assignment", id="A1", branch_ids=["B3"], question="Child question."),
            fact("C3", "B2", "S1", statement="RUNNING PARENT FINDING."),
            fact("C4", "B3", "S1", statement="Child finding."),
            add("branch", id="B4", title="Grandchild", question="Grandchild?", rationale="From C4.", parent_ids=["B3"]),
            add("novelty", kind="new_claim", claim_ids=["C3", "C4"], reason="More findings."),
            add("novelty", kind="new_branch", branch_ids=["B4"], reason="Grandchild branch."),
        )
        result = self.cli("worker-brief", str(run), "--assignment", "A1")
        self.assertEqual([c["id"] for c in result["brief"]["established_claims"]], ["C2"])
        self.assertIn("- Parent finding. [sources: https://example.org/s1]", result["text"])
        for hidden in ("SIBLING", "RUNNING PARENT", "Child finding."):
            self.assertNotIn(hidden, result["text"])
        self.apply(run, update("assignment", "A1", status="interrupted"), complete("P2"))
        self.apply(run, start("investigation", ["B1", "B2", "B3", "B4"]),
                   add("assignment", id="A2", branch_ids=["B4"], question="Grandchild question."))
        result = self.cli("worker-brief", str(run), "--assignment", "A2")
        self.assertEqual([c["id"] for c in result["brief"]["established_claims"]], ["C4"])

    def test_changed_qualifier_and_conclusion_must_link_the_claim_they_change(self) -> None:
        run = self.covered_run()
        self.apply(run, start("investigation", ["B1"]))
        before = (run / "state.json").read_bytes()
        result = self.apply(
            run, fact("C2", "B1", "S1"), add("novelty", kind="changed_qualifier", claim_ids=["C2"], reason="x"), code=1
        )
        self.assertIn("ops[1]: novelty N2: kind changed_qualifier needs claim C2 to link the accepted "
                      "claim it changes: set its refines (or contradicts) to the id of another accepted claim of scope "
                      "S1, from any phase. A paraphrase or vaguer restatement of an existing claim is a duplicate "
                      "(status duplicate with duplicate_of), not a changed qualifier", self.errors(result))
        result = self.apply(run, fact("C2", "B1", "S1", refines=["C2"]), code=1)
        self.assertIn("contradicts, refines, and based_on must not reference the claim itself", self.errors(result))
        result = self.apply(run, fact("C2", "B1", "S1", refines=["C9"]), code=1)
        self.assertIn("claim C2: field refines references unknown claim C9", self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.apply(run, fact("C3", "B1", "S1", status="duplicate", duplicate_of="C1"))
        result = self.apply(
            run,
            fact("C2", "B1", "S1", refines=["C3"]),
            add("novelty", kind="changed_conclusion", claim_ids=["C2"], reason="x"),
            code=1,
        )
        self.assertIn("kind changed_conclusion needs claim C2 to link the accepted claim it changes", self.errors(result))
        self.assertIn("not a changed conclusion", self.errors(result))
        self.apply(
            run,
            fact("C2", "B1", "S1", refines=["C1"], qualifiers=["only below -15 C"]),
            add("novelty", kind="changed_qualifier", claim_ids=["C2"], reason="Holds only below -15 C."),
            fact("C4", "B1", "S1", refines=["C2"]),
            add("novelty", kind="changed_qualifier", claim_ids=["C4"], reason="Same-phase refinement."),
            source("S2"),
            fact("C5", "B1", "S2", contradicts=["C1"]),
            add("novelty", kind="changed_conclusion", claim_ids=["C5"], reason="Field data overturns C1."),
            update("claim", "C1", status="rejected", rejection_reason="Overturned by C5."),
            update("claim", "C3", duplicate_of="C2"),
        )
        claims = {c["id"]: c for c in self.state(run)["claims"]}
        self.assertEqual((claims["C2"]["refines"], claims["C1"]["status"]), (["C1"], "rejected"))
        result = self.apply(
            run,
            fact("C7", "B1", "S2", contradicts=["C1"]),
            add("novelty", kind="changed_conclusion", claim_ids=["C7"], reason="x"),
            code=1,
        )
        self.assertIn("kind changed_conclusion needs claim C7 to link the accepted claim it changes", self.errors(result))
        self.apply(run, complete("P2", duplicates=1))
        self.apply(run, {"op": "change_scope", "reason": "Narrow.", "affected_branch_ids": ["B1"]},
                   start("investigation", ["B1"]))
        result = self.apply(
            run,
            fact("C6", "B1", "S2", refines=["C4"]),
            add("novelty", kind="changed_qualifier", claim_ids=["C6"], reason="x"),
            code=1,
        )
        self.assertIn("another accepted claim of scope S2", self.errors(result))
        self.apply(
            run,
            update("claim", "C4", scope_id="S2"),
            fact("C6", "B1", "S2", refines=["C4"]),
            add("novelty", kind="changed_qualifier", claim_ids=["C6"], reason="Revalidated and narrowed."),
        )
        result = self.apply(run, update("claim", "C4", refines=["C1"]), code=1)
        self.assertIn("fields refines are frozen", self.errors(result))
        self.assertIn("new claim in the running phase whose refines lists C4", self.errors(result))

    def test_completed_phase_claims_are_frozen(self) -> None:
        run = self.covered_run()
        reject = update("claim", "C1", status="rejected", rejection_reason="Refuted by field data.")
        overturn = "claim C1 was accepted in completed phase P1, reject it only while a phase runs"
        self.assertIn(overturn, self.errors(self.apply(run, reject, code=1)))
        self.apply(run, start("challenge", ["B1"], methods=["opposing sources"]))
        result = self.apply(run, update("claim", "C1", statement="Rewritten."), code=1)
        self.assertIn("fields statement are frozen", self.errors(result))
        result = self.apply(run, fact("C2", "B1", "S1", phase_id="P1"), code=1)
        self.assertIn("an accepted claim must belong to the running phase (P2)", self.errors(result))
        before = (run / "state.json").read_bytes()
        self.assertIn(overturn, self.errors(self.apply(run, reject, code=1)))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.apply(run, source("S2"), fact("C2", "B1", "S2", contradicts=["C1"]), reject)
        result = self.apply(run, update("claim", "C1", status="accepted", rejection_reason=None), code=1)
        self.assertIn("cannot become accepted", self.errors(result))
        result = self.apply(run, complete("P2"), code=1)
        self.assertIn("accepted claim C2 has no novelty event in phase P2", self.errors(result))
        self.apply(
            run,
            add("novelty", kind="contradiction", claim_ids=["C2"], reason="Field data refutes C1."),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A2", status="complete"),
            complete("P2"),
        )
        self.assertEqual(self.state(run)["phases"][-1]["novelty_count"], 1)
        self.apply(run, {"op": "change_scope", "reason": "Narrow.", "affected_branch_ids": ["B1"]})
        self.apply(run, update("claim", "C2", status="rejected", rejection_reason="Outside the narrowed scope."))

    def test_status_markdown_renders_report(self) -> None:
        run = self.covered_run()
        result = self.cli("status", str(run), "--format", "markdown")
        markdown = result["markdown"]
        self.assertIn("## Research record", markdown)
        self.assertIn("| 1 P1 | discovery | complete | 1 | 1 | 0 | 1 | 0 | yes | no |", markdown)
        self.assertIn("saturation allowed: no", markdown)
        self.assertIn("- Open branches: none", markdown)
        digest = json.loads((run / "CURRENT").read_bytes())["sha256"]
        self.assertIn(f"- Checkpoint 0 (sha256 {digest}), uncheckpointed changes: yes", markdown)

    def test_status_of_terminal_runs_does_not_report_a_failed_stopping_rule(self) -> None:
        run = self.saturated_run()
        report = self.status(run)
        self.assertEqual((report["saturated"], report["saturation_allowed"], report["reasons"]), (True, True, []))
        markdown = self.cli("status", str(run), "--format", "markdown")["markdown"]
        self.assertIn("- Status: saturated, stop reason: Two quiet phases.\n", markdown)
        self.assertIn("\n- Saturation: reached, run closed\n- Zero-novelty streak: 2\n", markdown)
        self.assertNotIn("saturation allowed", markdown)
        self.assertNotIn("run status is", markdown)
        stopped = self.covered_run()
        self.assertFalse(self.status(stopped)["saturated"])
        for status, reason in (("paused", "User break."), ("incomplete", "Phase limit reached.")):
            if status == "incomplete":
                self.cli("resume", str(stopped), "--reason", "Back.")
            self.cli("finish", str(stopped), "--status", status, "--reason", reason)
            report = self.status(stopped)
            self.assertEqual((report["saturated"], report["saturation_allowed"]), (False, False))
            markdown = self.cli("status", str(stopped), "--format", "markdown")["markdown"]
            self.assertIn(f"- Status: {status}, stop reason: {reason}\n", markdown)
            self.assertIn("- Zero-novelty streak: 0 (the saturation check runs only while researching)\n", markdown)
            self.assertNotIn("saturation allowed", markdown)
            self.assertNotIn("run status is", markdown)
        result = self.cli("resume", str(stopped), "--reason", "Back.")
        self.assertEqual(result["prior_status"], "incomplete")
        self.cli("finish", str(stopped), "--status", "paused", "--reason", "Break.")
        result = self.cli("finish", str(stopped), "--status", "saturated", "--reason", "Quiet.", code=1)
        self.assertIn("cannot finish saturated: run status is paused, saturation is decided only while researching",
                      self.errors(result))

    def test_missing_lock_makes_status_and_resume_report_every_verify_problem(self) -> None:
        run = self.init()
        self.cli("finish", str(run), "--status", "paused", "--reason", "Break.")
        (run / "lock").unlink()
        (run / "state.json").chmod(0o644)
        for command in (("status", str(run)), ("resume", str(run), "--reason", "Back.")):
            message = self.errors(self.cli(*command, code=1))
            self.assertIn(f"{command[0]} refused: verify found problems, the script never repairs or reconstructs a "
                          "run. Report these problems to the user and do not edit or create run files", message)
            self.assertIn("lock is missing or has the wrong file type", message)
            self.assertIn("state.json has mode 644, expected 600", message)
        self.assertFalse((run / "lock").exists())
        self.assertEqual(self.state(run)["status"], "paused")
        message = self.errors(self.apply(run, source("S1"), code=1))
        self.assertIn("Run verify and report its problems, never create or replace run files yourself", message)

    def test_concurrent_writer_is_refused(self) -> None:
        run = self.init()
        descriptor = os.open(run / "lock", os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        result = self.apply(run, source("S1"), code=1)
        self.assertIn("another coordinator is writing this run", self.errors(result))

    def test_schema_and_help_print(self) -> None:
        schema = self.cli("schema")
        self.assertEqual(schema["schema"], "deep-research.v1")
        self.assertEqual(sorted(schema["ops"]), ["add", "change_scope", "complete_phase", "start_phase", "update"])
        self.assertIn("accepted|duplicate|rejected", schema["types"]["claim"]["fields"]["status"])
        self.assertEqual(schema["types"]["novelty"]["required_on_add"], ["kind", "reason"])
        claim = schema["types"]["claim"]
        self.assertEqual((claim["fields"]["refines"], claim["defaults"]["refines"]), ("ids", []))
        self.assertIn("refines or contradicts", schema["novelty_kinds"]["changed_qualifier|changed_conclusion"])
        self.assertIn("created_at", schema["types"]["assignment"]["script_set"])
        self.assertIn("recorded_at", schema["types"]["failure"]["script_set"])
        self.assertEqual(schema["brief"]["browsing_method"], "str")
        self.assertIn("[--format json|text]", schema["commands"]["worker-brief"])
        self.assertNotIn("inbox", json.dumps(schema))
        completed = subprocess.run([sys.executable, str(SCRIPT), "--help"], stdout=subprocess.PIPE, check=True)
        self.assertIn(b"worker-brief", completed.stdout)
        self.assertNotIn(b"inbox", completed.stdout)

    def test_saturation_needs_every_truncated_or_interrupted_assignment_settled(self) -> None:
        run = self.init(branches=1)
        self.apply(
            run,
            start("discovery", ["B1"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Survey B1"),
            add("assignment", id="A2", branch_ids=["B1"], question="Recalls?", status="running"),
            add("assignment", id="A3", branch_ids=["B1"], question="Field data?"),
        )
        self.finish_assignment(run, "A1")
        self.capture(run, "A2", b"cut off", "--truncated")
        self.apply(
            run,
            update("assignment", "A2", status="truncated"),
            update("assignment", "A3", status="interrupted"),
            source("S1", found_by="A1"),
            fact("C1", "B1", "S1"),
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="First finding."),
            update("branch", "B1", status="covered"),
            complete("P1"),
        )
        self.zero_phase(run, "investigation")
        self.zero_phase(run, "challenge")
        report = self.status(run)
        unsettled = ("assignments failed, truncated, or interrupted without a complete retry, continuation, or "
                     "resolved failure: A2, A3")
        self.assertEqual((report["streak"], report["saturation_allowed"], report["reasons"]), (2, False, [unsettled]))
        self.assertEqual([a["id"] for a in report["unresolved_assignments"]], ["A2", "A3"])
        result = self.cli("finish", str(run), "--status", "saturated", "--reason", "Quiet.", code=1)
        self.assertIn(f"cannot finish saturated: {unsettled}", self.errors(result))
        result = self.apply(
            run,
            start("investigation", ["B1"]),
            add("assignment", branch_ids=["B1"], question="Rest of recalls?", continuation_of="A2"),
            add("assignment", branch_ids=["B1"], question="Field data?", continuation_of="A3"),
        )
        for assignment in result["ids"][1:]:
            self.finish_assignment(run, assignment)
        self.apply(run, complete(result["ids"][0]))
        self.zero_phase(run, "challenge")
        report = self.status(run)
        self.assertEqual((report["unresolved_assignments"], report["saturation_allowed"]), ([], True))

    def test_assignment_brief_fields_are_fixed_after_dispatch(self) -> None:
        run = self.init()
        self.apply(
            run,
            start("discovery", ["B1", "B2"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q"),
            add("assignment", id="A2", branch_ids=["B2"], question="Q"),
        )
        self.apply(run, update("assignment", "A1", question="Sharper question.", branch_ids=["B1", "B2"]))
        result = self.apply(run, update("assignment", "A2", status="running", question="Changed at dispatch."), code=1)
        self.assertIn("ops[0] (update assignment A2): question and status cannot change in the same update. Change "
                      "question in a separate update op placed before the status change, while A2 is still planned, "
                      "then set status running. Run worker-brief only after the change", self.errors(result))
        self.apply(run, update("assignment", "A2", question="Changed at dispatch."),
                   update("assignment", "A2", status="running"))
        self.assertEqual(self.state(run)["assignments"][1]["question"], "Changed at dispatch.")
        self.apply(run, update("assignment", "A1", status="running"))
        before = (run / "state.json").read_bytes()
        for fields in (
            {"branch_ids": ["B1"]},
            {"question": "Other."},
            {"queries": ["new query"]},
            {"budget": {"searches": 1}},
            {"worker": "coordinator", "status": "complete"},
        ):
            result = self.apply(run, update("assignment", "A1", **fields), code=1)
            self.assertIn("cannot change once the assignment leaves planned", self.errors(result), fields)
            self.assertEqual((run / "state.json").read_bytes(), before)
        result = self.apply(run, update("assignment", "A1", status="complete"), code=1)
        self.assertIn("run capture first", self.errors(result))
        self.apply(run, update("assignment", "A1", notes="Dispatched in wave 1."))

    def test_branches_enter_or_leave_scope_only_inside_a_phase_or_through_change_scope(self) -> None:
        run = self.covered_run()
        off_topic = {
            "title": "Pricing", "question": "Q?", "rationale": "Proposed by A1.", "status": "out_of_scope",
            "out_of_scope_reason": "Outside the question.",
        }
        result = self.apply(run, add("branch", id="B9", title="Costs", question="Q?", rationale="R."), code=1)
        self.assertIn("no phase is running, add a newly found branch while a phase runs", self.errors(result))
        self.apply(run, add("branch", id="B8", **off_topic))
        result = self.apply(run, update("branch", "B8", status="queued", out_of_scope_reason=None), code=1)
        self.assertIn("branch B8 can move into or out of out_of_scope only through change_scope", self.errors(result))
        self.apply(run, start("challenge", ["B1"], methods=["opposing sources"]))
        before = (run / "state.json").read_bytes()
        result = self.apply(
            run, add("branch", id="B9", title="Costs", question="Q?", rationale="R.", discovered_in=None), code=1
        )
        self.assertIn("discovered_in must be the running phase (P2)", self.errors(result))
        result = self.apply(run, update("branch", "B1", status="out_of_scope", out_of_scope_reason="Dropped."), code=1)
        self.assertIn("branch B1 can move into or out of out_of_scope only through change_scope", self.errors(result))
        result = self.apply(
            run, add("branch", id="B9", title="Costs", question="Q?", rationale="R.", status="covered"), code=1
        )
        self.assertIn("status covered needs a complete assignment", self.errors(result))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.apply(
            run,
            add("branch", id="B9", **off_topic),
            update("branch", "B9", status="queued", out_of_scope_reason=None),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A2", status="complete"),
        )
        result = self.apply(run, complete("P2"), code=1)
        self.assertIn("branch B9 was discovered in phase P2 but has no new_branch novelty event", self.errors(result))
        self.apply(run, add("novelty", kind="new_branch", branch_ids=["B9"], reason="Pricing matters."), complete("P2"))
        phase = self.state(run)["phases"][-1]
        self.assertEqual((phase["novelty_count"], phase["zero_novelty"]), (1, False))
        self.apply(
            run,
            {"op": "change_scope", "reason": "User added pricing.", "affected_branch_ids": ["B8"]},
            add("branch", id="B10", title="Tariffs", question="Q?", rationale="User asked."),
        )
        statuses = {b["id"]: (b["status"], b["scope_id"]) for b in self.state(run)["branches"]}
        self.assertEqual((statuses["B8"], statuses["B10"]), (("queued", "S2"), ("queued", "S2")))

    def test_subagent_assignment_needs_a_full_reply_to_complete(self) -> None:
        run = self.init()
        self.apply(
            run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q", status="running")
        )
        self.capture(run, "A1", b"cut off", "--truncated")
        result = self.apply(run, update("assignment", "A1", status="complete"), code=1)
        self.assertIn("every captured reply is truncated, set status truncated and add a continuation_of",
                      self.errors(result))
        self.capture(run, "A1", b"the full reply")
        self.apply(run, update("assignment", "A1", status="complete"))

    def test_failure_resolution_must_follow_the_failure_and_share_a_branch(self) -> None:
        run = self.init(branches=2)
        self.apply(
            run,
            start("discovery", ["B1", "B2"]),
            add("assignment", id="A1", branch_ids=["B1", "B2"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A1", status="complete"),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1"),
        )
        self.apply(
            run,
            start("investigation", ["B1", "B2"]),
            add("assignment", id="A2", branch_ids=["B1"], question="Q", status="running"),
            add("assignment", id="A3", branch_ids=["B2"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A3", status="complete"),
        )
        time.sleep(1.1)
        for phase_id in ("P1", None):
            result = self.apply(
                run, add("failure", phase_id=phase_id, assignment_id="A2", kind="tool", detail="Crashed."), code=1
            )
            self.assertIn("phase_id must be the running phase (P2)", self.errors(result))
        self.apply(
            run,
            add("failure", id="F1", assignment_id="A2", kind="tool", detail="Crashed."),
            update("assignment", "A2", status="failed"),
            add("failure", id="F2", kind="coverage", detail="No field data for B2."),
        )
        before = (run / "state.json").read_bytes()
        for failure_id, resolver in (("F1", "A1"), ("F1", "A3"), ("F2", "A1")):
            result = self.apply(
                run, update("failure", failure_id, resolved=True, resolution="Covered.", resolved_by=resolver), code=1
            )
            self.assertIn(f"failure {failure_id}: resolved_by {resolver} must belong to the failure's phase or a "
                          "later one and share a branch", self.errors(result))
            self.assertEqual((run / "state.json").read_bytes(), before)
        state = self.state(run)
        created = {a["id"]: a["created_at"] for a in state["assignments"]}
        recorded = {f["id"]: f["recorded_at"] for f in state["failures"]}
        self.assertLess(created["A3"], recorded["F2"])
        result = self.apply(run, update("failure", "F2", resolved=True, resolution="Covered.", resolved_by="A3"), code=1)
        message = self.errors(result)
        self.assertIn(f"failure F2: resolved_by A3 was added at {created['A3']}, before this failure was recorded at "
                      f"{recorded['F2']}, so it did no recovery work. Add a recovery assignment now (a retry_of, a "
                      "continuation_of, or a credible alternative on the same branch) and resolve with it once it is "
                      "complete", message)
        self.assertNotIn("must belong to the failure's phase", message)
        for op, expected in (
            (update("assignment", "A3", created_at="2099-01-01T00:00:00Z"), "field created_at cannot be updated"),
            (update("failure", "F2", recorded_at="2000-01-01T00:00:00Z"), "field recorded_at cannot be updated"),
            (add("failure", kind="tool", detail="x", recorded_at="2000-01-01T00:00:00Z"),
             "field recorded_at is set by the script, remove it"),
            (add("assignment", branch_ids=["B1"], question="Q", created_at="2099-01-01T00:00:00Z"),
             "field created_at is set by the script, remove it"),
        ):
            self.assertIn(expected, self.errors(self.apply(run, op, code=1)))
        self.assertEqual((run / "state.json").read_bytes(), before)
        self.apply(
            run,
            add("assignment", id="A4", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A4", status="complete"),
            add("assignment", id="A5", branch_ids=["B2"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A5", status="complete"),
            update("failure", "F1", resolved=True, resolution="A4 covered B1.", resolved_by="A4"),
            update("failure", "F2", resolved=True, resolution="A5 found field data.", resolved_by="A5"),
        )
        self.assertGreaterEqual(self.state(run)["assignments"][-1]["created_at"], recorded["F2"])

    def test_unexpected_inbox_is_reported_like_any_other_entry(self) -> None:
        run = self.init()
        self.apply(run, start("discovery", ["B1"]), add("assignment", id="A1", branch_ids=["B1"], question="Q"))
        inbox = run / "inbox"
        inbox.mkdir()
        inbox.chmod(0o700)
        (inbox / "reply.txt").write_bytes(b"reply")
        (inbox / "reply.txt").chmod(0o600)
        (run / "notes.txt").write_bytes(b"scratch")
        (run / "notes.txt").chmod(0o600)
        verified = self.cli("verify", str(run))
        self.assertEqual(verified["problems"], [])
        self.assertEqual(sorted(verified["warnings"]),
                         ["inbox/ is not part of the run layout", "notes.txt is not part of the run layout"])
        result = self.cli("capture", str(run), "--assignment", "A1", "--file", str(inbox / "reply.txt"))
        self.assertEqual(result["return_id"], "R1")
        self.assertTrue((inbox / "reply.txt").exists())
        (inbox / "reply.txt").chmod(0o644)
        (inbox / "link.txt").symlink_to(inbox / "reply.txt")
        problems = self.cli("verify", str(run), code=1)["problems"]
        self.assertIn("inbox/reply.txt has mode 644, expected 600", problems)
        self.assertIn("inbox/link.txt is a symlink, symlinks are never allowed in a run directory", problems)
        self.cli("finish", str(run), "--status", "paused", "--reason", "Break.")
        message = self.errors(self.cli("resume", str(run), "--reason", "Back.", code=1))
        self.assertIn("resume refused: verify found problems", message)
        self.assertIn("inbox/reply.txt has mode 644, expected 600", message)

    def test_interrupted_checkpoint_publish_is_adopted(self) -> None:
        run = self.init()
        self.apply(run, source("S1"))
        prior = json.loads((run / "CURRENT").read_bytes())["sha256"]
        payload = canonical({
            "sequence": 1, "created_at": "2026-01-01T00:00:00Z", "prior_sha256": prior, "note": None,
            "state": self.state(run),
        })
        orphan = run / "checkpoints" / "0001.json"
        orphan.write_bytes(payload)
        orphan.chmod(0o600)
        verified = self.cli("verify", str(run))
        self.assertEqual(verified["problems"], [])
        self.assertIn("checkpoint 0001 was published but CURRENT still names 0000", " ".join(verified["warnings"]))
        result = self.cli("checkpoint", str(run))
        self.assertEqual((result["unchanged"], result["sequence"]), (True, 1))
        current = json.loads((run / "CURRENT").read_bytes())
        self.assertEqual(current, {"sequence": 1, "sha256": hashlib.sha256(payload).hexdigest()})
        verified = self.cli("verify", str(run))
        self.assertEqual((verified["problems"], verified["warnings"]), ([], []))
        stray = run / "checkpoints" / "0003.json"
        stray.write_bytes(b"{}\n")
        stray.chmod(0o600)
        self.apply(run, source("S2"))
        message = self.errors(self.cli("checkpoint", str(run), code=1))
        self.assertIn("refusing to publish a checkpoint", message)
        self.assertIn("checkpoint 0003 exists beyond CURRENT sequence 1", message)

    @unittest.skipIf(os.getuid() == 0, "root ignores directory permissions")
    def test_failed_checkpoint_publish_leaves_state_and_chain_unchanged(self) -> None:
        run = self.init()
        self.apply(run, source("S1"))
        run.chmod(0o500)
        self.addCleanup(run.chmod, 0o700)
        self.assertIn("PermissionError", self.errors(self.cli("checkpoint", str(run), code=1)))
        run.chmod(0o700)
        self.assertEqual(sorted(path.name for path in (run / "checkpoints").iterdir()), ["0000.json"])
        self.assertEqual(self.cli("checkpoint", str(run))["sequence"], 1)
        before = (run / "state.json").read_bytes()
        (run / "checkpoints").chmod(0o500)
        self.addCleanup((run / "checkpoints").chmod, 0o700)
        self.cli("finish", str(run), "--status", "paused", "--reason", "Break.", code=1)
        (run / "checkpoints").chmod(0o700)
        self.assertEqual((run / "state.json").read_bytes(), before)
        result = self.cli("finish", str(run), "--status", "paused", "--reason", "Break.")
        self.assertEqual((result["status"], result["sequence"]), ("paused", 2))
        verified = self.cli("verify", str(run))
        self.assertEqual((verified["problems"], verified["warnings"], verified["uncheckpointed_changes"]),
                         ([], [], False))

    def test_checkpoint_refuses_hand_edited_state(self) -> None:
        run = self.covered_run()
        self.cli("checkpoint", str(run))
        original = (run / "state.json").read_bytes()
        edited = json.loads(original)
        edited.update(status="saturated", stop_reason="Done.")
        (run / "state.json").write_bytes(canonical(edited))
        verified = self.cli("verify", str(run), code=1)
        self.assertTrue(any("status saturated needs closure" in problem for problem in verified["problems"]))
        self.assertIn("status saturated needs closure", self.errors(self.cli("checkpoint", str(run), code=1)))
        edited["status"] = "paused"
        (run / "state.json").write_bytes(canonical(edited))
        verified = self.cli("verify", str(run))
        self.assertIn("state.json status paused differs from the latest checkpoint", " ".join(verified["warnings"]))
        self.assertIn("only finish and resume change status", self.errors(self.cli("checkpoint", str(run), code=1)))
        edited = json.loads(original)
        edited["phases"][0]["limitations"] = ["Rewritten later."]
        (run / "state.json").write_bytes(canonical(edited))
        problem = "completed phase P1 differs from the last checkpoint, completed phases never change"
        self.assertIn(f"state.json: {problem}", self.cli("verify", str(run), code=1)["problems"])
        self.assertIn(problem, self.errors(self.cli("checkpoint", str(run), code=1)))
        self.assertEqual(json.loads((run / "CURRENT").read_bytes())["sequence"], 1)

    def test_fifo_in_place_of_a_run_file_is_reported_not_waited_on(self) -> None:
        run = self.init()
        (run / "state.json").unlink()
        os.mkfifo(run / "state.json", 0o600)
        self.assertIn("state.json is not a regular file", self.errors(self.cli("status", str(run), code=1)))
        verified = self.cli("verify", str(run), code=1)
        self.assertIn("state.json is missing or has the wrong file type", verified["problems"])
        other = self.init()
        (other / "lock").unlink()
        os.mkfifo(other / "lock", 0o600)
        self.assertIn("lock is not a regular file", self.errors(self.cli("status", str(other), code=1)))

    def test_default_root_ignores_relative_xdg_and_explains_repository_refusal(self) -> None:
        home = self.base / "home"
        repository = self.base / "repository"
        (repository / ".git").mkdir(parents=True)
        self.env = dict(self.env, HOME=str(home), XDG_STATE_HOME=str(repository / "state"))
        result = self.cli("init", stdin={"question": "Q?"}, code=1)
        self.assertIn("this is the default root, ask the user for a root outside any repository and pass --root",
                      self.errors(result))
        self.assertNotIn("omit --root", self.errors(result))
        self.env["XDG_STATE_HOME"] = "relative-state"
        run = Path(self.cli("init", stdin={"question": "Q?"})["run_dir"])
        self.assertEqual(run.parent, (home / ".local" / "state" / "expskill" / "deep-research").resolve())

    def test_complete_phase_duplicates_defaults_to_recorded_duplicates(self) -> None:
        run = self.init(branches=1)
        self.apply(
            run,
            start("discovery", ["B1"]),
            add("assignment", id="A1", branch_ids=["B1"], question="Q", worker="coordinator", status="running"),
            update("assignment", "A1", status="complete"),
            source("S1"),
            fact("C1", "B1", "S1"),
            fact("C2", "B1", "S1", status="duplicate", duplicate_of="C1"),
            add("novelty", kind="new_claim", claim_ids=["C1"], reason="First finding."),
            update("branch", "B1", status="covered"),
        )
        result = self.apply(run, complete("P1", duplicates=0), code=1)
        self.assertIn("duplicates 0 is lower than the 1 duplicate claims of phase P1", self.errors(result))
        for value, suggestion in ((["C2", "C3"], 2), ("1", 1), (None, 1)):
            result = self.apply(run, complete("P1", duplicates=value), code=1)
            self.assertIn("ops[0] (complete_phase P1): duplicates must be one integer count of the paraphrases and "
                          "agreeing sources merged in phase P1, not a list of claim ids or any other value. Pass a "
                          f"number such as {suggestion} (at least 1, the duplicate claims recorded) or omit duplicates",
                          self.errors(result))
        self.apply(run, {"op": "complete_phase", "id": "P1"})
        self.assertEqual(self.state(run)["phases"][0]["duplicates"], 1)

    def run_until_first_quiet_phase(self) -> Path:
        run = self.init(branches=2, inclusions=["air-source heat pumps"], source_rules=["prefer measured data"])
        result = self.apply(
            run,
            start("discovery", ["B1", "B2"], methods=["broad web search"]),
            add("assignment", branch_ids=["B1"], question="Efficiency below -15 C?",
                queries=["heat pump COP -15C field study"], budget={"searches": 8, "opens": 5, "output_words": 800}),
            add("assignment", branch_ids=["B2"], question="Defrost losses?"),
        )
        self.assertEqual(result["ids"], ["P1", "A1", "A2"])
        self.assertIn("Efficiency below -15 C?", self.cli("worker-brief", str(run), "--assignment", "A1")["text"])
        self.apply(run, update("assignment", "A1", status="running"), update("assignment", "A2", status="running"))
        self.assertEqual(self.capture(run, "A1")["return_id"], "R1")
        self.assertEqual(self.capture(run, "A2")["return_id"], "R2")
        self.apply(
            run,
            update("assignment", "A1", status="complete"),
            update("assignment", "A2", status="complete"),
            source("SRC1", found_by="A1"),
            source("SRC2", found_by="A2"),
            source("SRC3", access="inaccessible", found_by="A2"),
            fact("C1", "B1", "SRC1", return_ids=["R1"]),
            fact("C2", "B2", "SRC2", return_ids=["R2"]),
            add("claim", id="C3", statement="Defrost drives the low-temperature drop.", epistemic="inference",
                branch_ids=["B1", "B2"], status="accepted", based_on=["C1", "C2"]),
            add("branch", id="B3", title="Backup heat", question="How often does backup heat run?",
                rationale="SRC2 mentions resistance backup.", parent_ids=["B2"]),
            add("failure", id="F1", kind="source", assignment_id="A2", detail="SRC3 paywalled."),
            add("novelty", kind="new_claim", claim_ids=["C1", "C2"], reason="First measured findings."),
            add("novelty", kind="new_mechanism", claim_ids=["C3"], reason="Defrost mechanism."),
            add("novelty", kind="new_branch", branch_ids=["B3"], reason="Backup heat question."),
            update("branch", "B1", status="covered"),
            update("branch", "B2", status="covered"),
            complete("P1", limitations=["English sources only"], next_questions=["Backup heat share?"]),
        )
        discovery = self.state(run)["phases"][0]
        self.assertEqual((discovery["novelty_count"], discovery["eligible"]), (3, False))
        self.assertIn("branch B3 is queued at completion", discovery["blockers"])
        self.assertIn("failure F1 of this phase is unresolved", discovery["blockers"])
        result = self.apply(
            run,
            start("investigation", ["B1", "B2", "B3"]),
            add("assignment", branch_ids=["B1", "B2"], question="Confirm findings."),
            add("assignment", branch_ids=["B3"], question="Backup heat runtime?"),
        )
        self.assertEqual(result["ids"], ["P2", "A3", "A4"])
        self.assertEqual(self.state(run)["branches"][2]["status"], "active")
        for assignment in ("A3", "A4"):
            self.finish_assignment(run, assignment)
        self.apply(
            run,
            source("SRC4", found_by="A4"),
            fact("C4", "B3", "SRC4", return_ids=["R4"]),
            fact("C5", "B1", "SRC4", status="duplicate", duplicate_of="C1"),
            update("failure", "F1", resolved=True, resolution="A3 confirmed the SRC3 data from SRC2.",
                   resolved_by="A3"),
            add("novelty", kind="new_claim", claim_ids=["C4"], reason="Backup heat share measured."),
            update("branch", "B3", status="covered"),
            complete("P2", duplicates=1),
        )
        investigation = self.state(run)["phases"][1]
        self.assertEqual(
            (investigation["eligible"], investigation["novelty_count"], investigation["zero_novelty"]), (True, 1, False)
        )
        result = self.apply(
            run,
            start("investigation", ["B1", "B2", "B3"]),
            add("assignment", branch_ids=["B1", "B2", "B3"], question="Look for anything new."),
        )
        self.finish_assignment(run, result["ids"][1])
        self.apply(
            run,
            source("SRC5", found_by="A5"),
            fact("C6", "B3", "SRC5", status="duplicate", duplicate_of="C4"),
            complete("P3", duplicates=1),
        )
        quiet = self.state(run)["phases"][2]
        self.assertEqual((quiet["eligible"], quiet["novelty_count"], quiet["zero_novelty"]), (True, 0, True))
        return run

    def test_full_run_reaches_saturation(self) -> None:
        run = self.run_until_first_quiet_phase()
        self.assertEqual(self.status(run)["streak"], 1)
        result = self.apply(
            run,
            start("challenge", ["B1", "B2", "B3"], methods=["searched for counterevidence"]),
            add("assignment", branch_ids=["B1", "B2", "B3"], question="Try to refute each accepted claim."),
        )
        self.finish_assignment(run, result["ids"][1])
        self.apply(
            run,
            source("SRC6", access="unverified", found_by="A6"),
            fact("C7", "B1", "SRC6", status="rejected", rejection_reason="Vendor claim without data."),
            complete("P4", methods=["searched for counterevidence", "checked primary data"]),
        )
        report = self.status(run)
        self.assertEqual((report["streak"], report["saturation_allowed"], report["reasons"]), (2, True, []))
        self.assertEqual(report["counts"]["claims_accepted"], 4)
        result = self.cli("finish", str(run), "--status", "saturated", "--reason", "Two quiet phases ending in challenge.")
        self.assertEqual((result["status"], result["summary"]["status"]), ("saturated", "saturated"))
        verified = self.cli("verify", str(run))
        self.assertEqual((verified["problems"], verified["warnings"]), ([], []))
        self.assertEqual(verified["sequence"], result["sequence"])
        self.assertFalse(verified["uncheckpointed_changes"])
        self.assertTrue(self.cli("checkpoint", str(run))["unchanged"])

    def test_full_run_cannot_saturate_after_one_quiet_phase(self) -> None:
        run = self.run_until_first_quiet_phase()
        result = self.cli("finish", str(run), "--status", "saturated", "--reason", "Looks done.", code=1)
        self.assertIn("zero-novelty streak is 1", self.errors(result))
        self.assertIn("the most recent completed phase P3 is investigation, it must be a challenge phase",
                      result["reasons"])
        self.assertEqual(self.state(run)["status"], "researching")


if __name__ == "__main__":
    unittest.main()
