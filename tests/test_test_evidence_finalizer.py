from __future__ import annotations

import ast
import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TEST_SKILL = ROOT / "packages" / "codex" / "skills" / "test"
FINALIZER = TEST_SKILL / "scripts" / "finalize_evidence.py"
QUALITY_CATALOG = TEST_SKILL / "references" / "quality-rules.json"
EVIDENCE_CONTRACT = TEST_SKILL / "references" / "evidence-contract.json"
HEAD = "0123456789012345678901234567890123456789"
COMPOSITE_CANONICAL = (
    '{"a":[{"😀":"astral","דּ":"bmp"},false],'
    '"z":{"a":null,"b":true}}'
)
COMPOSITE_SHA256 = "6d8b5436cc1e91290fd206e5d4536ac2e61bcc82d3b403645647a88b543194bb"
RFC7638_SHA256 = "3736cbb1787cb8309c77ee8c3705c5e16ffb9e859715901f1e4c59b11182f57b"
RFC7638_MODULUS = (
    "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAt"
    "VT86zwu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn6"
    "4tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FD"
    "W2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n9"
    "1CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINH"
    "aQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw"
)


def _fixture_canonical(value: object) -> bytes:
    """Canonical bytes for the ASCII-only protocol fixtures, independent of production."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_fixture_canonical(value)).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.write_bytes(_fixture_canonical(value))
    return path


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rule_applicability() -> list[dict[str, object]]:
    catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
    return [
        {
            "rule_id": rule["id"],
            "status": "active" if rule["id"].startswith("universal.") else "inactive",
            "outcome": "satisfied"
            if rule["id"].startswith("universal.")
            else "not-applicable",
            "evidence": ["Repository-grounded applicability was classified."],
        }
        for rule in catalog["rules"]
    ]


def write_charter(run_root: Path, *, exempt: bool = False) -> Path:
    behavior = "A consumer observes the accepted response."
    charter: dict[str, object] = {
        "schema_version": "test-charter.v1",
        "run_id": run_root.name,
        "workflow_id": None,
        "repository": str(run_root.parents[1].resolve()),
        "branch": "feature/test-finalizer",
        "head": HEAD,
        "accepted_behavior": behavior,
        "scope": {
            "accepted_behavior": behavior,
            "inner_ring": ["consumer command"],
            "adjacent_ring": ["adapter boundary"],
            "broader_ring": ["application smoke path"],
        },
        "material_oracles": []
        if exempt
        else [
            {
                "oracle_id": "consumer-response",
                "behavior": "The accepted response reaches the consumer.",
                "consumer_surface": "public CLI output",
                "required_action_ids": ["check-consumer"],
            }
        ],
        "exemption_grounding_artifact_ids": ["consumer-log"] if exempt else [],
    }
    return _write_json(run_root / "charter.json", charter)


def write_ledger(
    run_root: Path, *, statuses: tuple[str, ...] = ("pass",)
) -> Path:
    charter = _read_json(run_root / "charter.json")
    entries: list[dict[str, object]] = []
    for index, status in enumerate(statuses):
        action_id = "check-consumer" if index == 0 else f"check-consumer-{index + 1}"
        entries.append(
            {
                "action_id": action_id,
                "role": "check",
                "ring": "inner",
                "head": HEAD,
                "action": f"Run consumer observation {index + 1}",
                "path": [],
                "expected": "The consumer receives the accepted response.",
                "actual": f"The consumer observation was {status}.",
                "status": status,
                "oracle_ids": ["consumer-response"],
                "artifact_ids": ["consumer-log"],
            }
        )
    ledger = {
        "schema_version": "test-action-ledger.v1",
        "run_id": run_root.name,
        "charter_digest": _digest(charter),
        "entries": entries,
    }
    return _write_json(run_root / "ledger.json", ledger)


def write_draft(run_root: Path, *, intended_result: str = "PASS") -> Path:
    finding: dict[str, object] = {
        "kind": "product-defect",
        "severity": "high",
        "ring": "inner",
        "journey": "public CLI output",
        "expected": "The consumer receives the accepted response.",
        "actual": "The consumer did not receive the accepted response.",
        "reproduction": ["Run the retained consumer observation."],
        "violated_rule_ids": ["universal.consumer-outcome"],
        "artifacts": ["consumer-log"],
    }
    rule_applicability = [] if intended_result == "EXEMPT" else _rule_applicability()
    if intended_result == "FAIL":
        for rule in rule_applicability:
            if rule["rule_id"] == "universal.consumer-outcome":
                rule["outcome"] = "unsatisfied"
                break
    draft: dict[str, object] = {
        "schema_version": "test-evidence-draft.v1",
        "environment": {
            "runner": "python-unittest",
            "isolation": "temporary-repository",
        },
        "rule_applicability": rule_applicability,
        "exploration": {
            "mission": "Probe the adjacent consumer boundary once.",
            "evidence_budget": "One bounded observation.",
            "actions": ["Observe the adjacent boundary."],
            "observations": ["The observation matched the retained action."],
            "stop_condition": "Stop after one attributable observation.",
            "teardown": ["Remove owned temporary data."],
        },
        "findings": [finding] if intended_result == "FAIL" else [],
        "artifacts": [
            {
                "artifact_id": "consumer-log",
                "kind": "log",
                "path": "artifacts/consumer.log",
            }
        ],
        "teardown": {
            "status": "not-required" if intended_result == "EXEMPT" else "pass",
            "actions": [],
            "artifact_ids": [],
        },
        "test_side_commits": [],
        "limitations": [],
        "started_at": "2026-09-02T10:00:00Z",
        "summary": f"The retained run reached {intended_result} truthfully.",
        "intended_result": intended_result,
    }
    return _write_json(run_root / "draft.json", draft)


def invoke_finalizer(
    run_root: Path, *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(FINALIZER),
            "--root",
            str(run_root),
            "--charter",
            str(run_root / "charter.json"),
            "--ledger",
            str(run_root / "ledger.json"),
            "--draft",
            str(run_root / "draft.json"),
        ],
        cwd=run_root.parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def invoke_draft_composer(
    run_root: Path,
    *,
    preparation: Path | None = None,
    delta: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(FINALIZER),
            "compose-draft",
            "--root",
            str(run_root),
            "--preparation",
            str(preparation or run_root / "draft-preparation.json"),
            "--delta",
            str(delta or run_root / "draft-final-delta.json"),
        ],
        cwd=run_root.parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def write_draft_composition_inputs(run_root: Path) -> tuple[Path, Path]:
    """Write a large stable template plus only the explicitly pending delta."""

    draft_path = run_root / "draft.json"
    draft = _read_json(draft_path)
    draft_path.unlink()
    draft["environment"]["stable_context"] = "stable-" + ("x" * 16_384)
    draft["rule_applicability"][0]["outcome"] = {
        "$test_pending": "final-rule-outcome"
    }
    draft["exploration"]["observations"] = [
        "Stable observations were recorded before the final action.",
        {"$test_pending_splice": "final-observations"},
    ]
    draft["findings"] = [{"$test_pending_splice": "final-findings"}]
    draft["artifacts"].append({"$test_pending_splice": "final-artifacts"})
    draft["teardown"]["status"] = {"$test_pending": "final-teardown-status"}
    draft["teardown"]["actions"] = [
        {"$test_pending_splice": "final-teardown-actions"}
    ]
    draft["teardown"]["artifact_ids"] = [
        {"$test_pending_splice": "final-teardown-artifacts"}
    ]
    draft["limitations"] = [{"$test_pending_splice": "final-limitations"}]
    draft["summary"] = {"$test_pending": "final-summary"}
    draft["intended_result"] = {"$test_pending": "final-result"}
    preparation = {
        "schema_version": "test-draft-preparation.v1",
        "draft_template": draft,
    }
    delta = {
        "schema_version": "test-draft-final-delta.v1",
        "resolutions": [
            {"resolution_id": "final-rule-outcome", "value": "satisfied"},
            {
                "resolution_id": "final-observations",
                "value": ["The final consumer observation passed."],
            },
            {"resolution_id": "final-findings", "value": []},
            {"resolution_id": "final-artifacts", "value": []},
            {"resolution_id": "final-teardown-status", "value": "pass"},
            {"resolution_id": "final-teardown-actions", "value": []},
            {"resolution_id": "final-teardown-artifacts", "value": []},
            {"resolution_id": "final-limitations", "value": []},
            {
                "resolution_id": "final-summary",
                "value": "The final consumer observation passed.",
            },
            {"resolution_id": "final-result", "value": "PASS"},
        ],
    }
    return (
        _write_json(run_root / "draft-preparation.json", preparation),
        _write_json(run_root / "draft-final-delta.json", delta),
    )


def write_compact_draft_composition_inputs(run_root: Path) -> tuple[Path, Path]:
    """Write the compact assessment form expanded mechanically by the composer."""

    draft_path = run_root / "draft.json"
    draft = _read_json(draft_path)
    draft_path.unlink()
    intended_result = draft["intended_result"]
    active_rule_ids = [
        item["rule_id"]
        for item in draft.pop("rule_applicability")
        if item["status"] == "active"
    ]
    draft["exploration"]["observations"] = [
        {"$test_pending_splice": "final-observations"}
    ]
    draft["findings"] = [{"$test_pending_splice": "final-findings"}]
    draft["artifacts"].append({"$test_pending_splice": "final-artifacts"})
    draft["teardown"]["status"] = {"$test_pending": "final-teardown-status"}
    draft["teardown"]["actions"] = [
        {"$test_pending_splice": "final-teardown-actions"}
    ]
    draft["teardown"]["artifact_ids"] = [
        {"$test_pending_splice": "final-teardown-artifacts"}
    ]
    draft["limitations"] = [{"$test_pending_splice": "final-limitations"}]
    draft["summary"] = {"$test_pending": "final-summary"}
    draft["intended_result"] = {"$test_pending": "final-result"}
    preparation = {
        "schema_version": "test-draft-preparation.v2",
        "draft_template": draft,
        "rule_disposition": "exempt" if intended_result == "EXEMPT" else "evaluate",
        "active_rule_conditions": [],
        "rule_assessment_groups": [] if intended_result == "EXEMPT" else [
            {
                "rule_ids": active_rule_ids,
                "outcome": "satisfied",
                "evidence_action_ids": ["check-consumer"],
            }
        ],
    }
    delta = {
        "schema_version": "test-draft-final-delta.v1",
        "resolutions": [
            {
                "resolution_id": "final-observations",
                "value": ["The final consumer observation passed."],
            },
            {"resolution_id": "final-findings", "value": []},
            {"resolution_id": "final-artifacts", "value": []},
            {"resolution_id": "final-teardown-status", "value": "pass"},
            {"resolution_id": "final-teardown-actions", "value": []},
            {"resolution_id": "final-teardown-artifacts", "value": []},
            {"resolution_id": "final-limitations", "value": []},
            {
                "resolution_id": "final-summary",
                "value": f"The compact run reached {intended_result} truthfully.",
            },
            {"resolution_id": "final-result", "value": intended_result},
        ],
    }
    return (
        _write_json(run_root / "draft-preparation.json", preparation),
        _write_json(run_root / "draft-final-delta.json", delta),
    )


def write_direct_draft_composition_inputs(run_root: Path) -> tuple[Path, Path]:
    """Write one complete conditional candidate plus the required empty delta."""

    draft_path = run_root / "draft.json"
    draft = _read_json(draft_path)
    draft_path.unlink()
    intended_result = draft["intended_result"]
    active_rule_ids = [
        item["rule_id"]
        for item in draft.pop("rule_applicability")
        if item["status"] == "active"
    ]
    preparation = {
        "schema_version": "test-draft-preparation.v3",
        "draft_candidate": draft,
        "rule_disposition": "exempt" if intended_result == "EXEMPT" else "evaluate",
        "active_rule_conditions": [],
        "rule_assessment_groups": [] if intended_result == "EXEMPT" else [
            {
                "rule_ids": active_rule_ids,
                "outcome": "satisfied",
                "evidence_action_ids": ["check-consumer"],
            }
        ],
    }
    delta = {
        "schema_version": "test-draft-final-delta.v2",
        "resolutions": [],
    }
    return (
        _write_json(run_root / "draft-preparation.json", preparation),
        _write_json(run_root / "draft-final-delta.json", delta),
    )


def _load_finalizer_module():
    spec = importlib.util.spec_from_file_location("test_evidence_finalizer", FINALIZER)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {FINALIZER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class TestEvidenceFinalizer(unittest.TestCase):
    def _prepare_run(self, repository: Path, result: str = "PASS") -> Path:
        run_root = repository / ".test-evidence" / "run-001"
        (run_root / "artifacts").mkdir(parents=True)
        run_root.chmod(0o700)
        (run_root / "artifacts" / "consumer.log").write_text(
            "consumer observation\n", encoding="utf-8"
        )
        write_charter(run_root, exempt=result == "EXEMPT")
        statuses: tuple[str, ...]
        if result == "EXEMPT":
            statuses = ()
        elif result == "FAIL":
            statuses = ("fail",)
        elif result == "BLOCKED":
            statuses = ("blocked",)
        else:
            statuses = ("pass",)
        write_ledger(run_root, statuses=statuses)
        write_draft(run_root, intended_result=result)
        return run_root

    def _assert_failure(self, run_root: Path, *, code: str | None = None) -> dict:
        completed = invoke_finalizer(run_root)
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertNotIn("terminal_preflight=PASS", completed.stdout)
        self.assertFalse((run_root / "terminal").exists())
        self.assertFalse((run_root / "bundle.json").exists())
        self.assertFalse((run_root / "receipt.json").exists())
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(payload["status"], "ERROR")
        self.assertIsInstance(payload["errors"], list)
        if code is not None:
            self.assertIn(code, {error["code"] for error in payload["errors"]})
        return payload

    def test_pass_round_trip_publishes_valid_bundle_receipt_and_exact_two_line_stdout(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertTrue(completed.stdout.endswith("\n"))
            lines = completed.stdout.splitlines()
            self.assertEqual(lines[0], "terminal_preflight=PASS")
            self.assertEqual(len(lines), 2)
            payload = json.loads(lines[1])
            terminal = run_root / "terminal"
            self.assertEqual(stat.S_IMODE(terminal.stat().st_mode), 0o700)
            self.assertEqual(
                {path.name for path in terminal.iterdir()},
                {
                    "charter.json",
                    "ledger.json",
                    "draft.json",
                    "environment.json",
                    "bundle.json",
                    "receipt.json",
                },
            )
            for path in terminal.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
            bundle = _read_json(terminal / "bundle.json")
            receipt = _read_json(terminal / "receipt.json")
            digest_input = dict(bundle)
            del digest_input["bundle_digest"]
            self.assertEqual(bundle["bundle_digest"], _digest(digest_input))
            self.assertEqual(receipt["bundle_digest"], bundle["bundle_digest"])
            self.assertEqual(receipt["selected_scope_digest"], _digest(bundle["scope"]))
            self.assertEqual(receipt["environment_digest"], _digest({"isolation": "temporary-repository", "runner": "python-unittest"}))
            self.assertEqual(payload["receipt"], receipt)
            self.assertEqual(payload["bundle_digest"], bundle["bundle_digest"])
            self.assertEqual(payload["bundle_path"], str(terminal / "bundle.json"))
            self.assertEqual(payload["receipt_path"], str(terminal / "receipt.json"))
            self.assertEqual(payload["result"], "PASS")
            self.assertEqual(payload["summary"], "The retained run reached PASS truthfully.")
            self.assertEqual([item["check_id"] for item in bundle["checks"]], ["check-consumer"])
            self.assertEqual(bundle["journeys"], [])
            artifact_ids = {item["artifact_id"] for item in bundle["artifacts"]}
            self.assertEqual(
                artifact_ids,
                {
                    "consumer-log",
                    "test-charter",
                    "test-action-ledger",
                    "test-draft",
                    "test-environment",
                },
            )
            contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
            self.assertEqual(set(bundle), set(contract["bundle"]["required"]))
            self.assertEqual(set(receipt), set(contract["receipt"]["required"]))

    def test_v2_ledger_derives_the_charter_binding_without_a_copied_digest(self) -> None:
        """Regression: a one-character copied digest cannot derail terminalization."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            ledger = _read_json(run_root / "ledger.json")
            ledger["schema_version"] = "test-action-ledger.v2"
            del ledger["charter_digest"]
            _write_json(run_root / "ledger.json", ledger)

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            published = _read_json(run_root / "terminal" / "ledger.json")
            self.assertEqual(published["schema_version"], "test-action-ledger.v2")
            self.assertNotIn("charter_digest", published)

    def test_v2_ledger_rejects_a_redundant_manual_charter_digest(self) -> None:
        """The direct schema must have one unambiguous, mechanically bound shape."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            ledger = _read_json(run_root / "ledger.json")
            ledger["schema_version"] = "test-action-ledger.v2"
            _write_json(run_root / "ledger.json", ledger)

            self._assert_failure(run_root, code="unexpected-key")

    def test_check_path_is_tolerated_but_not_published(self) -> None:
        """Regression: a harmless source anchor cannot derail terminalization."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            ledger = _read_json(run_root / "ledger.json")
            ledger["entries"][0]["path"] = ["tests/e2e/harness.json"]
            _write_json(run_root / "ledger.json", ledger)

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            bundle = _read_json(run_root / "terminal" / "bundle.json")
            self.assertNotIn("path", bundle["checks"][0])

    def test_fail_blocked_and_exempt_round_trip(self) -> None:
        for result in ("FAIL", "BLOCKED", "EXEMPT"):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary), result)

                completed = invoke_finalizer(run_root)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout.splitlines()[1])
                bundle = _read_json(run_root / "terminal" / "bundle.json")
                self.assertEqual(payload["result"], result)
                self.assertEqual(bundle["result"], result)
                if result == "EXEMPT":
                    self.assertEqual(bundle["checks"], [])
                    self.assertEqual(bundle["journeys"], [])
                    self.assertEqual(bundle["rule_applicability"], [])

    def test_rfc8785_number_free_official_vectors(self) -> None:
        finalizer = _load_finalizer_module()
        composite = {
            "z": {"b": True, "a": None},
            "a": [{"דּ": "bmp", "😀": "astral"}, False],
        }
        rfc7638 = {"n": RFC7638_MODULUS, "kty": "RSA", "e": "AQAB"}

        self.assertEqual(finalizer.canonical_json(composite).decode(), COMPOSITE_CANONICAL)
        self.assertEqual(
            hashlib.sha256(finalizer.canonical_json(composite)).hexdigest(),
            COMPOSITE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(finalizer.canonical_json(rfc7638)).hexdigest(),
            RFC7638_SHA256,
        )
        self.assertEqual(
            finalizer.canonical_json({"b": "second", "a": "first"}),
            finalizer.canonical_json({"a": "first", "b": "second"}),
        )
        self.assertNotEqual(finalizer.canonical_json(["a", "b"]), finalizer.canonical_json(["b", "a"]))
        self.assertEqual(
            finalizer.canonical_json({"z": {"z": False, "a": True}}),
            b'{"z":{"a":true,"z":false}}',
        )
        self.assertEqual(
            finalizer.canonical_json("\x00\b\t\n\f\r\"\\"),
            b'"\\u0000\\b\\t\\n\\f\\r\\\"\\\\"',
        )
        self.assertNotEqual(finalizer.canonical_json("é"), finalizer.canonical_json("e\u0301"))

    def test_rejects_every_json_number_duplicate_decoded_keys_and_invalid_unicode(
        self,
    ) -> None:
        finalizer = _load_finalizer_module()
        for value in (0, -0.0, 1.0, float("nan"), float("inf")):
            with self.subTest(python_value=repr(value)):
                with self.assertRaises(finalizer.FinalizerError):
                    finalizer.canonical_json(value)

        for token in ("0", "-0", "1e0", "NaN", "Infinity"):
            with self.subTest(token=token), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                (run_root / "draft.json").write_text(
                    '{"schema_version":"test-evidence-draft.v1","number":'
                    + token
                    + "}",
                    encoding="utf-8",
                )
                self._assert_failure(run_root, code="number-not-allowed")

        for raw, code in (
            ('{"run_id":"run-001","\\u0072un_id":"run-001"}', "duplicate-key"),
            ('{"summary":"\\ud800"}', "invalid-unicode"),
            ('{"summary":"\\ufdd0"}', "invalid-unicode"),
        ):
            with self.subTest(code=code, raw=raw), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                (run_root / "draft.json").write_text(raw, encoding="utf-8")
                self._assert_failure(run_root, code=code)

    def test_rejects_insecure_or_symlinked_canonical_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            run_root.chmod(0o755)
            self._assert_failure(run_root, code="insecure-root")

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            real_evidence = repository / "real-evidence"
            real_evidence.mkdir()
            (repository / ".test-evidence").symlink_to(real_evidence, target_is_directory=True)
            run_root = self._prepare_run(repository)
            self._assert_failure(run_root, code="symlinked-path")

    def test_helper_exposes_only_the_finalizer_cli_and_no_engine_primitives(self) -> None:
        source = FINALIZER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_roots.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertTrue(
            imported_roots.isdisjoint(
                {"subprocess", "socket", "urllib", "http", "requests"}
            ),
            imported_roots,
        )
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(
            called_attributes.isdisjoint(
                {"system", "popen", "spawnl", "spawnv", "walk", "glob", "rglob"}
            ),
            called_attributes,
        )

        completed = subprocess.run(
            [sys.executable, str(FINALIZER), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        flags = set(__import__("re").findall(r"--[a-z][a-z-]*", completed.stdout))
        self.assertEqual(flags, {"--help", "--root", "--charter", "--ledger", "--draft"})

    def test_compose_draft_resolves_nested_and_splice_markers_before_finalization(
        self,
    ) -> None:
        """Regression: stable 15KB+ input is serialized before the terminal delta."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation, delta = write_draft_composition_inputs(run_root)
            self.assertGreater(preparation.stat().st_size, 15_000)
            self.assertLess(delta.stat().st_size, preparation.stat().st_size)
            self.assertFalse((run_root / "draft.json").exists())

            completed = invoke_draft_composer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.splitlines()[0], "draft_composition=PASS"
            )
            draft = _read_json(run_root / "draft.json")
            self.assertEqual(draft["rule_applicability"][0]["outcome"], "satisfied")
            self.assertEqual(
                draft["exploration"]["observations"][-1],
                "The final consumer observation passed.",
            )
            self.assertEqual(draft["findings"], [])
            self.assertEqual(draft["intended_result"], "PASS")
            self.assertNotIn("$test_pending", (run_root / "draft.json").read_text())
            self.assertFalse((run_root / "terminal").exists())
            finalized = invoke_finalizer(run_root)
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            self.assertIn("terminal_preflight=PASS", finalized.stdout)

    def test_compose_draft_expands_compact_rule_assessments_in_catalog_order(
        self,
    ) -> None:
        """The agent authors grouped active judgments, not 28 duplicated records."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation, _ = write_compact_draft_composition_inputs(run_root)
            self.assertLess(preparation.stat().st_size, 5_000)

            completed = invoke_draft_composer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            draft = _read_json(run_root / "draft.json")
            catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
            rules = draft["rule_applicability"]
            self.assertEqual(
                [item["rule_id"] for item in rules],
                [item["id"] for item in catalog["rules"]],
            )
            universal = [
                item for item in rules if item["rule_id"].startswith("universal.")
            ]
            conditional = [
                item for item in rules if not item["rule_id"].startswith("universal.")
            ]
            self.assertTrue(universal)
            self.assertTrue(
                all(
                    item == {
                        "rule_id": item["rule_id"],
                        "status": "active",
                        "outcome": "satisfied",
                        "evidence": ["ledger:check-consumer"],
                    }
                    for item in universal
                )
            )
            self.assertTrue(
                all(
                    item["status"] == "inactive"
                    and item["outcome"] == "not-applicable"
                    and item["evidence"]
                    for item in conditional
                )
            )
            finalized = invoke_finalizer(run_root)
            self.assertEqual(finalized.returncode, 0, finalized.stdout)

    def test_compose_draft_accepts_complete_marker_free_conditional_candidate(
        self,
    ) -> None:
        """V3 removes marker bookkeeping without weakening semantic preflight."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation, delta = write_direct_draft_composition_inputs(run_root)

            self.assertLess(preparation.stat().st_size, 5_000)
            self.assertLess(delta.stat().st_size, 100)
            completed = invoke_draft_composer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            draft = _read_json(run_root / "draft.json")
            self.assertEqual(draft["intended_result"], "PASS")
            self.assertNotIn("$test_pending", (run_root / "draft.json").read_text())
            self.assertTrue(draft["rule_applicability"])
            finalized = invoke_finalizer(run_root)
            self.assertEqual(finalized.returncode, 0, finalized.stdout)

    def test_direct_conditional_candidate_rejects_markers_and_nonempty_delta(
        self,
    ) -> None:
        mutations = (
            (
                "marker",
                lambda preparation, delta: preparation["draft_candidate"].update(
                    {"summary": {"$test_pending": "late-summary"}}
                ),
                "invalid-preparation",
            ),
            (
                "resolution",
                lambda preparation, delta: delta["resolutions"].append(
                    {"resolution_id": "unused", "value": "late"}
                ),
                "invalid-delta",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                preparation_path, delta_path = write_direct_draft_composition_inputs(
                    run_root
                )
                preparation = _read_json(preparation_path)
                delta = _read_json(delta_path)
                mutate(preparation, delta)
                _write_json(preparation_path, preparation)
                _write_json(delta_path, delta)

                completed = invoke_draft_composer(run_root)

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(expected_code, completed.stdout)
                self.assertFalse((run_root / "draft.json").exists())

    def test_compact_rule_assessments_fail_closed(self) -> None:
        """Compactness cannot omit, duplicate, invent, or activate rules implicitly."""

        mutations = (
            (
                "missing-active-rule",
                lambda value: value["rule_assessment_groups"][0]["rule_ids"].pop(),
                "missing-rule-assessment",
            ),
            (
                "unknown-condition",
                lambda value: value["active_rule_conditions"].append("invented"),
                "unknown-rule-condition",
            ),
            (
                "inactive-rule-assessed",
                lambda value: value["rule_assessment_groups"][0][
                    "rule_ids"
                ].append("ui.semantic-interaction"),
                "inactive-rule-assessment",
            ),
            (
                "duplicate-rule",
                lambda value: value["rule_assessment_groups"].append(
                    copy.deepcopy(value["rule_assessment_groups"][0])
                ),
                "duplicate-rule-assessment",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                preparation_path, _ = write_compact_draft_composition_inputs(run_root)
                preparation = _read_json(preparation_path)
                mutate(preparation)
                _write_json(preparation_path, preparation)

                completed = invoke_draft_composer(run_root)

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(expected_code, completed.stdout)
                self.assertFalse((run_root / "draft.json").exists())

    def test_compact_rule_assessments_activate_conditions_and_bind_ledger_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_compact_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
            ui_rule_ids = [
                rule["id"]
                for rule in catalog["rules"]
                if "ui-material" in rule["applies_when"]
            ]
            preparation["active_rule_conditions"] = ["ui-material"]
            preparation["rule_assessment_groups"].append(
                {
                    "rule_ids": ui_rule_ids,
                    "outcome": "satisfied",
                    "evidence_action_ids": ["check-consumer"],
                }
            )
            _write_json(preparation_path, preparation)

            completed = invoke_draft_composer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            draft = _read_json(run_root / "draft.json")
            active_ids = {
                rule["rule_id"]
                for rule in draft["rule_applicability"]
                if rule["status"] == "active"
            }
            self.assertTrue(set(ui_rule_ids).issubset(active_ids))

    def test_compact_rule_assessments_accept_redundant_always_condition(self) -> None:
        """Regression: `always` is an implicit condition, not an authoring trap."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_direct_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            preparation["active_rule_conditions"] = ["always"]
            _write_json(preparation_path, preparation)

            completed = invoke_draft_composer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            draft = _read_json(run_root / "draft.json")
            active_ids = {
                rule["rule_id"]
                for rule in draft["rule_applicability"]
                if rule["status"] == "active"
            }
            self.assertIn("universal.flake-honesty", active_ids)

    def test_compact_rule_evidence_must_reference_a_retained_ledger_action(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_compact_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            preparation["rule_assessment_groups"][0]["evidence_action_ids"] = [
                "missing-action"
            ]
            _write_json(preparation_path, preparation)

            completed = invoke_draft_composer(run_root)

            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("unknown-reference", completed.stdout)
            self.assertFalse((run_root / "draft.json").exists())

    def test_compact_exempt_disposition_expands_no_quality_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary), "EXEMPT")
            write_compact_draft_composition_inputs(run_root)

            composed = invoke_draft_composer(run_root)
            finalized = invoke_finalizer(run_root)

            self.assertEqual(composed.returncode, 0, composed.stdout)
            self.assertEqual(finalized.returncode, 0, finalized.stdout)
            self.assertEqual(
                _read_json(run_root / "terminal" / "draft.json")[
                    "rule_applicability"
                ],
                [],
            )

    def test_non_exempt_evidence_rejects_more_than_eight_semantic_actions(
        self,
    ) -> None:
        """A run cannot turn discoverable commands into an unbounded charter."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_ledger(run_root, statuses=("pass",) * 9)

            payload = self._assert_failure(
                run_root, code="action-budget-exceeded"
            )

            self.assertIn(
                "ledger.entries",
                {error["path"] for error in payload["errors"]},
            )

    def test_non_exempt_evidence_accepts_eight_semantic_actions(self) -> None:
        """Seven ordinary observations plus one final repetition remain bounded."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_ledger(run_root, statuses=("pass",) * 8)

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("terminal_preflight=PASS", completed.stdout)

    def test_large_draft_composes_finalizes_and_serializes_response_within_sprint(
        self,
    ) -> None:
        """Regression: the real helper path leaves ample room inside 60 seconds."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation, _ = write_draft_composition_inputs(run_root)
            self.assertGreater(preparation.stat().st_size, 15_000)
            charter = _read_json(run_root / "charter.json")
            started = time.monotonic()

            composed = invoke_draft_composer(run_root, timeout=60.0)
            self.assertEqual(composed.returncode, 0, composed.stderr)
            finalized = invoke_finalizer(run_root, timeout=60.0)
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            payload = json.loads(finalized.stdout.splitlines()[1])
            response = {
                "schema_version": "test-evaluator-response.v1",
                "terminal_state": payload["result"],
                "summary": payload["summary"],
                "scope": charter["scope"],
                "evidence": [
                    {
                        "kind": "probe",
                        "bundle_digest": payload["bundle_digest"],
                        "bundle_path": payload["bundle_path"],
                        "receipt_path": payload["receipt_path"],
                    }
                ],
                "limitations": [],
            }
            serialized = json.dumps(response, separators=(",", ":"), sort_keys=True)
            elapsed = time.monotonic() - started

            self.assertEqual(
                set(json.loads(serialized)),
                {
                    "schema_version",
                    "terminal_state",
                    "summary",
                    "scope",
                    "evidence",
                    "limitations",
                },
            )
            self.assertEqual(response["terminal_state"], "PASS")
            self.assertLess(elapsed, 60.0, f"terminal helper path took {elapsed:.3f}s")

    def test_compose_draft_authenticates_the_canonical_root_from_frozen_charter(
        self,
    ) -> None:
        """Regression: any private directory is not an authenticated run root."""

        cases = ("missing", "malformed", "wrong-schema", "wrong-run", "wrong-repository")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                run_root = self._prepare_run(repository)
                preparation, delta = write_draft_composition_inputs(run_root)
                target_root = run_root
                if case == "missing":
                    target_root = repository / "arbitrary-private-root"
                    target_root.mkdir(mode=0o700)
                    (target_root / preparation.name).write_bytes(preparation.read_bytes())
                    (target_root / delta.name).write_bytes(delta.read_bytes())
                elif case == "malformed":
                    (run_root / "charter.json").write_text("{", encoding="utf-8")
                else:
                    charter = _read_json(run_root / "charter.json")
                    if case == "wrong-schema":
                        charter["schema_version"] = "test-charter.v2"
                    elif case == "wrong-run":
                        charter["run_id"] = "another-run"
                    else:
                        charter["repository"] = str(repository / "another-repository")
                    _write_json(run_root / "charter.json", charter)

                completed = invoke_draft_composer(target_root)

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout)["status"], "ERROR")
                self.assertFalse((target_root / "draft.json").exists())
                self.assertFalse(any(target_root.glob(".draft-*")))

    def test_compose_draft_rejects_symlinked_or_substituted_charter_binding(
        self,
    ) -> None:
        """Regression: binding authentication survives charter path substitution."""

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            run_root = self._prepare_run(repository)
            write_draft_composition_inputs(run_root)
            real_charter = repository / "real-charter.json"
            (run_root / "charter.json").rename(real_charter)
            (run_root / "charter.json").symlink_to(real_charter)

            completed = invoke_draft_composer(run_root)

            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertFalse((run_root / "draft.json").exists())

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_draft_composition_inputs(run_root)
            original = finalizer._read_regular_snapshot_at
            substituted = False

            def read_then_substitute(
                root_descriptor: int, relative: Path, *, code: str
            ):
                nonlocal substituted
                value = original(root_descriptor, relative, code=code)
                if relative == Path("charter.json") and not substituted:
                    substituted = True
                    os.rename(
                        "charter.json",
                        "charter-original.json",
                        src_dir_fd=root_descriptor,
                        dst_dir_fd=root_descriptor,
                    )
                    descriptor = os.open(
                        "charter.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=root_descriptor,
                    )
                    try:
                        os.write(descriptor, b"{}")
                    finally:
                        os.close(descriptor)
                return value

            arguments = [
                "compose-draft",
                "--root",
                str(run_root),
                "--preparation",
                str(run_root / "draft-preparation.json"),
                "--delta",
                str(run_root / "draft-final-delta.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer,
                "_read_regular_snapshot_at",
                side_effect=read_then_substitute,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertTrue(substituted)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "draft.json").exists())
            self.assertFalse(any(run_root.glob(".draft-*")))
            self.assertEqual((run_root / "charter.json").read_bytes(), b"{}")

    def test_compose_draft_rejects_ledger_substitution_after_semantic_preflight(
        self,
    ) -> None:
        """The published candidate must remain bound to the ledger it validated."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_draft_composition_inputs(run_root)
            original = finalizer._read_regular_snapshot_at
            substituted = False

            def read_then_substitute(
                root_descriptor: int, relative: Path, *, code: str
            ):
                nonlocal substituted
                value = original(root_descriptor, relative, code=code)
                if relative == Path("ledger.json") and not substituted:
                    substituted = True
                    os.rename(
                        "ledger.json",
                        "ledger-original.json",
                        src_dir_fd=root_descriptor,
                        dst_dir_fd=root_descriptor,
                    )
                    descriptor = os.open(
                        "ledger.json",
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=root_descriptor,
                    )
                    try:
                        os.write(descriptor, b"{}")
                    finally:
                        os.close(descriptor)
                return value

            arguments = [
                "compose-draft",
                "--root",
                str(run_root),
                "--preparation",
                str(run_root / "draft-preparation.json"),
                "--delta",
                str(run_root / "draft-final-delta.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer,
                "_read_regular_snapshot_at",
                side_effect=read_then_substitute,
            ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertTrue(substituted)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("path-race", stdout.getvalue())
            self.assertFalse((run_root / "draft.json").exists())
            self.assertFalse(any(run_root.glob(".draft-*")))

    def test_compose_draft_rejects_unresolved_unused_duplicate_and_nested_markers(
        self,
    ) -> None:
        """Regression: the delta is an exact resolution set, not a scratch payload."""

        mutations = (
            (
                "missing-resolution",
                lambda preparation, delta: delta["resolutions"].pop(),
                "unresolved-marker",
            ),
            (
                "unused-resolution",
                lambda preparation, delta: delta["resolutions"].append(
                    {"resolution_id": "unused", "value": "not referenced"}
                ),
                "unused-resolution",
            ),
            (
                "duplicate-resolution",
                lambda preparation, delta: delta["resolutions"].append(
                    copy.deepcopy(delta["resolutions"][0])
                ),
                "duplicate-resolution",
            ),
            (
                "marker-in-delta",
                lambda preparation, delta: delta["resolutions"][0].update(
                    {"value": {"$test_pending": "nested"}}
                ),
                "invalid-resolution",
            ),
            (
                "malformed-marker",
                lambda preparation, delta: preparation["draft_template"][
                    "rule_applicability"
                ][0].update(
                    {
                        "outcome": {
                            "$test_pending": "final-rule-outcome",
                            "guessed": "satisfied",
                        }
                    }
                ),
                "invalid-preparation",
            ),
        )
        for name, mutate, expected_code in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                preparation_path, delta_path = write_draft_composition_inputs(run_root)
                preparation = _read_json(preparation_path)
                delta = _read_json(delta_path)
                mutate(preparation, delta)
                _write_json(preparation_path, preparation)
                _write_json(delta_path, delta)

                completed = invoke_draft_composer(run_root)

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(expected_code, completed.stdout)
                self.assertFalse((run_root / "draft.json").exists())

    def test_compose_draft_requires_final_judgments_to_remain_pending(self) -> None:
        """Regression: preparation cannot predeclare the final result or summary."""

        for field, guessed in (("summary", "It passed."), ("intended_result", "PASS")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                preparation_path, _ = write_draft_composition_inputs(run_root)
                preparation = _read_json(preparation_path)
                preparation["draft_template"][field] = guessed
                _write_json(preparation_path, preparation)

                completed = invoke_draft_composer(run_root)

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn("required-pending-judgment", completed.stdout)
                self.assertFalse((run_root / "draft.json").exists())

    def test_compose_draft_refuses_external_inputs_and_an_existing_draft(self) -> None:
        """Regression: composition is run-bound and never rewrites authority."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation, _ = write_draft_composition_inputs(run_root)
            external = run_root.parent / "external-preparation.json"
            external.write_bytes(preparation.read_bytes())
            outside = invoke_draft_composer(run_root, preparation=external)
            self.assertNotEqual(outside.returncode, 0, outside.stdout)
            self.assertIn("invalid-input-path", outside.stdout)
            self.assertFalse((run_root / "draft.json").exists())

            (run_root / "draft.json").write_text("sentinel\n", encoding="utf-8")
            existing = invoke_draft_composer(run_root)
            self.assertNotEqual(existing.returncode, 0, existing.stdout)
            self.assertIn("draft-exists", existing.stdout)
            self.assertEqual(
                (run_root / "draft.json").read_text(encoding="utf-8"),
                "sentinel\n",
            )

    def test_compose_draft_preflights_supplied_semantics_without_inference(self) -> None:
        """Invalid supplied judgment must fail before the final product action."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            _, delta_path = write_draft_composition_inputs(run_root)
            delta = _read_json(delta_path)
            for resolution in delta["resolutions"]:
                if resolution["resolution_id"] == "final-rule-outcome":
                    resolution["value"] = "unknown"
            _write_json(delta_path, delta)

            composed = invoke_draft_composer(run_root)
            self.assertNotEqual(composed.returncode, 0, composed.stdout)
            self.assertIn("unknown-state", composed.stdout)
            self.assertFalse((run_root / "draft.json").exists())

    def test_compose_draft_rejects_invalid_stable_shape_before_publication(
        self,
    ) -> None:
        """Regression: stable malformed arrays cannot survive until terminalization."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            preparation["draft_template"]["exploration"]["teardown"] = "not-required"
            _write_json(preparation_path, preparation)

            composed = invoke_draft_composer(run_root)

            self.assertNotEqual(composed.returncode, 0, composed.stdout)
            self.assertIn("draft.exploration.teardown", composed.stdout)
            self.assertIn("invalid-type", composed.stdout)
            self.assertFalse((run_root / "draft.json").exists())

    def test_compose_draft_allows_only_terminal_finalizer_to_require_future_artifact_bytes(
        self,
    ) -> None:
        """A declared final-action artifact may be absent until that action captures it."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            preparation["draft_template"]["artifacts"].insert(
                -1,
                {
                    "artifact_id": "future-final-observation",
                    "kind": "reproduction",
                    "path": "artifacts/future-final-observation.json",
                },
            )
            _write_json(preparation_path, preparation)

            composed = invoke_draft_composer(run_root)
            finalized = invoke_finalizer(run_root)

            self.assertEqual(composed.returncode, 0, composed.stdout)
            self.assertNotEqual(finalized.returncode, 0, finalized.stdout)
            self.assertIn("invalid-artifact-path", finalized.stdout)

    def test_compose_draft_rejects_root_as_a_future_artifact_path(self) -> None:
        """A deferred artifact still needs a lexical file path beneath the root."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            preparation_path, _ = write_draft_composition_inputs(run_root)
            preparation = _read_json(preparation_path)
            preparation["draft_template"]["artifacts"][0]["path"] = "."
            _write_json(preparation_path, preparation)

            composed = invoke_draft_composer(run_root)

            self.assertNotEqual(composed.returncode, 0, composed.stdout)
            self.assertIn("invalid-artifact-path", composed.stdout)
            self.assertFalse((run_root / "draft.json").exists())

    def test_compose_draft_fsync_failure_quarantines_but_never_path_unlinks_draft(
        self,
    ) -> None:
        """Regression: failed durability removes authority without unsafe deletion."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_draft_composition_inputs(run_root)
            real_fsync = finalizer.os.fsync
            directory_fsyncs: list[int] = []

            def fail_directory_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(finalizer.os.fstat(descriptor).st_mode):
                    directory_fsyncs.append(descriptor)
                    raise OSError("injected directory fsync failure")
                real_fsync(descriptor)

            arguments = [
                "compose-draft",
                "--root",
                str(run_root),
                "--preparation",
                str(run_root / "draft-preparation.json"),
                "--delta",
                str(run_root / "draft-final-delta.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ), mock.patch.object(finalizer.os, "fsync", side_effect=fail_directory_fsync):
                return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertTrue(directory_fsyncs)
            self.assertFalse((run_root / "draft.json").exists())
            quarantines = list(run_root.glob(".draft-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o600)
            self.assertIn("publication-failed", stdout.getvalue())
            self.assertIn("cleanup-failed", stdout.getvalue())

    def test_compose_draft_root_race_quarantines_but_never_path_unlinks_draft(
        self,
    ) -> None:
        """Regression: root drift cannot leave an authoritative or unsafely deleted draft."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            write_draft_composition_inputs(run_root)
            calls = 0

            def fail_after_publication(_root: Path, _descriptor: int) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise finalizer.FinalizerError(
                        "path-race", str(run_root), "injected root replacement"
                    )

            arguments = [
                "compose-draft",
                "--root",
                str(run_root),
                "--preparation",
                str(run_root / "draft-preparation.json"),
                "--delta",
                str(run_root / "draft-final-delta.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                stderr
            ), mock.patch.object(
                finalizer, "_revalidate_run_root", side_effect=fail_after_publication
            ):
                return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(calls, 2)
            self.assertFalse((run_root / "draft.json").exists())
            quarantines = list(run_root.glob(".draft-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o600)
            self.assertIn("path-race", stdout.getvalue())
            self.assertIn("cleanup-failed", stdout.getvalue())

    def test_draft_cleanup_never_unlinks_a_post_authentication_substitute(
        self,
    ) -> None:
        """Regression: inode proof cannot authorize a later pathname unlink."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run-001"
            run_root.mkdir(mode=0o700)
            draft = run_root / "draft.json"
            draft.write_bytes(b"owned draft")
            draft.chmod(0o600)
            owned = draft.stat()
            _, root_descriptor = finalizer._open_run_root(run_root)
            real_open_beneath = finalizer._open_beneath
            moved_owned = run_root / ".attacker-moved-owned-draft"
            substituted: Path | None = None

            def open_then_substitute(root_fd, relative, *, directory):
                nonlocal substituted
                descriptor = real_open_beneath(
                    root_fd, relative, directory=directory
                )
                if (
                    substituted is None
                    and relative.name.startswith(".draft-cleanup-")
                ):
                    finalizer.os.rename(
                        relative.name,
                        moved_owned.name,
                        src_dir_fd=root_fd,
                        dst_dir_fd=root_fd,
                    )
                    replacement = run_root / relative.name
                    replacement.write_bytes(b"attacker substitute")
                    replacement.chmod(0o600)
                    substituted = replacement
                return descriptor

            try:
                with mock.patch.object(
                    finalizer,
                    "_open_beneath",
                    side_effect=open_then_substitute,
                ):
                    issues = finalizer._cleanup_owned_draft_file(
                        root_descriptor, "draft.json", owned
                    )
            finally:
                finalizer.os.close(root_descriptor)

            self.assertIsNotNone(substituted)
            assert substituted is not None
            self.assertFalse(draft.exists())
            self.assertEqual(moved_owned.read_bytes(), b"owned draft")
            self.assertTrue(
                substituted.exists(),
                "cleanup deleted a pathname substitute after inode authentication",
            )
            self.assertEqual(substituted.read_bytes(), b"attacker substitute")
            self.assertIn("cleanup-failed", {issue.code for issue in issues})

    def test_draft_cleanup_reports_lost_ownership_when_quarantine_rename_fails(
        self,
    ) -> None:
        """Regression: a missing canonical name does not prove inode removal."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run-001"
            run_root.mkdir(mode=0o700)
            draft = run_root / "draft.json"
            draft.write_bytes(b"owned draft")
            draft.chmod(0o600)
            owned = draft.stat()
            _, root_descriptor = finalizer._open_run_root(run_root)
            moved_owned = run_root / ".attacker-moved-owned-draft"

            def rename_away_then_fail(source, _target, **kwargs) -> None:
                finalizer.os.rename(
                    source,
                    moved_owned.name,
                    src_dir_fd=kwargs["source_dir_fd"],
                    dst_dir_fd=kwargs["target_dir_fd"],
                )
                raise finalizer.FinalizerError(
                    "publication-failed",
                    str(source),
                    "injected quarantine rename failure",
                )

            try:
                with mock.patch.object(
                    finalizer,
                    "_rename_noreplace_raw",
                    side_effect=rename_away_then_fail,
                ):
                    issues = finalizer._cleanup_owned_draft_file(
                        root_descriptor, "draft.json", owned
                    )
            finally:
                finalizer.os.close(root_descriptor)

            self.assertFalse(draft.exists())
            self.assertEqual(moved_owned.read_bytes(), b"owned draft")
            self.assertIn("cleanup-failed", {issue.code for issue in issues})

    def test_finalizer_rejects_finding_kind_drift_between_runtime_and_contract(
        self,
    ) -> None:
        """Regression: fixed authoring enums and the packaged contract share authority."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            ledger = _read_json(run_root / "ledger.json")
            draft = _read_json(run_root / "draft.json")
            catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
            for malformed_kinds in (
                ["product-defect"],
                [{}, {}, {}, {}],
                "product-defect",
            ):
                with self.subTest(malformed_kinds=malformed_kinds):
                    contract = json.loads(
                        EVIDENCE_CONTRACT.read_text(encoding="utf-8")
                    )
                    contract["finding_kinds"] = malformed_kinds

                    issues = finalizer.validate_inputs(
                        run_root.resolve(),
                        charter,
                        ledger,
                        draft,
                        catalog=catalog,
                        contract=contract,
                    )

                    self.assertTrue(
                        any(
                            issue.code == "invalid-resource"
                            and issue.path == "contract.finding_kinds"
                            for issue in issues
                        ),
                        [(issue.code, issue.path) for issue in issues],
                    )

    def test_rejects_duplicate_ids_unknown_references_wrong_head_and_unknown_state(
        self,
    ) -> None:
        mutations = (
            ("duplicate-id", lambda ledger: ledger["entries"].append(dict(ledger["entries"][0]))),
            ("unknown-reference", lambda ledger: ledger["entries"][0]["oracle_ids"].append("missing-oracle")),
            ("wrong-head", lambda ledger: ledger["entries"][0].__setitem__("head", "f" * 40)),
            ("unknown-state", lambda ledger: ledger["entries"][0].__setitem__("status", "maybe")),
        )
        for code, mutate in mutations:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                ledger = _read_json(run_root / "ledger.json")
                mutate(ledger)
                _write_json(run_root / "ledger.json", ledger)
                self._assert_failure(run_root, code=code)

    def test_rejects_missing_escape_absolute_and_symlinked_artifacts(self) -> None:
        for case in ("missing", "escape", "absolute", "symlink"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                repository = Path(temporary)
                run_root = self._prepare_run(repository)
                draft = _read_json(run_root / "draft.json")
                declaration = draft["artifacts"][0]
                if case == "missing":
                    declaration["path"] = "artifacts/missing.log"
                elif case == "escape":
                    (run_root.parent / "escape.log").write_text("outside", encoding="utf-8")
                    declaration["path"] = "../escape.log"
                elif case == "absolute":
                    declaration["path"] = str(run_root / "artifacts" / "consumer.log")
                else:
                    target = run_root / "artifacts" / "consumer.log"
                    link = run_root / "artifacts" / "linked.log"
                    link.symlink_to(target)
                    declaration["path"] = "artifacts/linked.log"
                _write_json(run_root / "draft.json", draft)
                self._assert_failure(run_root, code="invalid-artifact-path")

    def test_rejects_stale_charter_digest_existing_terminal_and_stale_published_digests(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            ledger = _read_json(run_root / "ledger.json")
            ledger["charter_digest"] = "f" * 64
            _write_json(run_root / "ledger.json", ledger)
            self._assert_failure(run_root, code="stale-charter-digest")

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            terminal = run_root / "terminal"
            terminal.mkdir()
            sentinel = terminal / "sentinel"
            sentinel.write_text("immutable", encoding="utf-8")
            completed = invoke_finalizer(run_root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "immutable")
            self.assertEqual({path.name for path in terminal.iterdir()}, {"sentinel"})

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            self.assertEqual(invoke_finalizer(run_root).returncode, 0)
            bundle_path = run_root / "terminal" / "bundle.json"
            bundle = _read_json(bundle_path)
            bundle["bundle_digest"] = "f" * 64
            _write_json(bundle_path, bundle)
            finalizer = _load_finalizer_module()
            with self.assertRaises(finalizer.FinalizerError):
                finalizer.verify_published(run_root)

    def test_rejects_contradictory_fail_then_pass_and_missing_material_oracle_pass(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"] = [
                "check-consumer",
                "check-consumer-2",
            ]
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root, statuses=("fail", "pass"))
            self._assert_failure(run_root, code="contradictory-oracle")

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"] = []
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root, statuses=("pass",))
            self._assert_failure(run_root, code="missing-material-oracle")

    def test_missing_required_action_forbids_pass_without_becoming_unknown_reference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"].append(
                "planned-consumer-journey"
            )
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root)

            payload = self._assert_failure(run_root, code="unsupported-result")

            self.assertNotIn(
                "unknown-reference", {error["code"] for error in payload["errors"]}
            )

    def test_required_action_ids_report_every_unhashable_item_as_invalid_type(
        self,
    ) -> None:
        """Regression: malformed required actions remain validation evidence."""

        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"] = [
                {"unexpected": "object"},
                ["nested-array"],
            ]
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root)

            payload = self._assert_failure(run_root, code="invalid-type")

            self.assertEqual(
                {
                    error["path"]
                    for error in payload["errors"]
                    if error["code"] == "invalid-type"
                },
                {
                    "charter.material_oracles[0].required_action_ids[0]",
                    "charter.material_oracles[0].required_action_ids[1]",
                },
            )
            self.assertNotIn(
                "internal-error", {error["code"] for error in payload["errors"]}
            )

    def test_unhashable_reference_items_remain_explicit_validation_issues(
        self,
    ) -> None:
        """Regression: every validated reference array is safe to cross-check."""

        cases = (
            ("ledger-oracle", "PASS", "ledger.entries[0].oracle_ids[0]", {}),
            ("ledger-artifact", "PASS", "ledger.entries[0].artifact_ids[0]", []),
            (
                "finding-rule",
                "FAIL",
                "draft.findings[0].violated_rule_ids[0]",
                {},
            ),
            ("finding-artifact", "FAIL", "draft.findings[0].artifacts[0]", []),
            ("teardown-artifact", "PASS", "draft.teardown.artifact_ids[0]", {}),
        )
        for case, result, expected_path, invalid_value in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary), result)
                if case.startswith("ledger-"):
                    value = _read_json(run_root / "ledger.json")
                    field = "oracle_ids" if case == "ledger-oracle" else "artifact_ids"
                    value["entries"][0][field] = [invalid_value]
                    _write_json(run_root / "ledger.json", value)
                else:
                    value = _read_json(run_root / "draft.json")
                    if case == "finding-rule":
                        value["findings"][0]["violated_rule_ids"] = [invalid_value]
                    elif case == "finding-artifact":
                        value["findings"][0]["artifacts"] = [invalid_value]
                    else:
                        value["teardown"]["artifact_ids"] = [invalid_value]
                    _write_json(run_root / "draft.json", value)

                payload = self._assert_failure(run_root, code="invalid-type")
                errors = payload["errors"]

                self.assertIn(
                    expected_path,
                    {
                        error["path"]
                        for error in errors
                        if error["code"] == "invalid-type"
                    },
                )
                self.assertNotIn(
                    "internal-error", {error["code"] for error in errors}
                )

    def test_unhashable_enum_values_remain_explicit_unknown_state_issues(
        self,
    ) -> None:
        """Regression: enum membership validation accepts arbitrary JSON safely."""

        cases = (
            ("entry-role", "PASS", "ledger.entries[0].role", {}),
            ("entry-ring", "PASS", "ledger.entries[0].ring", []),
            ("entry-status", "PASS", "ledger.entries[0].status", {}),
            ("rule-status", "PASS", "draft.rule_applicability[0].status", []),
            ("rule-outcome", "PASS", "draft.rule_applicability[0].outcome", {}),
            ("artifact-kind", "PASS", "draft.artifacts[0].kind", []),
            ("finding-kind", "FAIL", "draft.findings[0].kind", {}),
            ("finding-severity", "FAIL", "draft.findings[0].severity", []),
            ("finding-ring", "FAIL", "draft.findings[0].ring", {}),
            ("teardown-status", "PASS", "draft.teardown.status", []),
            ("intended-result", "PASS", "draft.intended_result", {}),
        )
        for case, result, expected_path, invalid_value in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary), result)
                target = "ledger.json" if case.startswith("entry-") else "draft.json"
                value = _read_json(run_root / target)
                if case == "entry-role":
                    value["entries"][0]["role"] = invalid_value
                elif case == "entry-ring":
                    value["entries"][0]["ring"] = invalid_value
                elif case == "entry-status":
                    value["entries"][0]["status"] = invalid_value
                elif case == "rule-status":
                    value["rule_applicability"][0]["status"] = invalid_value
                elif case == "rule-outcome":
                    value["rule_applicability"][0]["outcome"] = invalid_value
                elif case == "artifact-kind":
                    value["artifacts"][0]["kind"] = invalid_value
                elif case == "finding-kind":
                    value["findings"][0]["kind"] = invalid_value
                elif case == "finding-severity":
                    value["findings"][0]["severity"] = invalid_value
                elif case == "finding-ring":
                    value["findings"][0]["ring"] = invalid_value
                elif case == "teardown-status":
                    value["teardown"]["status"] = invalid_value
                else:
                    value["intended_result"] = invalid_value
                _write_json(run_root / target, value)

                payload = self._assert_failure(run_root, code="unknown-state")

                self.assertIn(
                    expected_path,
                    {
                        error["path"]
                        for error in payload["errors"]
                        if error["code"] == "unknown-state"
                    },
                )
                self.assertNotIn(
                    "internal-error", {error["code"] for error in payload["errors"]}
                )

    def test_missing_required_action_supports_blocked_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"].append(
                "planned-consumer-journey"
            )
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root)
            draft = _read_json(run_root / "draft.json")
            draft["intended_result"] = "BLOCKED"
            draft["summary"] = "A planned material action could not be completed."
            _write_json(run_root / "draft.json", draft)

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                _read_json(run_root / "terminal" / "bundle.json")["result"],
                "BLOCKED",
            )

    def test_missing_required_action_can_coexist_with_retained_defect_for_fail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary), "FAIL")
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"].append(
                "planned-consumer-journey"
            )
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root, statuses=("fail",))

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                _read_json(run_root / "terminal" / "bundle.json")["result"],
                "FAIL",
            )

    def test_missing_required_action_alone_does_not_support_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"].append(
                "planned-consumer-journey"
            )
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root)
            draft = _read_json(run_root / "draft.json")
            draft["intended_result"] = "FAIL"
            _write_json(run_root / "draft.json", draft)

            payload = self._assert_failure(run_root, code="unsupported-result")

            self.assertNotIn(
                "unknown-reference", {error["code"] for error in payload["errors"]}
            )

    def test_present_required_action_back_binds_and_retains_an_artifact(self) -> None:
        for case in ("missing-back-binding", "missing-artifact", "unknown-artifact"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                ledger = _read_json(run_root / "ledger.json")
                entry = ledger["entries"][0]
                if case == "missing-back-binding":
                    entry["oracle_ids"] = []
                    expected_code = "unknown-reference"
                elif case == "missing-artifact":
                    entry["artifact_ids"] = []
                    expected_code = "missing-value"
                else:
                    entry["artifact_ids"] = ["missing-artifact"]
                    expected_code = "unknown-reference"
                _write_json(run_root / "ledger.json", ledger)

                self._assert_failure(run_root, code=expected_code)

    def test_composition_inputs_cannot_be_declared_as_raw_artifacts(self) -> None:
        for filename in ("draft-preparation.json", "draft-final-delta.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                (run_root / filename).write_text("private composition input\n", encoding="utf-8")
                draft = _read_json(run_root / "draft.json")
                draft["artifacts"][0]["path"] = filename
                _write_json(run_root / "draft.json", draft)

                self._assert_failure(run_root, code="invalid-artifact-path")

    def test_contradiction_supports_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary), "FAIL")
            charter = _read_json(run_root / "charter.json")
            charter["material_oracles"][0]["required_action_ids"] = [
                "check-consumer",
                "check-consumer-2",
            ]
            _write_json(run_root / "charter.json", charter)
            write_ledger(run_root, statuses=("fail", "pass"))

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                _read_json(run_root / "terminal" / "bundle.json")["result"],
                "FAIL",
            )

    def test_pass_accepts_not_required_teardown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            draft = _read_json(run_root / "draft.json")
            draft["teardown"]["status"] = "not-required"
            _write_json(run_root / "draft.json", draft)

            completed = invoke_finalizer(run_root)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                _read_json(run_root / "terminal" / "bundle.json")["result"],
                "PASS",
            )

    def test_unrelated_failed_action_does_not_support_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            ledger = _read_json(run_root / "ledger.json")
            unrelated = dict(ledger["entries"][0])
            unrelated.update(
                {
                    "action_id": "unrelated-diagnostic",
                    "actual": "An unrelated diagnostic failed.",
                    "status": "fail",
                    "oracle_ids": [],
                }
            )
            ledger["entries"].append(unrelated)
            _write_json(run_root / "ledger.json", ledger)
            draft = _read_json(run_root / "draft.json")
            draft["intended_result"] = "FAIL"
            _write_json(run_root / "draft.json", draft)

            self._assert_failure(run_root, code="unsupported-result")

    def test_rejects_result_without_required_support(self) -> None:
        cases = (
            ("PASS", "finding", "unsupported-result"),
            ("FAIL", "all-pass", "unsupported-result"),
            ("BLOCKED", "all-pass", "unsupported-result"),
            ("EXEMPT", "has-actions", "invalid-exemption"),
            ("MAYBE", "unknown", "unknown-state"),
            ("FAIL", "unmatched-unsatisfied-rule", "unsupported-rule-outcome"),
        )
        for result, mutation, code in cases:
            with self.subTest(result=result, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                draft = _read_json(run_root / "draft.json")
                ledger = _read_json(run_root / "ledger.json")
                if mutation == "finding":
                    finding_draft = _read_json(write_draft(run_root, intended_result="FAIL"))
                    draft["findings"] = finding_draft["findings"]
                    draft["intended_result"] = "PASS"
                elif mutation == "all-pass":
                    draft["intended_result"] = result
                elif mutation == "has-actions":
                    draft["intended_result"] = "EXEMPT"
                    draft["rule_applicability"] = []
                elif mutation == "unmatched-unsatisfied-rule":
                    draft["intended_result"] = "FAIL"
                    draft["rule_applicability"][0]["outcome"] = "unsatisfied"
                    draft["findings"] = []
                else:
                    draft["intended_result"] = "MAYBE"
                _write_json(run_root / "draft.json", draft)
                _write_json(run_root / "ledger.json", ledger)
                self._assert_failure(run_root, code=code)

    def test_environment_blocker_cannot_be_laundered_into_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary), "BLOCKED")
            draft = _read_json(run_root / "draft.json")
            draft["intended_result"] = "FAIL"
            draft["findings"] = [
                {
                    "kind": "environment-blocker",
                    "severity": "high",
                    "ring": "inner",
                    "journey": "public CLI output",
                    "expected": "A credential-backed consumer observation.",
                    "actual": "The required credential is unavailable.",
                    "reproduction": ["Run the retained credential check."],
                    "violated_rule_ids": [],
                    "artifacts": ["consumer-log"],
                }
            ]
            _write_json(run_root / "draft.json", draft)

            self._assert_failure(run_root, code="unsupported-result")

    def test_rejects_invalid_calendar_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            draft = _read_json(run_root / "draft.json")
            draft["started_at"] = "2026-99-99T99:99:99Z"
            _write_json(run_root / "draft.json", draft)

            self._assert_failure(run_root, code="invalid-timestamp")

    def test_rejects_duplicate_rules_reserved_artifacts_and_symlinked_inputs(
        self,
    ) -> None:
        for mutation, code in (
            ("duplicate-rule", "duplicate-id"),
            ("reserved-artifact", "duplicate-id"),
            ("symlinked-input", "symlinked-path"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                draft = _read_json(run_root / "draft.json")
                if mutation == "duplicate-rule":
                    draft["rule_applicability"].append(
                        dict(draft["rule_applicability"][0])
                    )
                    _write_json(run_root / "draft.json", draft)
                elif mutation == "reserved-artifact":
                    draft["artifacts"][0]["artifact_id"] = "test-charter"
                    _write_json(run_root / "draft.json", draft)
                else:
                    target = run_root / "draft-real.json"
                    (run_root / "draft.json").rename(target)
                    (run_root / "draft.json").symlink_to(target)
                self._assert_failure(run_root, code=code)

    def test_publication_failure_leaves_no_terminal_or_partial_bundle_or_receipt(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer,
                "_rename_noreplace",
                side_effect=OSError("injected rename failure"),
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertNotEqual(return_code, 0)
            self.assertNotIn("terminal_preflight=PASS", stdout.getvalue())
            self.assertFalse((run_root / "terminal").exists())
            self.assertFalse((run_root / "bundle.json").exists())
            self.assertFalse((run_root / "receipt.json").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)
            self.assertIn("cleanup-failed", stdout.getvalue())
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer,
                "_write_private_file_at",
                side_effect=OSError("injected write failure"),
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)
            self.assertNotEqual(return_code, 0)
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)
            self.assertIn("cleanup-failed", stdout.getvalue())
            self.assertNotIn("terminal_preflight=PASS", stdout.getvalue())

    def test_input_swap_after_validation_is_rejected_without_following_symlink(
        self,
    ) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            run_root = self._prepare_run(repository)
            outside = repository / "outside-charter.json"
            outside.write_bytes((run_root / "charter.json").read_bytes())
            original = finalizer._validate_input_location
            swapped = False

            def validate_then_swap(path: Path, expected: Path) -> None:
                nonlocal swapped
                original(path, expected)
                if path.name == "charter.json" and not swapped:
                    path.unlink()
                    path.symlink_to(outside)
                    swapped = True

            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer, "_validate_input_location", side_effect=validate_then_swap
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "ERROR")
            self.assertIn(
                "symlinked-path", {error["code"] for error in payload["errors"]}
            )
            self.assertFalse((run_root / "terminal").exists())
            self.assertEqual(outside.read_bytes(), (run_root / "charter.json").read_bytes())

    def test_artifact_read_race_is_a_deterministic_failure(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            artifact = run_root / "artifacts" / "consumer.log"
            original = finalizer._validate_artifact_path
            removed = False

            def validate_then_remove(
                root: Path, relative: object, *, root_descriptor: int | None = None
            ):
                nonlocal removed
                result = original(
                    root, relative, root_descriptor=root_descriptor
                )
                if result[1] is None and not removed:
                    artifact.unlink()
                    removed = True
                return result

            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer, "_validate_artifact_path", side_effect=validate_then_remove
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            self.assertEqual(list(run_root.glob(".terminal-*")), [])

    def test_fifo_inputs_and_artifacts_fail_without_blocking(self) -> None:
        for target_kind in ("input", "artifact"):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as temporary:
                run_root = self._prepare_run(Path(temporary))
                target = (
                    run_root / "charter.json"
                    if target_kind == "input"
                    else run_root / "artifacts" / "consumer.log"
                )
                target.unlink()
                os.mkfifo(target)

                completed = invoke_finalizer(run_root, timeout=1.0)

                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout)["status"], "ERROR")
                self.assertFalse((run_root / "terminal").exists())
                self.assertEqual(list(run_root.glob(".terminal-*")), [])

    def test_nul_path_uses_the_deterministic_error_contract(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root) + "\0replacement",
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")

    def test_cleanup_race_uses_the_deterministic_error_contract(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            stdout = io.StringIO()
            stderr = io.StringIO()
            real_write = finalizer._write_private_file_at
            writes = 0

            def fail_write_after_one(
                directory_descriptor: int, filename: str, data: bytes
            ) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("injected write failure")
                real_write(directory_descriptor, filename, data)

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer,
                "_write_private_file_at",
                side_effect=fail_write_after_one,
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)
            self.assertIn("cleanup-failed", stdout.getvalue())

    def test_raced_terminal_is_not_clobbered(self) -> None:
        finalizer = _load_finalizer_module()
        self.assertTrue(hasattr(finalizer, "_rename_noreplace"))
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            terminal = run_root / "terminal"
            raced_inode: int | None = None
            real_rename = finalizer._rename_noreplace_raw

            def race_terminal(*args, **kwargs) -> None:
                nonlocal raced_inode
                terminal.mkdir(mode=0o700)
                raced_inode = terminal.stat().st_ino
                real_rename(*args, **kwargs)

            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer, "_rename_noreplace", side_effect=race_terminal
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(terminal.stat().st_ino, raced_inode)
            self.assertEqual(list(terminal.iterdir()), [])
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_root_swap_never_writes_the_replacement_and_cleans_owned_output(
        self,
    ) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            run_root = self._prepare_run(repository)
            moved_root = repository / "moved-run-root"
            sentinel_contents = b"replacement must stay untouched"
            real_rename = finalizer._rename_noreplace

            def swap_root_then_publish(*args, **kwargs) -> None:
                run_root.rename(moved_root)
                run_root.mkdir(mode=0o700)
                (run_root / "sentinel").write_bytes(sentinel_contents)
                real_rename(*args, **kwargs)

            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer,
                "_rename_noreplace",
                side_effect=swap_root_then_publish,
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertEqual(
                {path.name for path in run_root.iterdir()}, {"sentinel"}
            )
            self.assertEqual((run_root / "sentinel").read_bytes(), sentinel_contents)
            self.assertFalse((moved_root / "terminal").exists())
            quarantines = list(moved_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_staging_is_fully_verified_before_atomic_exposure(self) -> None:
        finalizer = _load_finalizer_module()
        self.assertTrue(hasattr(finalizer, "_rename_noreplace"))
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            original_write = finalizer._write_private_file_at

            def corrupt_staged_charter(
                directory_descriptor: int, filename: str, data: bytes
            ) -> None:
                original_write(directory_descriptor, filename, data)
                if filename == "charter.json":
                    descriptor = finalizer.os.open(
                        filename,
                        finalizer.os.O_WRONLY | finalizer.os.O_TRUNC,
                        dir_fd=directory_descriptor,
                    )
                    try:
                        finalizer.os.write(descriptor, b"{}")
                    finally:
                        finalizer.os.close(descriptor)

            stdout = io.StringIO()
            stderr = io.StringIO()
            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            with mock.patch.object(
                finalizer,
                "_write_private_file_at",
                side_effect=corrupt_staged_charter,
            ), mock.patch.object(finalizer, "_rename_noreplace") as rename:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertFalse(rename.called)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_staging_open_failure_and_unexpected_published_entry_are_cleaned(
        self,
    ) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            real_open = finalizer.os.open
            failed = False

            def fail_first_staging_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal failed
                if (
                    not failed
                    and isinstance(path, str)
                    and path.startswith(".terminal-")
                ):
                    failed = True
                    raise OSError("injected staging open failure")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer.os, "open", side_effect=fail_first_staging_open
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_staging_post_open_validation_failure_cleans_created_directory(
        self,
    ) -> None:
        for failure in ("stat", "inode-mismatch"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                finalizer = _load_finalizer_module()
                run_root = self._prepare_run(Path(temporary))
                other = run_root / "other-directory"
                other.mkdir()
                other_metadata = other.stat()
                real_stat = finalizer.os.stat
                staging_stats = 0

                def fail_staging_validation(path, *args, **kwargs):
                    nonlocal staging_stats
                    if (
                        isinstance(path, str)
                        and path.startswith(".terminal-")
                    ):
                        staging_stats += 1
                        if staging_stats == 2:
                            if failure == "stat":
                                raise OSError("injected post-open stat failure")
                            return other_metadata
                    return real_stat(path, *args, **kwargs)

                arguments = [
                    "--root",
                    str(run_root),
                    "--charter",
                    str(run_root / "charter.json"),
                    "--ledger",
                    str(run_root / "ledger.json"),
                    "--draft",
                    str(run_root / "draft.json"),
                ]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with mock.patch.object(
                    finalizer.os, "stat", side_effect=fail_staging_validation
                ):
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        return_code = finalizer.main(arguments)

                self.assertEqual(return_code, 1)
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
                self.assertFalse((run_root / "terminal").exists())
                quarantines = list(run_root.glob(".terminal-cleanup-*"))
                self.assertEqual(len(quarantines), 1)
                self.assertEqual(
                    stat.S_IMODE(quarantines[0].stat().st_mode), 0o700
                )

    def test_cleanup_quarantines_owned_inode_without_path_deletion(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run-001"
            run_root.mkdir(mode=0o700)
            terminal = run_root / "terminal"
            terminal.mkdir(mode=0o700)
            (terminal / "bundle.json").write_bytes(b"owned")
            root, root_descriptor = finalizer._open_run_root(run_root)
            self.assertEqual(root, run_root)
            owned = terminal.stat()
            real_rename = finalizer._rename_noreplace_raw
            substituted = False

            def quarantine_then_substitute(source, target, **kwargs) -> None:
                nonlocal substituted
                real_rename(source, target, **kwargs)
                if source == "terminal" and not substituted:
                    substituted = True
                    terminal.mkdir(mode=0o700)
                    (terminal / "sentinel").write_bytes(b"do not delete")

            try:
                with mock.patch.object(
                    finalizer,
                    "_rename_noreplace_raw",
                    side_effect=quarantine_then_substitute,
                ):
                    issues = finalizer._cleanup_owned_directory(
                        root_descriptor, "terminal", owned
                    )
            finally:
                finalizer.os.close(root_descriptor)

            self.assertIn("cleanup-failed", {issue.code for issue in issues})
            self.assertTrue(substituted)
            self.assertEqual((terminal / "sentinel").read_bytes(), b"do not delete")
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual((quarantines[0] / "bundle.json").read_bytes(), b"owned")
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_terminal_cleanup_never_removes_a_post_authentication_substitute(
        self,
    ) -> None:
        """Regression: an authenticated directory fd cannot authorize pathname deletion."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run-001"
            run_root.mkdir(mode=0o700)
            terminal = run_root / "terminal"
            terminal.mkdir(mode=0o700)
            (terminal / "bundle.json").write_bytes(b"owned")
            owned = terminal.stat()
            _, root_descriptor = finalizer._open_run_root(run_root)
            real_rename = finalizer._rename_noreplace_raw
            real_fsync = finalizer.os.fsync
            quarantine_name = ""
            moved_owned = run_root / ".attacker-moved-owned-terminal"
            substituted: Path | None = None

            def capture_quarantine(source, target, **kwargs) -> None:
                nonlocal quarantine_name
                real_rename(source, target, **kwargs)
                quarantine_name = target

            def substitute_after_authentication(descriptor: int) -> None:
                nonlocal substituted
                if (
                    descriptor != root_descriptor
                    and quarantine_name
                    and substituted is None
                ):
                    finalizer.os.rename(
                        quarantine_name,
                        moved_owned.name,
                        src_dir_fd=root_descriptor,
                        dst_dir_fd=root_descriptor,
                    )
                    finalizer.os.mkdir(
                        quarantine_name, mode=0o700, dir_fd=root_descriptor
                    )
                    substituted = run_root / quarantine_name
                real_fsync(descriptor)

            try:
                with (
                    mock.patch.object(
                        finalizer,
                        "_rename_noreplace_raw",
                        side_effect=capture_quarantine,
                    ),
                    mock.patch.object(
                        finalizer.os,
                        "fsync",
                        side_effect=substitute_after_authentication,
                    ),
                ):
                    issues = finalizer._cleanup_owned_directory(
                        root_descriptor, "terminal", owned
                    )
            finally:
                finalizer.os.close(root_descriptor)

            self.assertIsNotNone(substituted)
            assert substituted is not None
            self.assertTrue(
                substituted.exists(),
                "cleanup deleted a directory substituted after fd authentication",
            )
            self.assertTrue(moved_owned.is_dir())
            self.assertIn("cleanup-failed", {issue.code for issue in issues})

    def test_terminal_cleanup_reports_lost_ownership_when_quarantine_rename_fails(
        self,
    ) -> None:
        """Regression: a missing terminal name does not prove directory removal."""

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = Path(temporary) / "run-001"
            run_root.mkdir(mode=0o700)
            terminal = run_root / "terminal"
            terminal.mkdir(mode=0o700)
            (terminal / "bundle.json").write_bytes(b"owned")
            owned = terminal.stat()
            _, root_descriptor = finalizer._open_run_root(run_root)
            moved_owned = run_root / ".attacker-moved-owned-terminal"

            def rename_away_then_fail(source, _target, **kwargs) -> None:
                finalizer.os.rename(
                    source,
                    moved_owned.name,
                    src_dir_fd=kwargs["source_dir_fd"],
                    dst_dir_fd=kwargs["target_dir_fd"],
                )
                raise finalizer.FinalizerError(
                    "publication-failed",
                    str(source),
                    "injected quarantine rename failure",
                )

            try:
                with mock.patch.object(
                    finalizer,
                    "_rename_noreplace_raw",
                    side_effect=rename_away_then_fail,
                ):
                    issues = finalizer._cleanup_owned_directory(
                        root_descriptor, "terminal", owned
                    )
            finally:
                finalizer.os.close(root_descriptor)

            self.assertFalse(terminal.exists())
            self.assertEqual((moved_owned / "bundle.json").read_bytes(), b"owned")
            self.assertIn("cleanup-failed", {issue.code for issue in issues})

    def test_staging_open_substitution_is_never_deleted_as_owned(self) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            real_open = finalizer.os.open
            injected = False
            staging_name = ""

            def substitute_before_failed_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal injected, staging_name
                if (
                    not injected
                    and isinstance(path, str)
                    and path.startswith(".terminal-")
                ):
                    injected = True
                    staging_name = path
                    finalizer.os.rename(
                        path,
                        "attacker-moved-owned-staging",
                        src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd,
                    )
                    finalizer.os.mkdir(path, mode=0o700, dir_fd=dir_fd)
                    substitute_descriptor = real_open(
                        path,
                        finalizer.os.O_RDONLY | finalizer.os.O_DIRECTORY,
                        dir_fd=dir_fd,
                    )
                    try:
                        sentinel = real_open(
                            "sentinel",
                            finalizer.os.O_WRONLY
                            | finalizer.os.O_CREAT
                            | finalizer.os.O_EXCL,
                            0o600,
                            dir_fd=substitute_descriptor,
                        )
                        finalizer.os.close(sentinel)
                    finally:
                        finalizer.os.close(substitute_descriptor)
                    raise OSError("injected staging substitution")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer.os, "open", side_effect=substitute_before_failed_open
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertTrue(staging_name)
            self.assertFalse((run_root / staging_name).exists())
            self.assertTrue((run_root / "attacker-moved-owned-staging").is_dir())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertTrue((quarantines[0] / "sentinel").exists())

    def test_staging_open_returning_substitute_fd_preserves_the_substitute(
        self,
    ) -> None:
        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            real_open = finalizer.os.open
            injected = False
            staging_name = ""

            def return_substitute_descriptor(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal injected, staging_name
                if (
                    not injected
                    and isinstance(path, str)
                    and path.startswith(".terminal-")
                ):
                    injected = True
                    staging_name = path
                    finalizer.os.rename(
                        path,
                        "attacker-moved-owned-staging",
                        src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd,
                    )
                    finalizer.os.mkdir(path, mode=0o700, dir_fd=dir_fd)
                    substitute = real_open(
                        path,
                        finalizer.os.O_RDONLY | finalizer.os.O_DIRECTORY,
                        dir_fd=dir_fd,
                    )
                    sentinel = real_open(
                        "sentinel",
                        finalizer.os.O_WRONLY
                        | finalizer.os.O_CREAT
                        | finalizer.os.O_EXCL,
                        0o600,
                        dir_fd=substitute,
                    )
                    finalizer.os.close(sentinel)
                    return substitute
                return real_open(path, flags, mode, dir_fd=dir_fd)

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer.os,
                "open",
                side_effect=return_substitute_descriptor,
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertTrue(staging_name)
            self.assertFalse((run_root / staging_name).exists())
            self.assertTrue((run_root / "attacker-moved-owned-staging").is_dir())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertTrue((quarantines[0] / "sentinel").exists())

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            real_verify = finalizer._verify_output_at
            injected = False

            def inject_unexpected_staged_entry(*args, **kwargs) -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    output_descriptor = args[2]
                    descriptor = finalizer.os.open(
                        "unexpected",
                        finalizer.os.O_WRONLY
                        | finalizer.os.O_CREAT
                        | finalizer.os.O_EXCL,
                        0o600,
                        dir_fd=output_descriptor,
                    )
                    finalizer.os.close(descriptor)
                real_verify(*args, **kwargs)

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer,
                "_verify_output_at",
                side_effect=inject_unexpected_staged_entry,
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

        finalizer = _load_finalizer_module()
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            real_verify = finalizer._verify_output_at
            calls = 0

            def inject_unexpected_entry(*args, **kwargs) -> None:
                nonlocal calls
                calls += 1
                real_verify(*args, **kwargs)
                if calls == 2:
                    output_descriptor = args[2]
                    descriptor = finalizer.os.open(
                        "unexpected",
                        finalizer.os.O_WRONLY
                        | finalizer.os.O_CREAT
                        | finalizer.os.O_EXCL,
                        0o600,
                        dir_fd=output_descriptor,
                    )
                    finalizer.os.close(descriptor)
                    raise finalizer.FinalizerError(
                        "publication-failed",
                        "terminal",
                        "injected post-publication failure",
                    )

            arguments = [
                "--root",
                str(run_root),
                "--charter",
                str(run_root / "charter.json"),
                "--ledger",
                str(run_root / "ledger.json"),
                "--draft",
                str(run_root / "draft.json"),
            ]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(
                finalizer, "_verify_output_at", side_effect=inject_unexpected_entry
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    return_code = finalizer.main(arguments)

            self.assertEqual(return_code, 1)
            self.assertEqual(stderr.getvalue(), "")
            self.assertEqual(json.loads(stdout.getvalue())["status"], "ERROR")
            self.assertFalse((run_root / "terminal").exists())
            quarantines = list(run_root.glob(".terminal-cleanup-*"))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(stat.S_IMODE(quarantines[0].stat().st_mode), 0o700)

    def test_failure_reports_all_input_errors_once_without_success_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_root = self._prepare_run(Path(temporary))
            charter = _read_json(run_root / "charter.json")
            ledger = _read_json(run_root / "ledger.json")
            draft = _read_json(run_root / "draft.json")
            charter["unexpected"] = "closed"
            ledger["run_id"] = "wrong-run"
            ledger["entries"][0]["head"] = "f" * 40
            draft["intended_result"] = "MAYBE"
            draft["unexpected"] = "closed"
            _write_json(run_root / "charter.json", charter)
            _write_json(run_root / "ledger.json", ledger)
            _write_json(run_root / "draft.json", draft)

            payload = self._assert_failure(run_root)

            errors = payload["errors"]
            serialized = [json.dumps(error, sort_keys=True) for error in errors]
            self.assertEqual(serialized, sorted(serialized))
            self.assertEqual(len(serialized), len(set(serialized)))
            paths = {error["path"] for error in errors}
            self.assertIn("charter.unexpected", paths)
            self.assertIn("ledger.run_id", paths)
            self.assertIn("ledger.entries[0].head", paths)
            self.assertIn("draft.intended_result", paths)
            self.assertIn("draft.unexpected", paths)


if __name__ == "__main__":
    unittest.main()
