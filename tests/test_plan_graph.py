from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "plugins" / "expskill" / "scripts" / "plan_graph.py"


def _skip_exception(error: BaseException) -> bool:
    return isinstance(error, unittest.SkipTest) or (
        error.__class__.__name__ == "Skipped"
        and error.__class__.__module__.startswith("_pytest.")
    )


class _NoSkipModule:
    """Turn candidate-requested skips into failures at the evaluation boundary."""

    def __init__(self, module: object) -> None:
        self._module = module

    def __getattr__(self, name: str) -> object:
        try:
            value = getattr(self._module, name)
        except BaseException as error:
            if _skip_exception(error):
                raise AssertionError(
                    f"candidate helper attempted to skip required attribute evidence in {name}"
                ) from error
            raise
        if not callable(value) or isinstance(value, type):
            return value

        def no_skip(*args: object, **kwargs: object) -> object:
            try:
                return value(*args, **kwargs)
            except BaseException as error:
                if _skip_exception(error):
                    raise AssertionError(
                        f"candidate helper attempted to skip required runtime evidence in {name}"
                    ) from error
                raise

        return no_skip


def load_helper() -> object:
    if not HELPER_PATH.is_file():
        raise AssertionError(f"missing route-neutral plan graph helper: {HELPER_PATH}")
    specification = importlib.util.spec_from_file_location("expskill_plan_graph", HELPER_PATH)
    if specification is None or specification.loader is None:
        raise ImportError(f"cannot load plan graph helper: {HELPER_PATH}")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    try:
        specification.loader.exec_module(module)
    except BaseException as error:
        if _skip_exception(error):
            raise AssertionError(
                "candidate helper attempted to skip required import-time evidence"
            ) from error
        raise
    return _NoSkipModule(module)


def minimal_graph() -> dict[str, object]:
    return {
        "schema_version": "plan-graph.v1",
        "outcomes": {
            "O1": {
                "kind": "outcome",
                "result": "Malformed configuration is rejected before startup",
            },
            "C1": {
                "kind": "constraint",
                "result": "Valid configuration behavior remains compatible",
            },
        },
        "evidence": {
            "E1": {
                "kind": "repository",
                "fact": "Configuration enters through src/config.py",
                "source": "src/config.py",
                "fresh": True,
                "supports": ["T1"],
            }
        },
        "decisions": {},
        "work": {
            "T1": {
                "kind": "slice",
                "result": "Validate at the existing configuration boundary",
                "covers": ["O1", "C1"],
                "requires": [],
                "based_on": ["E1"],
                "decisions": [],
                "proof": ["P1"],
                "repository_boundary": ["src/config.py", "tests/test_config.py"],
                "owner": "target",
                "concurrency": "serial",
            }
        },
        "proof": {
            "P1": {
                "claim": "Invalid configuration fails and valid configuration loads",
                "covers": ["O1", "C1"],
                "required_by": ["T1"],
                "planned_method": {
                    "surface": "configuration tests",
                    "positive": "Valid configuration loads",
                    "negative": "Malformed configuration fails before startup",
                },
                "evidence": [],
            }
        },
        "git": {
            "target": {
                "branch": "feature/config-validation",
                "protected": False,
                "reproducible": True,
                "dirty_dependency": False,
            },
            "lanes": {},
            "joins": {},
            "delivery": {"state": "planning"},
        },
        "projections": {
            "U1": {
                "covers": ["T1", "P1"],
                "version": 1,
                "presented": True,
                "confirmed": True,
                "stale": False,
            }
        },
        "invalidations": [],
        "unresolved": [],
    }


def rich_graph() -> dict[str, object]:
    graph = minimal_graph()
    graph["evidence"]["E1"]["supports"] = ["D1", "T1"]
    graph["evidence"]["E2"] = {
        "kind": "external",
        "fact": "The selected parser reports structured validation errors",
        "source": "https://example.invalid/parser-spec",
        "version": "1",
        "fresh": True,
        "limitations": ["Does not describe this repository's startup path"],
        "supports": ["D1"],
    }
    graph["decisions"]["D1"] = {
        "question": "Where should validation errors be normalized?",
        "choice": "At the existing configuration boundary",
        "alternatives": [
            {"id": "A", "status": "selected", "reason": "Preserves callers"},
            {"id": "B", "status": "rejected", "reason": "Duplicates validation in consumers"},
        ],
        "based_on": ["E1", "E2"],
        "material": True,
        "version": 1,
        "confirmed_version": 1,
        "stale": False,
        "invalidates": ["T1", "T2", "T3", "J1", "P1", "P2", "P3", "P4"],
    }
    graph["work"] = {
        "T1": {
            "kind": "slice",
            "result": "Stabilize the validation contract",
            "covers": ["O1", "C1"],
            "requires": [],
            "based_on": ["E1", "E2"],
            "decisions": ["D1"],
            "proof": ["P1"],
            "repository_boundary": ["src/config.py", "tests/test_config.py"],
            "owner": "target",
            "concurrency": "serial",
        },
        "T2": {
            "kind": "task",
            "result": "Implement validation behavior",
            "covers": ["O1"],
            "requires": ["T1"],
            "based_on": ["E1"],
            "decisions": ["D1"],
            "proof": ["P2"],
            "repository_boundary": ["src/config.py"],
            "owner": "lane-validation",
            "concurrency": "parallel-safe",
            "parallel_basis": {
                "stable_inputs": True,
                "ownership_disjoint": True,
                "shared_state_ordering": False,
                "independent_proof": True,
                "joins_at": "J1",
            },
        },
        "T3": {
            "kind": "task",
            "result": "Expand compatibility fixtures",
            "covers": ["C1"],
            "requires": ["T1"],
            "based_on": ["E1"],
            "decisions": ["D1"],
            "proof": ["P3"],
            "repository_boundary": ["tests/test_config.py"],
            "owner": "lane-fixtures",
            "concurrency": "parallel-safe",
            "parallel_basis": {
                "stable_inputs": True,
                "ownership_disjoint": True,
                "shared_state_ordering": False,
                "independent_proof": True,
                "joins_at": "J1",
            },
        },
        "J1": {
            "kind": "join",
            "result": "Prove integrated startup behavior",
            "covers": ["O1", "C1"],
            "requires": ["T2", "T3"],
            "based_on": ["E1"],
            "decisions": ["D1"],
            "proof": ["P4"],
            "repository_boundary": ["src/config.py", "tests/test_config.py"],
            "owner": "target",
            "concurrency": "serial",
        },
    }
    graph["proof"] = {
        "P1": {
            "claim": "The validation contract is unambiguous",
            "covers": ["O1", "C1"],
            "required_by": ["T1"],
            "planned_method": {
                "surface": "contract tests",
                "positive": "Valid configuration retains its values",
                "negative": "Malformed fields report structured errors",
            },
            "evidence": [],
        },
        "P2": {
            "claim": "Malformed configuration fails before startup",
            "covers": ["O1"],
            "required_by": ["T2"],
            "planned_method": {
                "surface": "configuration unit tests",
                "positive": "Valid configuration loads",
                "negative": "Invalid configuration does not start the application",
            },
            "evidence": [],
        },
        "P3": {
            "claim": "Existing configurations remain compatible",
            "covers": ["C1"],
            "required_by": ["T3"],
            "planned_method": {
                "surface": "compatibility fixtures",
                "positive": "Existing valid fixtures load unchanged",
                "negative": "Invalid legacy aliases are not silently accepted",
            },
            "evidence": [],
        },
        "P4": {
            "claim": "The integrated startup path enforces the contract",
            "covers": ["O1", "C1"],
            "required_by": ["J1"],
            "planned_method": {
                "surface": "startup integration tests",
                "positive": "The application starts with valid configuration",
                "negative": "The application never starts with malformed configuration",
            },
            "evidence": [],
        },
    }
    baseline = "__HELPER_INJECTS_BASELINE__"
    graph["git"]["lanes"] = {
        "L1": {
            "work": ["T2"],
            "base": baseline,
            "owner": "lane-validation",
            "integrates_at": "J1",
        },
        "L2": {
            "work": ["T3"],
            "base": baseline,
            "owner": "lane-fixtures",
            "integrates_at": "J1",
        },
    }
    graph["git"]["joins"] = {"J1": {"lanes": ["L1", "L2"], "proof": ["P4"]}}
    graph["projections"] = {
        "U1": {
            "covers": ["D1", "T1", "P1"],
            "version": 1,
            "decision_versions": {"D1": 1},
            "presented": True,
            "confirmed": True,
            "stale": False,
        },
        "U2": {
            "covers": ["T2", "T3", "J1", "P2", "P3", "P4"],
            "version": 1,
            "decision_versions": {"D1": 1},
            "presented": True,
            "confirmed": True,
            "stale": False,
        },
    }
    graph["invalidations"] = [
        {"source": "E1", "targets": ["D1"]},
        {"source": "D1", "targets": ["T1", "T2", "T3", "J1", "P1", "P2", "P3", "P4"]},
    ]
    return graph


def cloned_graph(graph: dict[str, object]) -> dict[str, object]:
    """Return a plain-data clone without sharing nested mutation state."""

    return json.loads(json.dumps(graph))


@unittest.skipUnless(HELPER_PATH.is_file(), "candidate plan graph helper is not implemented yet")
class PlanGraphRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repo = self.root / "product"
        self.repo.mkdir()
        self.state_home = self.root / "state"
        self._git("init", "-b", "feature/config-validation")
        self._git("config", "user.name", "Plan Graph Tests")
        self._git("config", "user.email", "plan-graph@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "config.py").write_text("def load():\n    return {}\n", encoding="utf-8")
        (self.repo / "tests" / "test_config.py").write_text("def test_load():\n    pass\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "initial")
        self.helper = load_helper()

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            self.fail(f"git failed: {' '.join(arguments)}\n{result.stdout}\n{result.stderr}")
        return result.stdout.strip()

    def _prepare_graph(self, graph: dict[str, object]) -> dict[str, object]:
        prepared = cloned_graph(graph)
        baseline = self._git("rev-parse", "HEAD")
        for lane in prepared.get("git", {}).get("lanes", {}).values():
            if lane.get("base") == "__HELPER_INJECTS_BASELINE__":
                lane["base"] = baseline
        return prepared

    def _initialize(
        self,
        graph: dict[str, object] | None = None,
        *,
        state_home: Path | None = None,
        repo: Path | None = None,
    ) -> object:
        return self.helper.initialize_workflow(
            repo or self.repo,
            "feature/config-validation",
            self._prepare_graph(graph or minimal_graph()),
            state_home or self.state_home,
        )

    def _load(self, *, state_home: Path | None = None) -> dict[str, object]:
        return self.helper.load_workflow(
            self.repo,
            "feature/config-validation",
            state_home or self.state_home,
        )

    def _typed_update(
        self,
        receipt: object,
        graph: dict[str, object],
        operation: str,
        path: list[str],
        value: dict[str, object],
        *,
        state_home: Path | None = None,
    ) -> object:
        version_field = (
            "version"
            if operation
            in {"reconfirm-decision", "regenerate-projection", "reconfirm-projection"}
            else "record_version"
        )
        version = value[version_field]
        operation_receipt = self.helper.issue_operation_receipt(
            operation=operation,
            workflow_id=receipt.workflow_id,
            prior_graph_revision=graph["graph_revision"],
            target=path,
            record_version=version,
            value=value,
        )
        return self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            graph["graph_revision"],
            [
                {
                    "op": operation,
                    "path": path,
                    "value": value,
                    "prior_graph_revision": graph["graph_revision"],
                    "record_version": version,
                    "receipt": operation_receipt,
                }
            ],
            state_home or self.state_home,
        )

    def _register_provenance(
        self,
        *,
        workflow_id: str,
        revision: int,
        role: str,
        salt: str,
        source: str,
        state_home: Path | None = None,
    ) -> dict[str, object]:
        value = self.helper.issue_provenance_receipt(
            workflow_id=workflow_id,
            graph_revision=revision,
            role=role,
            session_id=f"session-{salt}",
            raw_evidence_digest=hashlib.sha256(salt.encode()).hexdigest(),
            source=source,
        )
        return self.helper.register_provenance_receipt(
            value, state_home or self.state_home
        )

    def _assert_not_ready(
        self,
        name: str,
        graph: dict[str, object],
        expected_states: set[str],
    ) -> None:
        case_home = self.root / "semantic-cases" / name
        receipt = self._initialize(graph, state_home=case_home)
        loaded = self._load(state_home=case_home)
        state = self.helper.derive_plan_state(loaded)
        self.assertIn(state, expected_states)
        self.assertEqual(receipt.state, state)
        self.helper.discard_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            receipt.revision,
            explicit=True,
            state_home=case_home,
        )

    def _assert_structurally_rejected(self, name: str, graph: dict[str, object]) -> None:
        case_home = self.root / "structural-cases" / name
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize(graph, state_home=case_home)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discover_workflow(
                self.repo,
                "feature/config-validation",
                case_home,
            )

    def test_initialize_writes_private_strict_yaml_outside_repository_and_loads_it(self) -> None:
        receipt = self._initialize()
        self.assertEqual(receipt.revision, 1)
        self.assertTrue(receipt.workflow_id)
        self.assertTrue(receipt.path.is_file())
        self.assertFalse(receipt.path.is_symlink())
        self.assertFalse(receipt.path.is_relative_to(self.repo))
        self.assertEqual(stat.S_IMODE(receipt.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(receipt.path.stat().st_mode), 0o600)
        raw = receipt.path.read_text(encoding="utf-8")
        self.assertNotIn("!!", raw)
        self.assertEqual(json.loads(raw)["graph_revision"], 1)
        loaded = self.helper.load_workflow(
            self.repo, "feature/config-validation", self.state_home
        )
        self.assertEqual(loaded["workflow_id"], receipt.workflow_id)
        self.assertEqual(loaded["graph_revision"], 1)
        self.assertEqual(loaded["schema_version"], "plan-graph.v1")
        self.assertEqual(loaded["identity"]["repository"], str(self.repo.resolve()))
        self.assertEqual(loaded["identity"]["target_branch"], "feature/config-validation")
        self.assertEqual(
            loaded["baseline"]["repository_revision"],
            self._git("rev-parse", "HEAD"),
        )
        self.assertTrue(loaded["baseline"]["reproducible"])
        self.assertNotIn("status", loaded)

    def test_positive_rich_graph_contains_every_record_family_and_is_ready(self) -> None:
        receipt = self._initialize(rich_graph())
        loaded = self._load()
        self.assertEqual(receipt.state, "stale")
        audit = cloned_graph(loaded["audit"])
        audit.update(fresh=True, independent=True, graph_revision=1)
        receipt = self._typed_update(
            receipt, loaded, "refresh-audit", ["audit"], audit
        )
        loaded = self._load()

        self.assertEqual(receipt.state, "ready")
        self.assertEqual(self.helper.derive_plan_state(loaded), "ready")
        for family in (
            "identity",
            "baseline",
            "outcomes",
            "evidence",
            "decisions",
            "work",
            "proof",
            "git",
            "projections",
            "invalidations",
        ):
            with self.subTest(family=family):
                self.assertIn(family, loaded)
        self.assertEqual(set(loaded["work"]), {"T1", "T2", "T3", "J1"})
        self.assertEqual(set(loaded["git"]["lanes"]), {"L1", "L2"})
        self.assertEqual(loaded["decisions"]["D1"]["confirmed_version"], 1)

    def test_one_active_workflow_per_repository_branch_is_discovered_and_duplicate_refused(self) -> None:
        first = self._initialize()
        discovered = self.helper.discover_workflow(
            self.repo, "feature/config-validation", self.state_home
        )
        self.assertEqual(discovered.workflow_id, first.workflow_id)
        before = first.path.read_bytes()
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize()
        self.assertEqual(first.path.read_bytes(), before)

    def test_compare_and_swap_increments_revision_and_rejects_overlapping_stale_update(self) -> None:
        receipt = self._initialize()
        updated = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [
                {
                    "op": "set",
                    "path": ["work", "T1", "result"],
                    "value": "Validate configuration before startup",
                }
            ],
            self.state_home,
        )
        self.assertEqual(updated.revision, 2)
        with self.assertRaises(self.helper.RevisionConflict):
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                1,
                [{"op": "set", "path": ["work", "T1", "result"], "value": "stale"}],
                self.state_home,
            )
        self.assertEqual(
            self._load()["work"]["T1"]["result"],
            "Validate configuration before startup",
        )

    def test_disjoint_stale_update_can_be_reconciled_but_overlap_cannot(self) -> None:
        receipt = self._initialize()
        self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "first update"}],
            self.state_home,
        )
        reconciled = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["projections", "U1", "confirmed"], "value": False}],
            self.state_home,
            reconcile_disjoint=True,
        )
        self.assertEqual(reconciled.revision, 3)
        self.assertFalse(
            self._load()["projections"]["U1"]["confirmed"]
        )

    def test_competing_writers_serialize_and_only_one_overlapping_revision_wins(self) -> None:
        receipt = self._initialize()
        barrier = threading.Barrier(2)

        def update(value: str) -> tuple[str, object]:
            barrier.wait(timeout=5)
            try:
                result = self.helper.apply_updates(
                    self.repo,
                    "feature/config-validation",
                    receipt.workflow_id,
                    1,
                    [{"op": "set", "path": ["work", "T1", "result"], "value": value}],
                    self.state_home,
                )
                return "success", result
            except self.helper.RevisionConflict as error:
                return "conflict", error

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ("writer one", "writer two")))

        self.assertEqual(sorted(kind for kind, _ in results), ["conflict", "success"])
        loaded = self._load()
        self.assertEqual(loaded["graph_revision"], 2)
        self.assertIn(loaded["work"]["T1"]["result"], {"writer one", "writer two"})

    def test_readiness_is_derived_and_mutable_status_cannot_bypass_missing_coverage(self) -> None:
        graph = minimal_graph()
        graph["status"] = "ready"
        graph["proof"] = {}
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize(graph)

        receipt = self._initialize(minimal_graph())
        loaded = self._load()
        self.assertEqual(self.helper.derive_plan_state(loaded), "ready")
        self.assertEqual(receipt.state, "ready")

    def test_typed_updates_cannot_rebind_immutable_identity_or_schema_fields(self) -> None:
        forbidden = {
            "schema": (["schema_version"], "plan-graph.v999"),
            "workflow": (["workflow_id"], "replacement"),
            "revision": (["graph_revision"], 999),
            "repository": (["identity", "repository"], "/tmp/other"),
            "branch": (["identity", "target_branch"], "feature/other"),
            "baseline": (["baseline", "repository_revision"], "0" * 40),
        }
        for name, (path, value) in forbidden.items():
            with self.subTest(name=name):
                case_home = self.root / "immutable-cases" / name
                receipt = self._initialize(state_home=case_home)
                before = receipt.path.read_bytes()
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.apply_updates(
                        self.repo,
                        "feature/config-validation",
                        receipt.workflow_id,
                        1,
                        [{"op": "set", "path": path, "value": value}],
                        case_home,
                    )
                self.assertEqual(receipt.path.read_bytes(), before)

    def test_structural_references_cycles_joins_owners_and_parallel_claims_fail_closed(self) -> None:
        cycle = minimal_graph()
        cycle["work"]["T1"]["requires"] = ["T1"]
        orphan = minimal_graph()
        orphan["work"]["T1"]["covers"] = ["missing"]
        ownerless = minimal_graph()
        ownerless["work"]["T1"]["owner"] = ""
        unsafe_parallel = minimal_graph()
        unsafe_parallel["work"]["T1"]["concurrency"] = "parallel-safe"
        incomplete_join = rich_graph()
        incomplete_join["git"]["joins"]["J1"]["lanes"] = ["L1"]
        wrong_lane_base = rich_graph()
        wrong_lane_base["git"]["lanes"]["L1"]["base"] = "0" * 40
        branch_mismatch = minimal_graph()
        branch_mismatch["git"]["target"]["branch"] = "feature/other"

        for name, graph in (
            ("cycle", cycle),
            ("orphan", orphan),
            ("ownerless", ownerless),
            ("unsafe-parallel", unsafe_parallel),
            ("incomplete-join", incomplete_join),
            ("wrong-lane-base", wrong_lane_base),
            ("branch-mismatch", branch_mismatch),
        ):
            with self.subTest(name=name):
                self._assert_structurally_rejected(name, graph)

    def test_each_semantic_readiness_invariant_blocks_ready_independently(self) -> None:
        outcome_uncovered = minimal_graph()
        outcome_uncovered["work"]["T1"]["covers"] = ["C1"]
        constraint_uncovered = minimal_graph()
        constraint_uncovered["work"]["T1"]["covers"] = ["O1"]
        constraint_uncovered["proof"]["P1"]["covers"] = ["O1"]
        no_positive = minimal_graph()
        del no_positive["proof"]["P1"]["planned_method"]["positive"]
        no_negative = minimal_graph()
        del no_negative["proof"]["P1"]["planned_method"]["negative"]
        unresolved = minimal_graph()
        unresolved["unresolved"] = [
            {"id": "Q1", "kind": "user-decision", "material": True}
        ]
        nonreproducible = minimal_graph()
        nonreproducible["git"]["target"]["reproducible"] = False

        for name, graph, expected in (
            ("outcome-uncovered", outcome_uncovered, {"not-ready"}),
            ("constraint-uncovered", constraint_uncovered, {"not-ready"}),
            ("unresolved", unresolved, {"awaiting-user", "not-ready"}),
            ("nonreproducible", nonreproducible, {"not-ready"}),
        ):
            with self.subTest(name=name):
                self._assert_not_ready(name, graph, expected)

        for name, graph in (
            ("no-positive-proof", no_positive),
            ("no-negative-proof", no_negative),
        ):
            with self.subTest(name=name):
                self._assert_structurally_rejected(name, graph)

    def test_staleness_and_confirmation_versions_block_ready_independently(self) -> None:

        stale = minimal_graph()
        stale["evidence"]["E1"]["fresh"] = False
        unconfirmed = minimal_graph()
        unconfirmed["decisions"]["D1"] = {
            "question": "Select validation boundary",
            "choice": "configuration loader",
            "based_on": ["E1"],
            "material": True,
            "version": 1,
            "confirmed_version": None,
            "stale": False,
            "alternatives": [],
        }
        unconfirmed["work"]["T1"]["decisions"] = ["D1"]
        unconfirmed["projections"]["U1"]["covers"].append("D1")
        unconfirmed["projections"]["U1"]["decision_versions"] = {"D1": 1}

        projection_mismatch = rich_graph()
        projection_mismatch["projections"]["U1"]["decision_versions"]["D1"] = 2
        projection_missing = rich_graph()
        projection_missing["projections"]["U1"]["covers"].remove("D1")
        projection_missing["projections"]["U1"]["decision_versions"] = {}
        stale_decision = rich_graph()
        stale_decision["decisions"]["D1"]["stale"] = True

        for name, graph, expected in (
            ("stale-evidence", stale, {"stale"}),
            ("unconfirmed-decision", unconfirmed, {"awaiting-user"}),
            ("projection-version-mismatch", projection_mismatch, {"stale", "awaiting-user"}),
            ("projection-missing-decision", projection_missing, {"not-ready", "awaiting-user", "stale"}),
            ("stale-decision", stale_decision, {"stale"}),
        ):
            with self.subTest(name=name):
                self._assert_not_ready(name, graph, expected)

    def test_strict_parser_refuses_tags_unsupported_schema_and_oversized_graph(self) -> None:
        receipt = self._initialize()
        receipt.path.write_text("!!python/object/apply:os.system ['id']\n", encoding="utf-8")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo, "feature/config-validation", self.state_home
            )

        shutil.rmtree(self.state_home)
        unsupported = minimal_graph()
        unsupported["schema_version"] = "plan-graph.v999"
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize(unsupported)

        oversized = minimal_graph()
        oversized["outcomes"]["O1"]["result"] = "x" * self.helper.MAX_GRAPH_BYTES
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize(oversized)

        duplicate_home = self.root / "duplicate-key-state"
        duplicate = self._initialize(state_home=duplicate_home)
        duplicate.path.write_text(
            '{"schema_version":"plan-graph.v1","schema_version":"plan-graph.v1"}\n',
            encoding="utf-8",
        )
        duplicate.path.chmod(0o600)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo,
                "feature/config-validation",
                duplicate_home,
            )

    def test_generations_are_bounded_and_explicit_recovery_restores_the_previous_valid_graph(self) -> None:
        receipt = self._initialize()
        self.assertFalse(receipt.previous_path.exists())

        second = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "revision two"}],
            self.state_home,
        )
        self.assertEqual(second.revision, 2)
        self.assertEqual(json.loads(second.previous_path.read_text(encoding="utf-8"))["graph_revision"], 1)

        third = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            2,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "revision three"}],
            self.state_home,
        )
        self.assertEqual(third.revision, 3)
        self.assertEqual(json.loads(third.path.read_text(encoding="utf-8"))["graph_revision"], 3)
        self.assertEqual(json.loads(third.previous_path.read_text(encoding="utf-8"))["graph_revision"], 2)
        self.assertEqual(
            {path.name for path in third.path.parent.glob("*.yaml")},
            {third.path.name, third.previous_path.name},
        )

        third.path.write_text("interrupted", encoding="utf-8")
        third.path.chmod(0o600)
        with self.assertRaises(self.helper.PlanGraphError):
            self._load()
        recovered = self.helper.recover_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            self.state_home,
        )
        self.assertEqual(recovered.revision, 4)
        self.assertEqual(recovered.previous_revision, 2)
        self.assertEqual(self._load()["work"]["T1"]["result"], "revision two")
        self.assertLessEqual(len(list(recovered.path.parent.glob("*.yaml"))), 2)

    def test_failed_atomic_replace_preserves_current_generation_and_leaves_no_temporary_graph(self) -> None:
        receipt = self._initialize()
        before = receipt.path.read_bytes()
        real_replace = self.helper.os.replace

        def fail_current_replace(source: object, destination: object, *args: object, **kwargs: object) -> object:
            if Path(destination).name == receipt.path.name:
                raise OSError("injected current-generation replacement failure")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(self.helper.os, "replace", side_effect=fail_current_replace):
            with self.assertRaises(self.helper.PlanGraphError):
                self.helper.apply_updates(
                    self.repo,
                    "feature/config-validation",
                    receipt.workflow_id,
                    1,
                    [{"op": "set", "path": ["work", "T1", "result"], "value": "must not land"}],
                    self.state_home,
                )

        self.assertEqual(receipt.path.read_bytes(), before)
        self.assertEqual(self._load()["graph_revision"], 1)
        self.assertFalse(any("tmp" in path.name for path in receipt.path.parent.iterdir()))

    def test_successful_update_synchronizes_file_and_parent_directory(self) -> None:
        receipt = self._initialize()
        with mock.patch.object(self.helper.os, "fsync", wraps=self.helper.os.fsync) as fsync:
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                1,
                [{"op": "set", "path": ["work", "T1", "result"], "value": "durable"}],
                self.state_home,
            )
        self.assertGreaterEqual(fsync.call_count, 2)

    def test_nested_substitution_nonregular_mode_duplicate_and_traversal_attacks_fail_closed(self) -> None:
        symlink_home = self.root / "symlink-case"
        symlink_receipt = self._initialize(state_home=symlink_home)
        outside = self.root / "outside-graph"
        outside.write_text("caller-owned\n", encoding="utf-8")
        symlink_receipt.path.unlink()
        symlink_receipt.path.symlink_to(outside)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo,
                "feature/config-validation",
                symlink_home,
            )
        self.assertEqual(outside.read_text(encoding="utf-8"), "caller-owned\n")

        directory_home = self.root / "directory-case"
        directory_receipt = self._initialize(state_home=directory_home)
        directory_receipt.path.unlink()
        directory_receipt.path.mkdir()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo,
                "feature/config-validation",
                directory_home,
            )

        mode_home = self.root / "mode-case"
        mode_receipt = self._initialize(state_home=mode_home)
        mode_receipt.path.chmod(0o644)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo,
                "feature/config-validation",
                mode_home,
            )

        parent_home = self.root / "parent-symlink-case"
        parent_receipt = self._initialize(state_home=parent_home)
        original_parent = parent_receipt.path.parent
        held_parent = original_parent.with_name(original_parent.name + "-held")
        outside_parent = self.root / "outside-parent"
        outside_parent.mkdir(mode=0o700)
        outside_copy = outside_parent / parent_receipt.path.name
        shutil.copyfile(parent_receipt.path, outside_copy)
        outside_copy.chmod(0o600)
        original_parent.rename(held_parent)
        original_parent.symlink_to(outside_parent, target_is_directory=True)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.load_workflow(
                self.repo,
                "feature/config-validation",
                parent_home,
            )
        self.assertEqual(outside_copy.read_bytes(), (held_parent / parent_receipt.path.name).read_bytes())

        duplicate_home = self.root / "ambiguous-case"
        duplicate_receipt = self._initialize(state_home=duplicate_home)
        duplicate_path = duplicate_receipt.path.parent / "duplicate.yaml"
        shutil.copyfile(duplicate_receipt.path, duplicate_path)
        duplicate_path.chmod(0o600)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discover_workflow(
                self.repo,
                "feature/config-validation",
                duplicate_home,
            )

        traversal_home = self.root / "traversal-case"
        traversal_receipt = self._initialize(state_home=traversal_home)
        before = traversal_receipt.path.read_bytes()
        escape = self.root / "escape"
        escape.write_text("survives\n", encoding="utf-8")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                traversal_receipt.workflow_id,
                1,
                [{"op": "set", "path": ["..", "escape"], "value": "overwritten"}],
                traversal_home,
            )
        self.assertEqual(traversal_receipt.path.read_bytes(), before)
        self.assertEqual(escape.read_text(encoding="utf-8"), "survives\n")

    def test_previous_generation_substitution_refuses_update_without_touching_current_or_outside(self) -> None:
        receipt = self._initialize()
        second = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["work", "T1", "result"], "value": "second"}],
            self.state_home,
        )
        before = second.path.read_bytes()
        outside = self.root / "previous-outside"
        outside.write_text("outside\n", encoding="utf-8")
        second.previous_path.unlink()
        second.previous_path.symlink_to(outside)

        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                2,
                [{"op": "set", "path": ["work", "T1", "result"], "value": "third"}],
                self.state_home,
            )
        self.assertEqual(second.path.read_bytes(), before)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_canonical_repository_alias_discovers_the_same_workflow(self) -> None:
        alias = self.root / "repository-alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        receipt = self._initialize(repo=alias)
        discovered = self.helper.discover_workflow(
            self.repo,
            "feature/config-validation",
            self.state_home,
        )
        self.assertEqual(discovered.workflow_id, receipt.workflow_id)
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize()

    def test_bound_evidence_receipts_reject_each_wrong_identity_without_partial_update(self) -> None:
        mutations = {
            "workflow": ("workflow_id", "wrong-workflow"),
            "revision": ("graph_revision", 999),
            "node": ("node", "missing-node"),
            "branch": ("branch", "feature/wrong"),
            "commit": ("commit", "0" * 40),
            "result": ("result", "claimed"),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                case_home = self.root / "receipt-cases" / name
                receipt = self._initialize(state_home=case_home)
                evidence = {
                    "workflow_id": receipt.workflow_id,
                    "graph_revision": 1,
                    "node": "T1",
                    "branch": "feature/config-validation",
                    "commit": self._git("rev-parse", "HEAD"),
                    "check": "python3 -m pytest tests/test_config.py",
                    "result": {"status": "pass", "exit_code": 0},
                }
                evidence[field] = value
                before = receipt.path.read_bytes()
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.apply_updates(
                        self.repo,
                        "feature/config-validation",
                        receipt.workflow_id,
                        1,
                        [{"op": "set", "path": ["proof", "P1", "evidence"], "value": [evidence]}],
                        case_home,
                    )
                self.assertEqual(receipt.path.read_bytes(), before)

        valid_home = self.root / "receipt-cases" / "valid"
        receipt = self._initialize(state_home=valid_home)
        evidence = {
            "workflow_id": receipt.workflow_id,
            "graph_revision": 1,
            "node": "T1",
            "branch": "feature/config-validation",
            "commit": self._git("rev-parse", "HEAD"),
            "check": "python3 -m pytest tests/test_config.py",
            "result": {"status": "pass", "exit_code": 0},
        }
        updated = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            1,
            [{"op": "set", "path": ["proof", "P1", "evidence"], "value": [evidence]}],
            valid_home,
        )
        self.assertEqual(updated.revision, 2)
        self.assertEqual(self._load(state_home=valid_home)["proof"]["P1"]["evidence"], [evidence])

    def test_symlinked_or_world_accessible_state_is_refused_without_repository_mutation(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        self.state_home.symlink_to(outside, target_is_directory=True)
        before = self._git("status", "--porcelain=v1")
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize()
        self.assertEqual(self._git("status", "--porcelain=v1"), before)

        self.state_home.unlink()
        self.state_home.mkdir(mode=0o777)
        self.state_home.chmod(0o777)
        with self.assertRaises(self.helper.PlanGraphError):
            self._initialize()

    def test_pause_survives_and_only_explicit_discard_removes_owned_state(self) -> None:
        receipt = self._initialize()
        paused = self.helper.pause_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            receipt.revision,
            self.state_home,
        )
        self.assertTrue(paused.path.exists())
        self.assertEqual(
            self.helper.derive_plan_state(
                self.helper.load_workflow(
                    self.repo, "feature/config-validation", self.state_home
                )
            ),
            "paused",
        )
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discard_workflow(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                paused.revision,
                explicit=False,
                state_home=self.state_home,
            )
        self.assertTrue(paused.path.exists())
        self.helper.discard_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            paused.revision,
            explicit=True,
            state_home=self.state_home,
        )
        self.assertFalse(paused.path.parent.exists())

    def test_fresh_session_resume_revalidates_repository_revision_and_marks_changed_evidence_stale(self) -> None:
        unchanged_home = self.root / "resume-unchanged"
        receipt = self._initialize(state_home=unchanged_home)
        paused = self.helper.pause_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            receipt.revision,
            unchanged_home,
        )
        resumed = self.helper.resume_workflow(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            paused.revision,
            unchanged_home,
        )
        self.assertEqual(resumed.state, "ready")

        changed_home = self.root / "resume-changed"
        changed = self._initialize(state_home=changed_home)
        changed_paused = self.helper.pause_workflow(
            self.repo,
            "feature/config-validation",
            changed.workflow_id,
            changed.revision,
            changed_home,
        )
        (self.repo / "src" / "config.py").write_text("def load():\n    return {'changed': True}\n", encoding="utf-8")
        self._git("add", "src/config.py")
        self._git("commit", "-m", "change grounded source")
        stale = self.helper.resume_workflow(
            self.repo,
            "feature/config-validation",
            changed.workflow_id,
            changed_paused.revision,
            changed_home,
        )
        self.assertEqual(stale.state, "stale")
        stale_graph = self._load(state_home=changed_home)
        self.assertFalse(stale_graph["evidence"]["E1"]["fresh"])
        self.assertNotEqual(stale_graph["baseline"]["repository_revision"], self._git("rev-parse", "HEAD"))

    def test_discard_refuses_unknown_or_wrongly_bound_state_and_preserves_recovery_paths(self) -> None:
        receipt = self._initialize()
        unknown = receipt.path.parent / "caller-owned.txt"
        unknown.write_text("preserve\n", encoding="utf-8")
        unknown.chmod(0o600)
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discard_workflow(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                receipt.revision,
                explicit=True,
                state_home=self.state_home,
            )
        self.assertTrue(receipt.path.is_file())
        self.assertEqual(unknown.read_text(encoding="utf-8"), "preserve\n")

        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.discard_workflow(
                self.repo,
                "feature/config-validation",
                "wrong-workflow",
                receipt.revision,
                explicit=True,
                state_home=self.state_home,
            )
        self.assertTrue(receipt.path.is_file())

    def test_draft_delivery_requires_a_coherent_commit_and_terminal_handoff_requires_exact_head_gates(self) -> None:
        receipt = self._initialize(rich_graph())
        head = self._git("rev-parse", "HEAD")
        before_refs = self._git("for-each-ref", "--format=%(refname):%(objectname)")

        graph = self._load()
        audit = cloned_graph(graph["audit"])
        audit.update(fresh=True, independent=True, graph_revision=receipt.revision)
        receipt = self._typed_update(
            receipt, graph, "refresh-audit", ["audit"], audit
        )

        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                receipt.workflow_id,
                receipt.revision,
                [{"op": "set", "path": ["git", "delivery"], "value": {"state": "draft"}}],
                self.state_home,
            )
        self.assertTrue(receipt.path.is_file())

        def build_delivery(active: object, home: Path) -> dict[str, object]:
            provenances = {
                role: self._register_provenance(
                    workflow_id=active.workflow_id,
                    revision=active.revision,
                    role=role,
                    salt=f"{active.workflow_id}-{role}",
                    source=f"{role}-raw",
                    state_home=home,
                )
                for role in ("implement", "review", "verify", "integrate")
            }
            policy = self._register_provenance(
                workflow_id=active.workflow_id,
                revision=active.revision,
                role="provider-policy",
                salt=f"{active.workflow_id}-policy",
                source="pytest",
                state_home=home,
            )

            def gate(role: str) -> dict[str, object]:
                provenance = provenances[role]
                record: dict[str, object] = {
                    "role": role,
                    "receipt_id": f"{role}-receipt",
                    "provenance_id": provenance["receipt_id"],
                    "provenance_digest": provenance["digest"],
                    "provenance_session": provenance["session_id"],
                    "provenance_source": provenance["source"],
                    "raw_evidence_digest": provenance["raw_evidence_digest"],
                    "workflow_id": active.workflow_id,
                    "graph_revision": active.revision,
                    "branch": "feature/config-validation",
                    "commit": head,
                    "work": ["T1"],
                    "proof": ["P1"],
                    "commands": ["python3 -m pytest"],
                    "results": [{"command": "python3 -m pytest", "exit_code": 0, "output": "passed"}],
                    "result": {"status": "pass"},
                }
                if role == "integrate":
                    record.update(work=["J1"], proof=["P4"], join="J1", integrated_proof="join proof integrated")
                elif role == "implement":
                    record.update(implementation_evidence="implementation evidence")
                elif role == "review":
                    record.update(disposition="approve", findings=[])
                else:
                    record.update(independent=True)
                if role in {"implement", "review", "verify"}:
                    record.update(work=["T1", "T2", "T3"], proof=["P1", "P2", "P3", "P4"])
                record["digest"] = hashlib.sha256(
                    json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                return record

            return {
                "state": "draft",
                "request": {"id": "PR-17", "url": "https://example.invalid/pull/17", "draft": True, "head": head},
                "first_coherent_commit": head,
                "exact_head": head,
                "gates": {"implementation": gate("implement"), "review": gate("review"),
                          "verification": gate("verify"), "target_proof": gate("integrate")},
                "checks_policy": {"provider": "local", "source": "pytest", "request_id": "PR-17",
                    "workflow_id": active.workflow_id, "graph_revision": active.revision,
                    "branch": "feature/config-validation", "head": head,
                    "observed_at": "2026-01-01T00:00:00Z", "authoritative": True,
                    "discovered_names": ["tests"], "provenance_id": policy["receipt_id"],
                    "provenance_digest": policy["digest"],
                    "provenance_session": policy["session_id"],
                    "raw_evidence_digest": policy["raw_evidence_digest"]},
                "checks": [{"run_id": "run-1", "url": "https://example.invalid/run-1", "name": "tests",
                            "head": head, "conclusion": "success", "status": "pass", "result": "passed"}],
                "lanes_clean": True,
                "handoff": {"title": "Validate configuration before startup",
                            "summary": "All AI-owned gates are bound to the exact request head."},
            }

        delivery = build_delivery(receipt, self.state_home)
        draft = self.helper.apply_updates(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            receipt.revision,
            [{"op": "set", "path": ["git", "delivery"], "value": delivery}],
            self.state_home,
        )
        self.assertEqual(draft.revision, receipt.revision + 1)

        incomplete_cases = {
            "review-pending": lambda value: value["gates"]["review"]["result"].update(status="pending"),
            "check-pending": lambda value: value["checks"][0].update(status="pending"),
            "lanes-not-clean": lambda value: value.update(lanes_clean=False),
            "handoff-missing": lambda value: value.update(handoff={}),
        }
        for name, mutate in incomplete_cases.items():
            with self.subTest(name=name):
                case_home = self.root / "terminal-cases" / name
                case_receipt = self._initialize(rich_graph(), state_home=case_home)
                case_graph = self._load(state_home=case_home)
                case_audit = cloned_graph(case_graph["audit"])
                case_audit.update(fresh=True, independent=True, graph_revision=case_receipt.revision)
                case_receipt = self._typed_update(
                    case_receipt, case_graph, "refresh-audit", ["audit"], case_audit,
                    state_home=case_home,
                )
                incomplete = build_delivery(case_receipt, case_home)
                mutate(incomplete)
                for bound_gate in incomplete["gates"].values():
                    bound_gate.pop("digest", None)
                    bound_gate["digest"] = hashlib.sha256(json.dumps(bound_gate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                case_draft = self.helper.apply_updates(
                    self.repo,
                    "feature/config-validation",
                    case_receipt.workflow_id,
                    case_receipt.revision,
                    [{"op": "set", "path": ["git", "delivery"], "value": incomplete}],
                    case_home,
                )
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.complete_for_human_review(
                        self.repo,
                        "feature/config-validation",
                        case_receipt.workflow_id,
                        case_draft.revision,
                        case_home,
                    )
                self.assertTrue(case_draft.path.is_file())

        wrong_home = self.root / "wrong-terminal-head"
        wrong_receipt = self._initialize(rich_graph(), state_home=wrong_home)
        wrong_graph = self._load(state_home=wrong_home)
        wrong_audit = cloned_graph(wrong_graph["audit"])
        wrong_audit.update(fresh=True, independent=True, graph_revision=wrong_receipt.revision)
        wrong_receipt = self._typed_update(
            wrong_receipt, wrong_graph, "refresh-audit", ["audit"], wrong_audit,
            state_home=wrong_home,
        )
        wrong_head = build_delivery(wrong_receipt, wrong_home)
        wrong_head["gates"]["verification"]["commit"] = "0" * 40
        wrong_head["gates"]["verification"].pop("digest")
        wrong_head["gates"]["verification"]["digest"] = hashlib.sha256(
            json.dumps(wrong_head["gates"]["verification"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.apply_updates(
                self.repo,
                "feature/config-validation",
                wrong_receipt.workflow_id,
                wrong_receipt.revision,
                [{"op": "set", "path": ["git", "delivery"], "value": wrong_head}],
                wrong_home,
            )
        self.assertTrue(wrong_receipt.path.is_file())

        terminal = self.helper.complete_for_human_review(
            self.repo,
            "feature/config-validation",
            receipt.workflow_id,
            draft.revision,
            self.state_home,
        )
        self.assertEqual(terminal.state, "ready-for-human-review")
        self.assertEqual(terminal.request_id, "PR-17")
        self.assertEqual(terminal.head, head)
        self.assertFalse(draft.path.parent.exists())
        self.assertEqual(
            self._git("for-each-ref", "--format=%(refname):%(objectname)"),
            before_refs,
        )

    def test_branch_setup_requires_confirmation_and_never_overwrites_existing_branch(self) -> None:
        self._git("switch", "-C", "main")
        baseline = self._git("rev-parse", "HEAD")
        unrelated = self.repo / "unrelated.local"
        unrelated.write_text("preserve\n", encoding="utf-8")
        before_status = self._git("status", "--porcelain=v1")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.create_workflow_branch(
                self.repo, "feature/new-plan", baseline, confirmed=False
            )
        self.assertEqual(self._git("branch", "--show-current"), "main")
        self.assertEqual(self._git("status", "--porcelain=v1"), before_status)

        for unsafe in ("main", "develop", "--orphan", "../escape"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(self.helper.PlanGraphError):
                    self.helper.create_workflow_branch(
                        self.repo, unsafe, baseline, confirmed=True
                    )
                self.assertEqual(self._git("branch", "--show-current"), "main")

        created = self.helper.create_workflow_branch(
            self.repo, "feature/new-plan", baseline, confirmed=True
        )
        self.assertEqual(created.branch, "feature/new-plan")
        self.assertEqual(created.commit, baseline)
        self.assertEqual(self._git("branch", "--show-current"), "feature/new-plan")
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve\n")
        self.assertEqual(self._git("status", "--porcelain=v1"), before_status)

        self._git("switch", "main")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.create_workflow_branch(
                self.repo, "feature/new-plan", baseline, confirmed=True
            )
        self.assertEqual(self._git("branch", "--show-current"), "main")

    def test_detached_entry_requires_confirmation_and_creates_only_the_authorized_branch(self) -> None:
        baseline = self._git("rev-parse", "HEAD")
        self._git("switch", "--detach", baseline)
        before_refs = self._git("for-each-ref", "--format=%(refname):%(objectname)", "refs/heads")
        with self.assertRaises(self.helper.PlanGraphError):
            self.helper.create_workflow_branch(
                self.repo,
                "feature/from-detached",
                baseline,
                confirmed=False,
            )
        self.assertEqual(self._git("branch", "--show-current"), "")
        self.assertEqual(
            self._git("for-each-ref", "--format=%(refname):%(objectname)", "refs/heads"),
            before_refs,
        )

        created = self.helper.create_workflow_branch(
            self.repo,
            "feature/from-detached",
            baseline,
            confirmed=True,
        )
        self.assertEqual(created.branch, "feature/from-detached")
        self.assertEqual(created.commit, baseline)
        self.assertEqual(self._git("branch", "--show-current"), "feature/from-detached")


class PlanGraphMissingCapabilityTest(unittest.TestCase):
    def test_import_time_skip_is_a_failure_not_evidence_omission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / "plan_graph.py"
            candidate.write_text(
                "import unittest\nraise unittest.SkipTest('omit runtime evidence')\n",
                encoding="utf-8",
            )
            with mock.patch(__name__ + ".HELPER_PATH", candidate):
                with self.assertRaisesRegex(AssertionError, "attempted to skip"):
                    load_helper()

    def test_runtime_skip_is_a_failure_not_evidence_omission(self) -> None:
        class Candidate:
            @staticmethod
            def initialize_workflow() -> None:
                raise unittest.SkipTest("omit runtime evidence")

        helper = _NoSkipModule(Candidate())
        with self.assertRaisesRegex(AssertionError, "attempted to skip"):
            helper.initialize_workflow()

    def test_attribute_lookup_skips_are_failures_not_evidence_omissions(self) -> None:
        PytestStyleSkipped = type(
            "Skipped",
            (BaseException,),
            {"__module__": "_pytest.outcomes"},
        )

        class Candidate:
            def __init__(self, error: BaseException) -> None:
                self.error = error

            def __getattr__(self, name: str) -> object:
                raise self.error

        for name, error in (
            ("unittest", unittest.SkipTest("omit attribute evidence")),
            ("pytest", PytestStyleSkipped("omit attribute evidence")),
        ):
            with self.subTest(name=name):
                helper = _NoSkipModule(Candidate(error))
                with self.assertRaisesRegex(AssertionError, "attempted to skip"):
                    helper.initialize_workflow

    def test_route_neutral_plan_graph_helper_exists(self) -> None:
        self.assertTrue(HELPER_PATH.is_file(), HELPER_PATH)


if __name__ == "__main__":
    unittest.main()
