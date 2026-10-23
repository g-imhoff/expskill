from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate import validate_repository
from tests import test_test_evidence_finalizer as finalizer_fixtures


ROOT = Path(__file__).resolve().parents[1]
TEST_SKILL = ROOT / "plugins" / "expskill" / "content" / "skills" / "test"
CODEX_TEST_METADATA = (
    ROOT
    / "plugins"
    / "expskill"
    / "codex"
    / "skill-adapters"
    / "test"
    / "agents"
    / "openai.yaml"
)
QUALITY_CATALOG = TEST_SKILL / "references" / "quality-rules.json"
EVIDENCE_CONTRACT = TEST_SKILL / "references" / "evidence-contract.json"
FINALIZER = TEST_SKILL / "scripts" / "finalize_evidence.py"
FINAL_ACTION_RECORDER = TEST_SKILL / "scripts" / "record_final_action.py"
CHARTER_FREEZER = TEST_SKILL / "scripts" / "freeze_charter.py"
RUN_BOOTSTRAP = TEST_SKILL / "scripts" / "bootstrap_run.py"
LEDGER_APPENDER = TEST_SKILL / "scripts" / "append_ledger.py"
EXPECTED_RULE_IDS = {
    "universal.revision-and-oracle",
    "universal.consumer-outcome",
    "universal.isolated-data-and-cleanup",
    "universal.state-synchronization",
    "universal.real-composition",
    "universal.independent-tests",
    "universal.failure-diagnostics",
    "universal.safe-effects",
    "universal.flake-honesty",
    "durable-test.stable-regression-value",
    "ui.semantic-interaction",
    "ui.user-visible-assertion",
    "ui.runtime-observation",
    "persistence.production-equivalent-engine",
    "persistence.migrations-and-transactions",
    "persistence.independent-readback",
    "messaging.real-boundary",
    "messaging.eventual-outcome",
    "messaging.idempotency-and-ordering",
    "contract.consumer-provider-boundary",
    "contract.negative-compatibility",
    "external.sandboxed-service",
    "external.failure-and-recovery",
    "parallel.unique-data",
    "parallel.no-shared-workers",
    "accessibility.keyboard-and-semantics",
    "visual.representative-states",
    "visual.responsive-viewports",
}
VALID_RULE = {
    "id": "universal.example-rule",
    "level": "hard",
    "applies_when": ["always"],
    "requirement": "Exercise the observable consumer outcome.",
    "failure_prevented": "A false pass based only on implementation details.",
    "required_evidence": ["The observed consumer outcome."],
    "allowed_exceptions": [
        {
            "predicate": "The isolated runner reports that the consumer surface is unavailable.",
            "required_evidence": ["The runner identity and unavailability result."],
        }
    ],
}
VALID_CATALOG = {
    "schema_version": "test-quality-rules.v1",
    "rules": [VALID_RULE],
}

BUNDLE_FIELDS = (
    "schema_version",
    "run_id",
    "workflow_id",
    "repository",
    "branch",
    "head",
    "environment_digest",
    "scope",
    "rule_applicability",
    "checks",
    "journeys",
    "exploration",
    "findings",
    "artifacts",
    "teardown",
    "test_side_commits",
    "limitations",
    "result",
    "bundle_digest",
    "started_at",
    "completed_at",
)
RECEIPT_FIELDS = (
    "schema_version",
    "run_id",
    "workflow_id",
    "repository",
    "branch",
    "head",
    "environment_digest",
    "selected_scope_digest",
    "bundle_digest",
    "test_side_commits",
    "result",
)
FINDING_FIELDS = (
    "kind",
    "severity",
    "repository",
    "head",
    "environment_digest",
    "ring",
    "journey",
    "expected",
    "actual",
    "reproduction",
    "violated_rule_ids",
    "artifacts",
)
TERMINAL_STATES = ["PASS", "FAIL", "BLOCKED", "EXEMPT"]
FINDING_KINDS = [
    "product-defect",
    "test-system-defect",
    "environment-blocker",
    "unresolved-cause",
]
SHA256_PATTERN = "^[0-9a-f]{64}$"
HEAD_PATTERN = "^[0-9a-f]{40,64}$"
BUNDLE_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the complete "
    "bundle with only the bundle_digest field omitted"
)
ENVIRONMENT_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the complete "
    "recorded environment identity used for this run"
)
SCOPE_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the selected "
    "three-ring scope recorded in the retained bundle"
)


def _string_schema(
    *,
    constant: str | None = None,
    enum: list[str] | None = None,
    pattern: str | None = None,
    semantics: str | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {"type": "string", "min_length": 1}
    if constant is not None:
        schema["const"] = constant
    if enum is not None:
        schema["enum"] = enum
    if pattern is not None:
        schema["pattern"] = pattern
    if semantics is not None:
        schema["semantics"] = semantics
    return schema


def _nullable_workflow_schema() -> dict[str, object]:
    return {
        "type": ["string", "null"],
        "min_length": 1,
        "nullable_when": "direct invocation without Plan ancestry",
    }


def _array_schema(
    items: dict[str, object], *, min_items: int = 0, unique_items: bool = False
) -> dict[str, object]:
    return {
        "type": "array",
        "items": items,
        "min_items": min_items,
        "unique_items": unique_items,
    }


def _object_schema(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additional_properties": False,
        "required": list(properties),
        "properties": properties,
    }


ARTIFACT_SCHEMA = _object_schema(
    {
        "artifact_id": _string_schema(),
        "kind": _string_schema(
            enum=[
                "screenshot",
                "trace",
                "video",
                "log",
                "console",
                "network",
                "reproduction",
                "metadata",
            ]
        ),
        "path": _string_schema(),
        "sha256": _string_schema(
            pattern=SHA256_PATTERN,
            semantics="SHA-256 lowercase hex over the retained artifact bytes",
        ),
    }
)
FINDING_SCHEMA = _object_schema(
    {
        "kind": _string_schema(enum=FINDING_KINDS),
        "severity": _string_schema(enum=["critical", "high", "medium", "low"]),
        "repository": _string_schema(),
        "head": _string_schema(pattern=HEAD_PATTERN),
        "environment_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics=ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "ring": _string_schema(enum=["inner", "adjacent", "broader"]),
        "journey": _string_schema(),
        "expected": _string_schema(),
        "actual": _string_schema(),
        "reproduction": _array_schema(_string_schema(), min_items=1),
        "violated_rule_ids": _array_schema(
            _string_schema(pattern="^[a-z][a-z0-9.-]+$"), unique_items=True
        ),
        "artifacts": _array_schema(_string_schema(), unique_items=True),
    }
)
SCOPE_SCHEMA = _object_schema(
    {
        "accepted_behavior": _string_schema(),
        "inner_ring": _array_schema(_string_schema(), min_items=1, unique_items=True),
        "adjacent_ring": _array_schema(_string_schema(), unique_items=True),
        "broader_ring": _array_schema(_string_schema(), unique_items=True),
    }
)
RULE_APPLICABILITY_SCHEMA = _object_schema(
    {
        "rule_id": _string_schema(pattern="^[a-z][a-z0-9.-]+$"),
        "status": _string_schema(enum=["active", "inactive", "unknown"]),
        "evidence": _array_schema(_string_schema(), min_items=1),
    }
)
CHECK_SCHEMA = _object_schema(
    {
        "check_id": _string_schema(),
        "ring": _string_schema(enum=["inner", "adjacent", "broader"]),
        "action": _string_schema(),
        "expected": _string_schema(),
        "actual": _string_schema(),
        "status": _string_schema(enum=["pass", "fail", "blocked"]),
        "artifact_ids": _array_schema(_string_schema(), unique_items=True),
    }
)
JOURNEY_SCHEMA = _object_schema(
    {
        "journey_id": _string_schema(),
        "ring": _string_schema(enum=["inner", "adjacent", "broader"]),
        "path": _array_schema(_string_schema(), min_items=1),
        "expected": _string_schema(),
        "actual": _string_schema(),
        "status": _string_schema(enum=["pass", "fail", "blocked"]),
        "artifact_ids": _array_schema(_string_schema(), unique_items=True),
    }
)
EXPLORATION_SCHEMA = _object_schema(
    {
        "mission": _string_schema(),
        "evidence_budget": _string_schema(),
        "actions": _array_schema(_string_schema()),
        "observations": _array_schema(_string_schema()),
        "stop_condition": _string_schema(),
        "teardown": _array_schema(_string_schema()),
    }
)
TEARDOWN_SCHEMA = _object_schema(
    {
        "status": _string_schema(enum=["pass", "fail", "not-required"]),
        "actions": _array_schema(_string_schema()),
        "artifact_ids": _array_schema(_string_schema(), unique_items=True),
    }
)
BUNDLE_SCHEMA = _object_schema(
    {
        "schema_version": _string_schema(constant="test-evidence-bundle.v1"),
        "run_id": _string_schema(),
        "workflow_id": _nullable_workflow_schema(),
        "repository": _string_schema(),
        "branch": _string_schema(),
        "head": _string_schema(pattern=HEAD_PATTERN),
        "environment_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics=ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "scope": SCOPE_SCHEMA,
        "rule_applicability": _array_schema(RULE_APPLICABILITY_SCHEMA),
        "checks": _array_schema(CHECK_SCHEMA),
        "journeys": _array_schema(JOURNEY_SCHEMA),
        "exploration": EXPLORATION_SCHEMA,
        "findings": _array_schema(copy.deepcopy(FINDING_SCHEMA)),
        "artifacts": _array_schema(ARTIFACT_SCHEMA),
        "teardown": TEARDOWN_SCHEMA,
        "test_side_commits": _array_schema(
            _string_schema(pattern=HEAD_PATTERN), unique_items=True
        ),
        "limitations": _array_schema(_string_schema()),
        "result": _string_schema(enum=TERMINAL_STATES),
        "bundle_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics=BUNDLE_DIGEST_SEMANTICS,
        ),
        "started_at": _string_schema(
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z$"
        ),
        "completed_at": _string_schema(
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z$"
        ),
    }
)
RECEIPT_SCHEMA = _object_schema(
    {
        "schema_version": _string_schema(constant="test-evidence-receipt.v1"),
        "run_id": _string_schema(),
        "workflow_id": _nullable_workflow_schema(),
        "repository": _string_schema(),
        "branch": _string_schema(),
        "head": _string_schema(pattern=HEAD_PATTERN),
        "environment_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics=ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "selected_scope_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics=SCOPE_DIGEST_SEMANTICS,
        ),
        "bundle_digest": _string_schema(
            pattern=SHA256_PATTERN,
            semantics="Exact bundle_digest from the retained evidence bundle",
        ),
        "test_side_commits": _array_schema(
            _string_schema(pattern=HEAD_PATTERN), unique_items=True
        ),
        "result": _string_schema(enum=TERMINAL_STATES),
    }
)
VALID_EVIDENCE_CONTRACT = {
    "schema_version": "test-evidence-contract.v1",
    "terminal_states": TERMINAL_STATES,
    "finding_kinds": FINDING_KINDS,
    "bundle": BUNDLE_SCHEMA,
    "receipt": RECEIPT_SCHEMA,
    "finding": FINDING_SCHEMA,
}


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_finding() -> dict[str, object]:
    return {
        "kind": "product-defect",
        "severity": "high",
        "repository": "https://example.invalid/product.git",
        "head": "a" * 40,
        "environment_digest": "b" * 64,
        "ring": "inner",
        "journey": "create-and-read-item",
        "expected": "The created item is visible through an independent read path.",
        "actual": "The read path returns no item.",
        "reproduction": ["Create an owned item.", "Read it through the consumer surface."],
        "violated_rule_ids": ["universal.consumer-outcome"],
        "artifacts": ["failure-log"],
    }


def _valid_bundle() -> dict[str, object]:
    finding = _valid_finding()
    bundle: dict[str, object] = {
        "schema_version": "test-evidence-bundle.v1",
        "run_id": "run-20260902-001",
        "workflow_id": "workflow-123",
        "repository": "https://example.invalid/product.git",
        "branch": "feature/revision-bound-evidence",
        "head": "a" * 40,
        "environment_digest": "b" * 64,
        "scope": {
            "accepted_behavior": "Create and independently read an owned item.",
            "inner_ring": ["consumer create/read journey"],
            "adjacent_ring": ["persistence boundary"],
            "broader_ring": [],
        },
        "rule_applicability": [
            {
                "rule_id": "universal.consumer-outcome",
                "status": "active",
                "evidence": ["The accepted behavior has a consumer-visible result."],
            }
        ],
        "checks": [
            {
                "check_id": "focused-regression",
                "ring": "inner",
                "action": "Run the focused consumer regression.",
                "expected": "The consumer reads the created item.",
                "actual": "The consumer cannot read the created item.",
                "status": "fail",
                "artifact_ids": ["failure-log"],
            }
        ],
        "journeys": [
            {
                "journey_id": "create-and-read-item",
                "ring": "inner",
                "path": ["Create an owned item.", "Read it through the consumer surface."],
                "expected": "The created item is visible.",
                "actual": "No item is visible.",
                "status": "fail",
                "artifact_ids": ["failure-log"],
            }
        ],
        "exploration": {
            "mission": "Probe one adjacent persistence read path.",
            "evidence_budget": "One owned item and one independent read.",
            "actions": ["Read the owned item through the adjacent API."],
            "observations": ["The adjacent API also returns no item."],
            "stop_condition": "Stop after the anomaly is reproduced once safely.",
            "teardown": ["Remove the owned item namespace."],
        },
        "findings": [finding],
        "artifacts": [
            {
                "artifact_id": "failure-log",
                "kind": "log",
                "path": "artifacts/failure.log",
                "sha256": "c" * 64,
            }
        ],
        "teardown": {
            "status": "pass",
            "actions": ["Removed the owned item namespace."],
            "artifact_ids": [],
        },
        "test_side_commits": ["d" * 40],
        "limitations": ["No broader-ring behavior was selected."],
        "result": "FAIL",
        "bundle_digest": "pending",
        "started_at": "2026-09-02T10:00:00Z",
        "completed_at": "2026-09-02T10:05:00Z",
    }
    digest_input = copy.deepcopy(bundle)
    del digest_input["bundle_digest"]
    bundle["bundle_digest"] = _canonical_sha256(digest_input)
    return bundle


def _valid_receipt(bundle: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "test-evidence-receipt.v1",
        "run_id": bundle["run_id"],
        "workflow_id": bundle["workflow_id"],
        "repository": bundle["repository"],
        "branch": bundle["branch"],
        "head": bundle["head"],
        "environment_digest": bundle["environment_digest"],
        "selected_scope_digest": _canonical_sha256(bundle["scope"]),
        "bundle_digest": bundle["bundle_digest"],
        "test_side_commits": bundle["test_side_commits"],
        "result": bundle["result"],
    }


def _assert_schema_instance(schema: dict[str, object], value: object, path: str) -> None:
    declared_type = schema.get("type")
    allowed_types = declared_type if isinstance(declared_type, list) else [declared_type]
    if value is None:
        if "null" not in allowed_types:
            raise AssertionError(f"{path} must not be null")
        return

    if "object" in allowed_types:
        if not isinstance(value, dict):
            raise AssertionError(f"{path} must be an object")
        if schema.get("additional_properties") is not False:
            raise AssertionError(f"{path} schema must close additional properties")
        required = schema.get("required")
        properties = schema.get("properties")
        if not isinstance(required, list) or not isinstance(properties, dict):
            raise AssertionError(f"{path} object schema is malformed")
        missing = [field for field in required if field not in value]
        if missing:
            raise AssertionError(f"{path} is missing required fields {missing!r}")
        extras = sorted(set(value) - set(properties))
        if extras:
            raise AssertionError(f"{path} has unknown fields {extras!r}")
        for field, item in value.items():
            child_schema = properties[field]
            if not isinstance(child_schema, dict):
                raise AssertionError(f"{path}.{field} schema must be an object")
            _assert_schema_instance(child_schema, item, f"{path}.{field}")
        return

    if "array" in allowed_types:
        if not isinstance(value, list):
            raise AssertionError(f"{path} must be an array")
        minimum = schema.get("min_items", 0)
        if not isinstance(minimum, int) or len(value) < minimum:
            raise AssertionError(f"{path} has too few items")
        if schema.get("unique_items") is True:
            serialized = [json.dumps(item, sort_keys=True) for item in value]
            if len(serialized) != len(set(serialized)):
                raise AssertionError(f"{path} must contain unique items")
        items = schema.get("items")
        if not isinstance(items, dict):
            raise AssertionError(f"{path}.items schema must be an object")
        for index, item in enumerate(value):
            _assert_schema_instance(items, item, f"{path}[{index}]")
        return

    if "string" in allowed_types:
        if not isinstance(value, str):
            raise AssertionError(f"{path} must be a string")
        minimum = schema.get("min_length", 0)
        if not isinstance(minimum, int) or len(value) < minimum:
            raise AssertionError(f"{path} is too short")
        constant = schema.get("const")
        if constant is not None and value != constant:
            raise AssertionError(f"{path} must equal {constant!r}")
        enum = schema.get("enum")
        if enum is not None and value not in enum:
            raise AssertionError(f"{path} must be one of {enum!r}")
        pattern = schema.get("pattern")
        if pattern is not None and (
            not isinstance(pattern, str) or re.fullmatch(pattern, value) is None
        ):
            raise AssertionError(f"{path} does not match {pattern!r}")
        return

    raise AssertionError(f"{path} has unsupported declared type {declared_type!r}")


def _wrong_instance_value(schema: dict[str, object]) -> object:
    declared_type = schema["type"]
    allowed_types = declared_type if isinstance(declared_type, list) else [declared_type]
    if "object" in allowed_types:
        return []
    if "array" in allowed_types:
        return {}
    return 7


def _object_schema_paths(
    schema: dict[str, object], path: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    paths: list[tuple[object, ...]] = []
    if schema.get("type") == "object":
        paths.append(path)
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, child in properties.items():
                if isinstance(child, dict):
                    paths.extend(
                        _object_schema_paths(child, (*path, "properties", name))
                    )
    if schema.get("type") == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            paths.extend(_object_schema_paths(items, (*path, "items")))
    return paths


def _at_path(value: object, path: tuple[object, ...]) -> object:
    current = value
    for component in path:
        if not isinstance(current, dict) or component not in current:
            raise AssertionError(f"invalid fixture path {path!r}")
        current = current[component]
    return current


def _frontmatter(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise AssertionError(f"missing Test entrypoint: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("Test entrypoint has no frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError("Test frontmatter is unterminated") from error
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip().strip('"')
    return values


def _markdown_section(contents: str, heading: str) -> str:
    match = re.search(
        rf"^{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        contents,
        flags=re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing markdown section {heading!r}")
    return match.group("body")


def _input_authoring_contract(contents: str) -> dict[str, object]:
    matches: list[dict[str, object]] = []
    for raw in re.findall(r"^```json\s*$\n(.*?)^```\s*$", contents, re.MULTILINE | re.DOTALL):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("schema_marker") == "test-input-authoring-contract.v1":
            matches.append(value)
    if len(matches) != 1:
        raise AssertionError(
            "Test must contain exactly one machine-readable test-input-authoring-contract.v1 block"
        )
    return matches[0]


def _assert_prose_values(
    case: unittest.TestCase,
    values: object,
    keys: set[str],
    exact: dict[str, object] | None = None,
    containing: dict[str, str | list[str]] | None = None,
) -> None:
    exact = exact or {}
    containing = containing or {}
    case.assertIsInstance(values, dict)
    case.assertEqual(set(values), keys | set(exact) | set(containing))
    for key in keys:
        with case.subTest(prose=key):
            case.assertIsInstance(values[key], str)
            case.assertTrue(values[key].strip())
    case.assertEqual({key: values[key] for key in exact}, exact)
    for key, identifiers in containing.items():
        with case.subTest(identifier=key):
            observed = values[key] if isinstance(values[key], list) else [values[key]]
            expected = identifiers if isinstance(identifiers, list) else [identifiers]
            case.assertEqual(len(observed), len(expected))
            for value, identifier in zip(observed, expected):
                case.assertIsInstance(value, str)
                case.assertIn(identifier, value)


def _load_contract_finalizer_module():
    spec = importlib.util.spec_from_file_location(
        "test_evidence_finalizer_contract", FINALIZER
    )
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


def _load_final_action_recorder_module():
    spec = importlib.util.spec_from_file_location(
        "test_final_action_recorder_contract", FINAL_ACTION_RECORDER
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {FINAL_ACTION_RECORDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _load_ledger_appender_module():
    spec = importlib.util.spec_from_file_location(
        "test_ledger_appender_contract", LEDGER_APPENDER
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {LEDGER_APPENDER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _assert_pre_write_ownership_gate(contents: str) -> None:
    headings = re.findall(r"^## .+$", contents, flags=re.MULTILINE)
    expected_prefix = [
        "## Boundary",
        "## Pre-write ownership gate",
        "## Ground and exempt",
    ]
    if headings[:3] != expected_prefix:
        raise AssertionError(
            "missing early Pre-write ownership gate immediately after Boundary"
        )


def _assert_terminal_evidence_transaction(contents: str) -> None:
    headings = re.findall(r"^## .+$", contents, flags=re.MULTILINE)
    evidence_index = headings.index("## Evidence and retention")
    if headings[evidence_index : evidence_index + 3] != [
        "## Evidence and retention",
        "## Terminal evidence transaction",
        "## Result",
    ]:
        raise AssertionError(
            "missing canonical Terminal evidence transaction before Result"
        )
    transaction = _markdown_section(contents, "## Terminal evidence transaction")
    _input_authoring_contract(transaction)
    handoff_command = """python3 .agents/skills/test/scripts/record_final_action.py handoff \\
  --root <home>/.expskill/skills/test/runs/<repo-hash>/<run-id> \\
  -- <literal-product-command-and-arguments>"""
    if transaction.count(handoff_command) != 1:
        raise AssertionError("Terminal evidence transaction command drift")
    collapsed_transaction = " ".join(transaction.split())
    for obsolete in (
        "record_final_action.py validate",
        "record_final_action.py run",
        "finalize_evidence.py compose-draft",
    ):
        if obsolete in collapsed_transaction:
            raise AssertionError("Terminal evidence transaction retained split commands")


class TestSkillContractTests(unittest.TestCase):
    def copy_repository(self) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        temporary = Path(temporary_directory.name)
        for name in ("plugins", "scripts"):
            shutil.copytree(ROOT / name, temporary / name)
        shutil.copy2(ROOT / "README.md", temporary / "README.md")
        (temporary / "docs").mkdir()
        shutil.copy2(ROOT / "docs" / "guide.md", temporary / "docs" / "guide.md")
        return temporary

    def _restore_home_var(self, original: str | None) -> None:
        if original is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original

    def valid_finalizer_inputs(
        self,
    ) -> tuple[Path, dict[str, object], dict[str, object], dict[str, object]]:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        home = Path(temporary_directory.name) / "home"
        home.mkdir()
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        self.addCleanup(self._restore_home_var, original_home)
        repository = Path(temporary_directory.name).resolve()
        digest = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()
        run_root = home / ".expskill" / "skills" / "test" / "runs" / digest / "run-001"
        (run_root / "artifacts").mkdir(parents=True)
        run_root.chmod(0o700)
        (run_root / "artifacts" / "consumer.log").write_text(
            "consumer observation\n", encoding="utf-8"
        )
        finalizer_fixtures.write_charter(run_root, repository)
        finalizer_fixtures.write_ledger(run_root)
        finalizer_fixtures.write_draft(run_root)
        return (
            run_root,
            finalizer_fixtures._read_json(run_root / "charter.json"),
            finalizer_fixtures._read_json(run_root / "ledger.json"),
            finalizer_fixtures._read_json(run_root / "draft.json"),
        )

    def catalog_path(self, root: Path) -> Path:
        return (
            root
            / "plugins"
            / "expskill"
            / "content"
            / "skills"
            / "test"
            / "references"
            / "quality-rules.json"
        )

    def write_catalog(self, root: Path, payload: object = VALID_CATALOG) -> Path:
        path = self.catalog_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def evidence_contract_path(self, root: Path) -> Path:
        return (
            root
            / "plugins"
            / "expskill"
            / "content"
            / "skills"
            / "test"
            / "references"
            / "evidence-contract.json"
        )

    def write_evidence_contract(
        self, root: Path, payload: object = VALID_EVIDENCE_CONTRACT
    ) -> Path:
        path = self.evidence_contract_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def copy_with_valid_catalog(self) -> tuple[Path, Path]:
        root = self.copy_repository()
        path = self.write_catalog(root, copy.deepcopy(VALID_CATALOG))
        self.write_evidence_contract(root, copy.deepcopy(VALID_EVIDENCE_CONTRACT))
        self.assertEqual(validate_repository(root, include_opencode=False), ())
        return root, path

    def copy_with_valid_evidence_contract(self) -> tuple[Path, Path]:
        root = self.copy_repository()
        path = self.write_evidence_contract(
            root, copy.deepcopy(VALID_EVIDENCE_CONTRACT)
        )
        self.assertEqual(validate_repository(root, include_opencode=False), ())
        return root, path

    def assert_catalog_rejected(self, root: Path, marker: str) -> None:
        errors = validate_repository(root, include_opencode=False)
        matching = [
            error
            for error in errors
            if "test quality catalog" in error and marker in error
        ]
        self.assertTrue(matching, f"missing {marker!r} catalog error in {errors!r}")

    def assert_evidence_contract_rejected(self, root: Path, marker: str) -> None:
        errors = validate_repository(root, include_opencode=False)
        matching = [
            error
            for error in errors
            if "test evidence contract" in error and marker in error
        ]
        self.assertTrue(
            matching,
            f"missing {marker!r} evidence-contract error in {errors!r}",
        )

    def test_package_file_set_is_closed_and_codex_metadata_is_explicit_only(self) -> None:
        """Regression: Test ships only its declared resources and helpers, with explicit-only Codex metadata."""

        self.assertTrue(TEST_SKILL.is_dir(), f"missing Test package: {TEST_SKILL}")
        observed = {
            path.relative_to(TEST_SKILL).as_posix()
            for path in TEST_SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.relative_to(TEST_SKILL).parts
        }
        self.assertEqual(
            observed,
            {
                "SKILL.md",
                "references/quality-rules.json",
                "references/evidence-contract.json",
                "scripts/append_ledger.py",
                "scripts/bootstrap_run.py",
                "scripts/finalize_evidence.py",
                "scripts/freeze_charter.py",
                "scripts/record_final_action.py",
            },
        )
        metadata_path = CODEX_TEST_METADATA
        self.assertTrue(metadata_path.is_file(), metadata_path)
        metadata = metadata_path.read_text(encoding="utf-8")
        self.assertRegex(metadata, r'(?m)^  default_prompt: ".*\$test\b')
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_quality_catalog_contains_every_independent_initial_hard_gate(self) -> None:
        """Regression: collapsing named gates prevents independent applicability evidence."""

        payload = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"schema_version", "rules"})
        self.assertEqual(payload["schema_version"], "test-quality-rules.v1")
        rules = payload["rules"]
        identifiers = [rule["id"] for rule in rules]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(EXPECTED_RULE_IDS.issubset(identifiers))
        self.assertTrue(all(rule["level"] == "hard" for rule in rules))

    def test_text_after_boundary_names_quality_catalog_and_every_status(self) -> None:
        """The text after the Boundary section names the quality catalog and its three states."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        boundary_end = contents.index("\n## ", contents.index("## Boundary") + 1)
        quality_section = contents[boundary_end:]
        for marker in (
            "`references/quality-rules.json`",
            "`active`",
            "`inactive`",
            "`unknown`",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, quality_section)

    def test_validator_rejects_missing_empty_directory_and_symlinked_catalogs(self) -> None:
        """Regression: an unreadable catalog must never silently remove all hard gates."""

        for mutation, expected in (
            ("missing", "is missing"),
            ("empty", "not valid JSON"),
            ("directory", "must be a regular file"),
            ("symlink", "contains a symlink"),
        ):
            with self.subTest(mutation=mutation):
                root, path = self.copy_with_valid_catalog()
                if mutation == "missing":
                    path.unlink()
                elif mutation == "empty":
                    path.write_text("", encoding="utf-8")
                elif mutation == "directory":
                    path.unlink()
                    path.mkdir()
                else:
                    path.unlink()
                    target = root / "quality-rules-target.json"
                    target.write_text(json.dumps(VALID_CATALOG), encoding="utf-8")
                    path.symlink_to(target)
                self.assert_catalog_rejected(root, expected)

    def test_validator_rejects_invalid_json_and_non_object_catalog_roots(self) -> None:
        """Regression: malformed JSON must fail closed before any rule is consumed."""

        for mutation, payload, expected in (
            ("invalid-json", "{", "not valid JSON"),
            ("array-root", [], "must contain a JSON object"),
            ("string-root", "\"rules\"", "must contain a JSON object"),
        ):
            with self.subTest(mutation=mutation):
                root, _ = self.copy_with_valid_catalog()
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, expected)

    def test_validator_rejects_invalid_top_level_catalog_schema(self) -> None:
        """Regression: version or root-field drift must not change catalog meaning silently."""

        mutations: tuple[tuple[str, object, str], ...] = (
            (
                "unknown-field",
                {**copy.deepcopy(VALID_CATALOG), "surprise": True},
                "keys must be exactly",
            ),
            (
                "missing-schema-version",
                {"rules": [copy.deepcopy(VALID_RULE)]},
                "keys must be exactly",
            ),
            (
                "missing-rules",
                {"schema_version": "test-quality-rules.v1"},
                "keys must be exactly",
            ),
            (
                "wrong-version",
                {
                    **copy.deepcopy(VALID_CATALOG),
                    "schema_version": "test-quality-rules.v2",
                },
                "schema_version must be",
            ),
            (
                "rules-not-list",
                {"schema_version": "test-quality-rules.v1", "rules": {}},
                "rules must be a non-empty list",
            ),
            (
                "rules-empty",
                {"schema_version": "test-quality-rules.v1", "rules": []},
                "rules must be a non-empty list",
            ),
            (
                "rule-not-object",
                {"schema_version": "test-quality-rules.v1", "rules": ["rule"]},
                "rules[0] must be an object",
            ),
        )
        for mutation, payload, expected in mutations:
            with self.subTest(mutation=mutation):
                root, _ = self.copy_with_valid_catalog()
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, expected)

    def test_validator_rejects_unknown_and_missing_rule_fields(self) -> None:
        """Regression: every rule field carries distinct hard-gate meaning."""

        mutations = [("unknown", {**copy.deepcopy(VALID_RULE), "surprise": True})]
        for field in VALID_RULE:
            rule = {
                key: copy.deepcopy(value)
                for key, value in VALID_RULE.items()
                if key != field
            }
            mutations.append((f"missing-{field}", rule))

        for mutation, rule in mutations:
            with self.subTest(mutation=mutation):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"] = [rule]
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, "rules[0] keys must be exactly")

    def test_validator_rejects_duplicate_and_malformed_rule_ids(self) -> None:
        """Regression: invalid or duplicate IDs make rule evidence ambiguous."""

        root, _ = self.copy_with_valid_catalog()
        duplicate = copy.deepcopy(VALID_CATALOG)
        duplicate["rules"].append(copy.deepcopy(VALID_RULE))
        self.write_catalog(root, duplicate)
        self.assert_catalog_rejected(root, "duplicate rule id")

        malformed = (
            "",
            " ",
            "universal",
            "Universal.rule",
            "universal_rule",
            ".universal",
            "universal.",
            "universal..rule",
            "universal.-rule",
            "universal.rule-",
            1,
        )
        for identifier in malformed:
            with self.subTest(identifier=identifier):
                changed_root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0]["id"] = identifier
                self.write_catalog(changed_root, payload)
                self.assert_catalog_rejected(changed_root, "must be a kebab/dot identifier")

    def test_validator_rejects_every_non_hard_level(self) -> None:
        """Regression: advisory levels let applicable integrity failures pass."""

        for level in ("soft", "advisory", "", None, 1):
            with self.subTest(level=level):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0]["level"] = level
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, "level must be 'hard'")

    def test_validator_rejects_unknown_empty_or_malformed_applicability(self) -> None:
        """Regression: malformed applicability can silently deactivate a hard gate."""

        for applies_when in (
            [],
            [""],
            ["unknown-condition"],
            "always",
            [1],
        ):
            with self.subTest(applies_when=applies_when):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0]["applies_when"] = applies_when
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, "applies_when")

    def test_validator_rejects_empty_requirements_failures_and_evidence(self) -> None:
        """Regression: empty rule obligations cannot support a hard-gate decision."""

        mutations = (
            (
                "requirement-empty",
                "requirement",
                "",
                "requirement must be a non-empty string",
            ),
            (
                "requirement-blank",
                "requirement",
                "  ",
                "requirement must be a non-empty string",
            ),
            (
                "failure-empty",
                "failure_prevented",
                "",
                "failure_prevented must be a non-empty string",
            ),
            (
                "failure-not-string",
                "failure_prevented",
                [],
                "failure_prevented must be a non-empty string",
            ),
            (
                "evidence-empty",
                "required_evidence",
                [],
                "required_evidence must be a non-empty list",
            ),
            (
                "evidence-not-list",
                "required_evidence",
                "evidence",
                "required_evidence must be a non-empty list",
            ),
            (
                "evidence-blank-item",
                "required_evidence",
                [""],
                "required_evidence[0] must be a non-empty string",
            ),
            (
                "evidence-non-string-item",
                "required_evidence",
                [1],
                "required_evidence[0] must be a non-empty string",
            ),
        )
        for mutation, field, value, expected in mutations:
            with self.subTest(mutation=mutation):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0][field] = value
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, expected)

    def test_validator_rejects_non_list_exception_values(self) -> None:
        """Regression: an exception container must not admit ambiguous shapes."""

        for exceptions in ({}, "none", None, 1):
            with self.subTest(exceptions=exceptions):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0]["allowed_exceptions"] = exceptions
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, "allowed_exceptions must be a list")

    def test_validator_rejects_inexact_or_unobservable_exception_entries(self) -> None:
        """Regression: exceptions require an observable predicate and recorded evidence."""

        valid_exception = copy.deepcopy(VALID_RULE["allowed_exceptions"][0])
        mutations = (
            ("not-object", "exception", "allowed_exceptions[0] must be an object"),
            (
                "missing-predicate",
                {"required_evidence": ["Observed evidence."]},
                "allowed_exceptions[0] keys must be exactly",
            ),
            (
                "missing-evidence",
                {"predicate": "The runner reports the condition."},
                "allowed_exceptions[0] keys must be exactly",
            ),
            (
                "extra-field",
                {**valid_exception, "reason": "convenience"},
                "allowed_exceptions[0] keys must be exactly",
            ),
            (
                "blank-predicate",
                {**valid_exception, "predicate": " "},
                "predicate must be a non-empty string",
            ),
            (
                "non-string-predicate",
                {**valid_exception, "predicate": True},
                "predicate must be a non-empty string",
            ),
            (
                "empty-evidence",
                {**valid_exception, "required_evidence": []},
                "required_evidence must be a non-empty list",
            ),
            (
                "non-list-evidence",
                {**valid_exception, "required_evidence": "evidence"},
                "required_evidence must be a non-empty list",
            ),
            (
                "blank-evidence-item",
                {**valid_exception, "required_evidence": [""]},
                "required_evidence[0] must be a non-empty string",
            ),
            (
                "non-string-evidence-item",
                {**valid_exception, "required_evidence": [1]},
                "required_evidence[0] must be a non-empty string",
            ),
        )
        for mutation, exception, expected in mutations:
            with self.subTest(mutation=mutation):
                root, _ = self.copy_with_valid_catalog()
                payload = copy.deepcopy(VALID_CATALOG)
                payload["rules"][0]["allowed_exceptions"] = [exception]
                self.write_catalog(root, payload)
                self.assert_catalog_rejected(root, expected)

    def test_validator_rejects_missing_empty_directory_and_symlinked_evidence_contracts(
        self,
    ) -> None:
        """Regression: every terminal result must fail closed without its contract."""

        for mutation, expected in (
            ("missing", "is missing"),
            ("empty", "not valid JSON"),
            ("directory", "must be a regular file"),
            ("symlink", "contains a symlink"),
        ):
            with self.subTest(mutation=mutation):
                root, path = self.copy_with_valid_evidence_contract()
                if mutation == "missing":
                    path.unlink()
                elif mutation == "empty":
                    path.write_text("", encoding="utf-8")
                elif mutation == "directory":
                    path.unlink()
                    path.mkdir()
                else:
                    path.unlink()
                    target = root / "evidence-contract-target.json"
                    target.write_text(
                        json.dumps(VALID_EVIDENCE_CONTRACT), encoding="utf-8"
                    )
                    path.symlink_to(target)
                self.assert_evidence_contract_rejected(root, expected)

    def test_validator_rejects_invalid_json_and_non_object_evidence_roots(self) -> None:
        """Regression: malformed evidence declarations cannot govern terminal results."""

        root, _ = self.copy_with_valid_evidence_contract()
        for mutation, payload, expected in (
            ("invalid-json", "{", "not valid JSON"),
            ("array-root", [], "must contain a JSON object"),
            ("string-root", '"evidence"', "must contain a JSON object"),
        ):
            with self.subTest(mutation=mutation):
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(root, expected)

    def test_validator_rejects_unknown_missing_and_substituted_root_contract_values(
        self,
    ) -> None:
        """Regression: root key or enum drift must not silently redefine a verdict."""

        root, _ = self.copy_with_valid_evidence_contract()
        mutations: list[tuple[str, dict[str, object], str]] = [
            (
                "unknown-key",
                {**copy.deepcopy(VALID_EVIDENCE_CONTRACT), "surprise": True},
                "keys must be exactly",
            ),
            (
                "wrong-version",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "schema_version": "test-evidence-contract.v2",
                },
                "schema_version must be",
            ),
            (
                "terminal-state-substitution",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "terminal_states": ["PASS", "FAIL", "BLOCKED", "SKIPPED"],
                },
                "terminal_states must be exactly",
            ),
            (
                "terminal-state-duplicate",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "terminal_states": ["PASS", "FAIL", "BLOCKED", "BLOCKED"],
                },
                "terminal_states must be exactly",
            ),
            (
                "terminal-states-wrong-type",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "terminal_states": "PASS",
                },
                "terminal_states must be exactly",
            ),
            (
                "finding-kind-substitution",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "finding_kinds": [
                        "product-defect",
                        "test-system-defect",
                        "environment-blocker",
                        "unknown-defect",
                    ],
                },
                "finding_kinds must be exactly",
            ),
            (
                "finding-kind-duplicate",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "finding_kinds": [
                        "product-defect",
                        "test-system-defect",
                        "environment-blocker",
                        "environment-blocker",
                    ],
                },
                "finding_kinds must be exactly",
            ),
            (
                "finding-kinds-wrong-type",
                {
                    **copy.deepcopy(VALID_EVIDENCE_CONTRACT),
                    "finding_kinds": {},
                },
                "finding_kinds must be exactly",
            ),
        ]
        for field in VALID_EVIDENCE_CONTRACT:
            payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
            del payload[field]
            mutations.append((f"missing-{field}", payload, "keys must be exactly"))

        for mutation, payload, expected in mutations:
            with self.subTest(mutation=mutation):
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(root, expected)

    def test_validator_rejects_missing_duplicate_and_unknown_definition_fields(self) -> None:
        """Regression: every bundle, receipt, and finding field has distinct meaning."""

        root, _ = self.copy_with_valid_evidence_contract()
        for definition, expected_fields in (
            ("bundle", BUNDLE_FIELDS),
            ("receipt", RECEIPT_FIELDS),
            ("finding", FINDING_FIELDS),
        ):
            for field in expected_fields:
                with self.subTest(definition=definition, mutation="missing-required", field=field):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    payload[definition]["required"].remove(field)
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, f"{definition}.required must contain each required field exactly once"
                    )

                with self.subTest(definition=definition, mutation="duplicate-required", field=field):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    payload[definition]["required"].append(field)
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, f"{definition}.required must contain each required field exactly once"
                    )

                with self.subTest(definition=definition, mutation="missing-property", field=field):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    del payload[definition]["properties"][field]
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, f"{definition}.properties keys must be exactly"
                    )

            with self.subTest(definition=definition, mutation="unknown-property"):
                payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                payload[definition]["properties"]["surprise"] = _string_schema()
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(
                    root, f"{definition}.properties keys must be exactly"
                )

    def test_validator_rejects_mistyped_definition_fields_and_unknown_schema_keys(
        self,
    ) -> None:
        """Regression: a valid key with a substituted type changes evidence meaning."""

        root, _ = self.copy_with_valid_evidence_contract()
        for definition, expected_fields in (
            ("bundle", BUNDLE_FIELDS),
            ("receipt", RECEIPT_FIELDS),
            ("finding", FINDING_FIELDS),
        ):
            for field in expected_fields:
                with self.subTest(definition=definition, field=field):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    field_schema = payload[definition]["properties"][field]
                    field_schema["type"] = (
                        "array" if field_schema["type"] != "array" else "string"
                    )
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, f"{definition}.properties.{field} must match the required schema"
                    )

        payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
        payload["bundle"]["properties"]["run_id"]["surprise"] = True
        self.write_evidence_contract(root, payload)
        self.assert_evidence_contract_rejected(
            root, "bundle.properties.run_id must match the required schema"
        )

    def test_validator_rejects_malformed_schema_container_types_without_crashing(
        self,
    ) -> None:
        """Regression: hostile schema containers must return errors, not abort validation."""

        root, _ = self.copy_with_valid_evidence_contract()
        mutations = (
            ("definition", ("bundle",), [], "bundle must be an object schema"),
            (
                "required-not-list",
                ("bundle", "required"),
                {},
                "bundle.required must contain each required field exactly once",
            ),
            (
                "required-item-not-string",
                ("bundle", "required"),
                [*BUNDLE_FIELDS[:-1], {}],
                "bundle.required must contain each required field exactly once",
            ),
            (
                "properties-not-object",
                ("bundle", "properties"),
                [],
                "bundle.properties keys must be exactly",
            ),
            (
                "property-schema-not-object",
                ("bundle", "properties", "run_id"),
                "string",
                "bundle.properties.run_id must be an object schema",
            ),
            (
                "array-items-not-object",
                ("bundle", "properties", "limitations", "items"),
                "string",
                "bundle.properties.limitations.items must be an object schema",
            ),
        )
        for mutation, path, replacement, expected in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                parent = _at_path(payload, path[:-1])
                parent[path[-1]] = replacement
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(root, expected)

    def test_validator_rejects_open_object_boundaries_at_every_depth(self) -> None:
        """Regression: no nested evidence object may admit undeclared data."""

        root, _ = self.copy_with_valid_evidence_contract()
        for definition in ("bundle", "receipt", "finding"):
            paths = _object_schema_paths(VALID_EVIDENCE_CONTRACT[definition])
            self.assertTrue(paths)
            for path in paths:
                with self.subTest(definition=definition, path=path):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    schema = _at_path(payload[definition], path)
                    self.assertIsInstance(schema, dict)
                    schema["additional_properties"] = True
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, "additional_properties must be false"
                    )

    def test_validator_rejects_duplicate_and_unknown_fields_at_every_object_boundary(
        self,
    ) -> None:
        """Regression: nested closed objects cannot redefine their own field set."""

        root, _ = self.copy_with_valid_evidence_contract()
        for definition in ("bundle", "receipt", "finding"):
            for path in _object_schema_paths(VALID_EVIDENCE_CONTRACT[definition]):
                expected_schema = _at_path(VALID_EVIDENCE_CONTRACT[definition], path)
                required = expected_schema["required"]
                self.assertTrue(required)

                with self.subTest(definition=definition, path=path, mutation="duplicate"):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    schema = _at_path(payload[definition], path)
                    schema["required"].append(required[0])
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, "required must contain each required field exactly once"
                    )

                with self.subTest(definition=definition, path=path, mutation="unknown"):
                    payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                    schema = _at_path(payload[definition], path)
                    schema["required"].append("surprise")
                    schema["properties"]["surprise"] = _string_schema()
                    self.write_evidence_contract(root, payload)
                    self.assert_evidence_contract_rejected(
                        root, "properties keys must be exactly"
                    )

    def test_validator_rejects_unbound_revision_environment_scope_and_digests(self) -> None:
        """Regression: a well-shaped receipt must not be detached from its evidence."""

        root, _ = self.copy_with_valid_evidence_contract()
        mutations = (
            ("bundle-head", ("bundle", "head", "pattern"), ".*", "must bind exact head"),
            ("receipt-head", ("receipt", "head", "pattern"), ".*", "must bind exact head"),
            ("finding-head", ("finding", "head", "pattern"), ".*", "must bind exact head"),
            (
                "bundle-environment",
                ("bundle", "environment_digest", "semantics"),
                "A digest.",
                "must bind exact environment",
            ),
            (
                "receipt-environment",
                ("receipt", "environment_digest", "semantics"),
                "A digest.",
                "must bind exact environment",
            ),
            (
                "finding-environment",
                ("finding", "environment_digest", "semantics"),
                "A digest.",
                "must bind exact environment",
            ),
            (
                "bundle-self-referential-digest",
                ("bundle", "bundle_digest", "semantics"),
                "SHA-256 over the complete bundle including bundle_digest.",
                "must omit bundle_digest",
            ),
            (
                "receipt-unbound-bundle",
                ("receipt", "bundle_digest", "semantics"),
                "A digest.",
                "must bind retained bundle",
            ),
            (
                "receipt-unbound-scope",
                ("receipt", "selected_scope_digest", "semantics"),
                "A digest.",
                "must bind selected scope",
            ),
        )
        for mutation, path, replacement, expected in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                definition, field, key = path
                payload[definition]["properties"][field][key] = replacement
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(root, expected)

    def test_validator_rejects_unscoped_nullability_and_enum_drift(self) -> None:
        """Regression: only direct-run workflow ancestry is nullable, enums stay closed."""

        root, _ = self.copy_with_valid_evidence_contract()
        mutations = (
            (
                "bundle-workflow-not-nullable",
                "bundle",
                "workflow_id",
                {"type": "string", "min_length": 1},
                "workflow_id must be nullable only for direct invocation",
            ),
            (
                "receipt-workflow-not-nullable",
                "receipt",
                "workflow_id",
                {"type": "string", "min_length": 1},
                "workflow_id must be nullable only for direct invocation",
            ),
            (
                "unscoped-head-nullability",
                "bundle",
                "head",
                {"type": ["string", "null"], "min_length": 1},
                "bundle.properties.head must match the required schema",
            ),
            (
                "bundle-result-enum",
                "bundle",
                "result",
                _string_schema(enum=["PASS", "FAIL", "BLOCKED", "SKIPPED"]),
                "bundle result enum must match terminal_states",
            ),
            (
                "receipt-result-enum",
                "receipt",
                "result",
                _string_schema(enum=["PASS", "FAIL", "BLOCKED", "SKIPPED"]),
                "receipt result enum must match terminal_states",
            ),
            (
                "finding-kind-enum",
                "finding",
                "kind",
                _string_schema(enum=["product-defect", "unknown-defect"]),
                "finding kind enum must match finding_kinds",
            ),
        )
        for mutation, definition, field, field_schema, expected in mutations:
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(VALID_EVIDENCE_CONTRACT)
                payload[definition]["properties"][field] = field_schema
                self.write_evidence_contract(root, payload)
                self.assert_evidence_contract_rejected(root, expected)

    def test_local_contract_validates_complete_bundle_receipt_and_finding_examples(
        self,
    ) -> None:
        """Regression: the declarative schema must accept complete bound examples."""

        contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
        bundle = _valid_bundle()
        receipt = _valid_receipt(bundle)
        finding = _valid_finding()
        _assert_schema_instance(contract["bundle"], bundle, "bundle")
        _assert_schema_instance(contract["receipt"], receipt, "receipt")
        _assert_schema_instance(contract["finding"], finding, "finding")

        digest_input = copy.deepcopy(bundle)
        del digest_input["bundle_digest"]
        self.assertEqual(bundle["bundle_digest"], _canonical_sha256(digest_input))
        self.assertEqual(receipt["bundle_digest"], bundle["bundle_digest"])
        self.assertEqual(
            receipt["selected_scope_digest"], _canonical_sha256(bundle["scope"])
        )

        direct_bundle = copy.deepcopy(bundle)
        direct_bundle["workflow_id"] = None
        digest_input = copy.deepcopy(direct_bundle)
        del digest_input["bundle_digest"]
        direct_bundle["bundle_digest"] = _canonical_sha256(digest_input)
        direct_receipt = _valid_receipt(direct_bundle)
        _assert_schema_instance(contract["bundle"], direct_bundle, "direct_bundle")
        _assert_schema_instance(contract["receipt"], direct_receipt, "direct_receipt")

    def test_local_contract_rejects_every_missing_mistyped_enum_and_unknown_example_field(
        self,
    ) -> None:
        """Regression: every required example field is independently enforced."""

        contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
        bundle = _valid_bundle()
        examples = {
            "bundle": bundle,
            "receipt": _valid_receipt(bundle),
            "finding": _valid_finding(),
        }
        for definition, example in examples.items():
            schema = contract[definition]
            for field in schema["required"]:
                with self.subTest(definition=definition, mutation="missing", field=field):
                    changed = copy.deepcopy(example)
                    del changed[field]
                    with self.assertRaises(AssertionError):
                        _assert_schema_instance(schema, changed, definition)

                with self.subTest(definition=definition, mutation="type", field=field):
                    changed = copy.deepcopy(example)
                    changed[field] = _wrong_instance_value(schema["properties"][field])
                    with self.assertRaises(AssertionError):
                        _assert_schema_instance(schema, changed, definition)

            with self.subTest(definition=definition, mutation="unknown-field"):
                changed = copy.deepcopy(example)
                changed["surprise"] = True
                with self.assertRaises(AssertionError):
                    _assert_schema_instance(schema, changed, definition)

        enum_mutations = (
            ("bundle", "result", "SKIPPED"),
            ("receipt", "result", "SKIPPED"),
            ("finding", "kind", "unknown-defect"),
            ("finding", "severity", "informational"),
            ("finding", "ring", "outside"),
        )
        for definition, field, replacement in enum_mutations:
            with self.subTest(definition=definition, enum_field=field):
                changed = copy.deepcopy(examples[definition])
                changed[field] = replacement
                with self.assertRaises(AssertionError):
                    _assert_schema_instance(contract[definition], changed, definition)

        for definition in ("bundle", "receipt", "finding"):
            with self.subTest(definition=definition, null_field="head"):
                changed = copy.deepcopy(examples[definition])
                changed["head"] = None
                with self.assertRaises(AssertionError):
                    _assert_schema_instance(contract[definition], changed, definition)

    def test_evidence_section_references_the_shipped_evidence_contract(self) -> None:
        """Regression: the evidence section must point at the shipped evidence contract file."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        evidence_section = _markdown_section(contents, "## Evidence and retention")
        self.assertIn("`references/evidence-contract.json`", evidence_section)

    def test_machine_readable_input_contract_matches_the_shipped_finalizer(self) -> None:
        """The input authoring contract lists the same field sets as the shipped finalizer and appender."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        contract = _input_authoring_contract(contents)
        finalizer = _load_contract_finalizer_module()
        appender = _load_ledger_appender_module()
        expected_objects = {
            "charter": finalizer.CHARTER_FIELDS,
            "scope": finalizer.SCOPE_FIELDS,
            "oracle": finalizer.ORACLE_FIELDS,
            "ledger": finalizer.DIRECT_LEDGER_FIELDS,
            "entry": finalizer.ENTRY_FIELDS,
            "ledger_batch": appender.BATCH_FIELDS,
            "ledger_batch_entry": appender.BATCH_ENTRY_FIELDS,
            "draft": finalizer.DRAFT_FIELDS,
            "draft_candidate": finalizer.COMPACT_DRAFT_FIELDS,
            "rule": finalizer.RULE_FIELDS,
            "exploration": finalizer.EXPLORATION_FIELDS,
            "finding": finalizer.FINDING_FIELDS,
            "artifact": finalizer.ARTIFACT_FIELDS,
            "teardown": finalizer.TEARDOWN_FIELDS,
            "draft_preparation": finalizer.DIRECT_PREPARATION_FIELDS,
            "rule_assessment_group": finalizer.RULE_ASSESSMENT_GROUP_FIELDS,
            "draft_final_delta": finalizer.DELTA_FIELDS,
        }
        self.assertEqual(set(contract["closed_objects"]), set(expected_objects))
        for name, expected in expected_objects.items():
            with self.subTest(object=name):
                self.assertEqual(set(contract["closed_objects"][name]), set(expected))

        self.assertEqual(
            contract["schema_versions"],
            {
                "charter": finalizer.CHARTER_SCHEMA_VERSION,
                "ledger": finalizer.DIRECT_LEDGER_SCHEMA_VERSION,
                "ledger_batch": appender.BATCH_SCHEMA_VERSION,
                "draft": finalizer.DRAFT_SCHEMA_VERSION,
                "draft_preparation": finalizer.DIRECT_PREPARATION_SCHEMA_VERSION,
                "draft_final_delta": finalizer.EMPTY_DELTA_SCHEMA_VERSION,
            },
        )
        expected_enums = {
            "terminal_state": finalizer.TERMINAL_STATES,
            "action_role": finalizer.ACTION_ROLES,
            "action_status": finalizer.ACTION_STATES,
            "ring": finalizer.RINGS,
            "rule_status": finalizer.RULE_STATUSES,
            "rule_outcome": finalizer.RULE_OUTCOMES,
            "artifact_kind": finalizer.ARTIFACT_KINDS,
            "finding_kind": finalizer.FINDING_KINDS,
            "finding_severity": finalizer.FINDING_SEVERITIES,
            "teardown_status": finalizer.TEARDOWN_STATES,
        }
        self.assertEqual(set(contract["enums"]), set(expected_enums))
        for name, expected in expected_enums.items():
            with self.subTest(enum=name):
                self.assertEqual(set(contract["enums"][name]), set(expected))
        packaged_contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            set(packaged_contract["finding_kinds"]), finalizer.FINDING_KINDS
        )
        self.assertEqual(
            set(contract["reserved_artifact_ids"]),
            finalizer.RESERVED_ARTIFACT_IDS,
        )
        _assert_prose_values(
            self,
            contract["authoring_invariants"],
            {
                "scope_behavior",
                "draft_environment",
                "reserved_artifacts",
                "preparation",
                "compact_rules",
                "delta",
                "conditional_entry",
                "candidate_batch",
                "atomic_ledger_batch",
                "observation_capture",
                "action_status_semantics",
                "diagnostic_completion",
                "late_probes",
                "checkpoint",
                "handoff_artifact_binding",
                "immediate_local_recovery",
                "blocked_rule_encoding",
                "preflight_correction",
                "benign_normalization",
                "exempt_execution",
                "branch_budget",
                "canary_independence",
                "exploration_independence",
                "direct_result_gate",
                "machine_identity",
                "root_lineage",
                "direct_argv",
            },
        )
        _assert_prose_values(
            self,
            contract["run_bootstrap"],
            {"inventory", "root_allocation", "parent_seal"},
            {
                "script": "scripts/bootstrap_run.py",
                "derived_fields": [
                    "repository",
                    "branch",
                    "run_id",
                    "root",
                    "started_at",
                    "cutoff_at",
                ],
            },
        )
        self.assertEqual(
            contract["charter_freezer"],
            {
                "script": "scripts/freeze_charter.py",
                "input": "charter-preparation.json",
                "input_schema": "test-charter-preparation.v1",
                "caller_fields": [
                    "schema_version",
                    "workflow_id",
                    "accepted_behavior",
                    "scope",
                    "material_oracles",
                    "exemption_grounding_artifact_ids",
                ],
                "derived_fields": ["repository", "branch", "head", "run_id"],
                "outputs": ["charter.json", "ledger.json"],
            },
        )
        _assert_prose_values(
            self,
            contract["ledger_appender"],
            {"failure"},
            {
                "script": "scripts/append_ledger.py",
                "input": appender.BATCH_FILENAME,
                "input_schema": appender.BATCH_SCHEMA_VERSION,
                "caller_entry_fields": [
                    "action_id",
                    "role",
                    "ring",
                    "action",
                    "path",
                    "expected",
                    "actual",
                    "status",
                    "oracle_ids",
                    "artifact_ids",
                ],
                "derived_fields": ["entries[].head"],
                "output": "ledger.json",
            },
        )
        self.assertEqual(
            contract["composition"]["input_files"],
            {
                "preparation": finalizer.PREPARATION_FILENAME,
                "delta": finalizer.DELTA_FILENAME,
                "output": finalizer.DRAFT_FILENAME,
            },
        )
        _assert_prose_values(
            self,
            contract["composition"]["compact_rule_expansion"],
            {"active_rule_conditions", "rule_assessment_groups", "inactive_rules"},
            {
                "rule_disposition": ["evaluate", "exempt"],
                "evidence": "ledger:<action-id>",
            },
        )
        _assert_prose_values(
            self,
            contract["response_handoff"],
            {"summary", "terminal_state", "after_finalizer"},
            containing={"probe_evidence": ["bundle_digest", "bundle_path", "receipt_path"]},
        )
        _assert_prose_values(
            self,
            contract["composition"]["direct_candidate"],
            {"candidate", "authority"},
            {
                "preparation_schema": finalizer.DIRECT_PREPARATION_SCHEMA_VERSION,
                "delta_schema": finalizer.EMPTY_DELTA_SCHEMA_VERSION,
            },
            containing={"delta_resolutions": "empty"},
        )
        expected_nonempty_scalars = {
            "charter.run_id",
            "charter.repository",
            "charter.branch",
            "charter.head",
            "charter.accepted_behavior",
            "charter.scope.accepted_behavior",
            "charter.material_oracles[].oracle_id",
            "charter.material_oracles[].behavior",
            "charter.material_oracles[].consumer_surface",
            "ledger.run_id",
            "ledger.entries[].action_id",
            "ledger.entries[].head",
            "ledger.entries[].action",
            "ledger.entries[].expected",
            "ledger.entries[].actual",
            "draft.rule_applicability[].rule_id",
            "draft.exploration.mission",
            "draft.exploration.evidence_budget",
            "draft.exploration.stop_condition",
            "draft.findings[].journey",
            "draft.findings[].expected",
            "draft.findings[].actual",
            "draft.artifacts[].artifact_id",
            "draft.artifacts[].path",
            "draft.started_at",
            "draft.summary",
        }
        expected_nonempty_items = {
            "charter.scope.inner_ring[]",
            "charter.scope.adjacent_ring[]",
            "charter.scope.broader_ring[]",
            "charter.material_oracles[].required_action_ids[]",
            "charter.exemption_grounding_artifact_ids[]",
            "ledger.entries[].path[]",
            "ledger.entries[].oracle_ids[]",
            "ledger.entries[].artifact_ids[]",
            "draft.rule_applicability[].evidence[]",
            "draft.exploration.actions[]",
            "draft.exploration.observations[]",
            "draft.exploration.teardown[]",
            "draft.findings[].reproduction[]",
            "draft.findings[].violated_rule_ids[]",
            "draft.findings[].artifacts[]",
            "draft.teardown.actions[]",
            "draft.teardown.artifact_ids[]",
            "draft.test_side_commits[]",
            "draft.limitations[]",
        }
        nonempty = contract["nonempty_strings"]
        self.assertEqual(set(nonempty), {"scalars", "nullable_scalars_when_present", "items"})
        self.assertEqual(set(nonempty["scalars"]), expected_nonempty_scalars)
        self.assertEqual(
            nonempty["nullable_scalars_when_present"], ["charter.workflow_id"]
        )
        self.assertEqual(set(nonempty["items"]), expected_nonempty_items)
        self.assertNotIn("charter.material_oracles[].*", json.dumps(contract))

        cardinality = contract["cardinality"]
        self.assertEqual(
            set(cardinality),
            {
                "min_one",
                "must_be_empty",
                "non_exempt_min_one",
                "exempt_min_one",
                "unique_items",
                "unique_ids",
            },
        )
        self.assertEqual(
            set(cardinality["min_one"]),
            {
                "charter.scope.inner_ring",
                "charter.material_oracles[].required_action_ids",
                "ledger.entries[].artifact_ids",
                "draft.rule_applicability[].evidence",
                "draft.findings[].reproduction",
                "draft.environment",
            },
        )
        self.assertEqual(
            contract["cardinality"]["must_be_empty"],
            ["draft_final_delta.resolutions"],
        )
        self.assertEqual(
            cardinality["non_exempt_min_one"], ["charter.material_oracles"]
        )
        self.assertEqual(
            cardinality["exempt_min_one"],
            ["charter.exemption_grounding_artifact_ids"],
        )
        self.assertEqual(
            set(cardinality["unique_items"]),
            {
                "charter.scope.inner_ring",
                "charter.scope.adjacent_ring",
                "charter.scope.broader_ring",
                "charter.material_oracles[].required_action_ids",
                "charter.exemption_grounding_artifact_ids",
                "ledger.entries[].oracle_ids",
                "ledger.entries[].artifact_ids",
                "draft.findings[].violated_rule_ids",
                "draft.findings[].artifacts",
                "draft.teardown.artifact_ids",
                "draft.test_side_commits",
            },
        )
        self.assertEqual(
            set(cardinality["unique_ids"]),
            {
                "charter.material_oracles[].oracle_id",
                "ledger.entries[].action_id",
                "draft.rule_applicability[].rule_id",
                "draft.artifacts[].artifact_id",
            },
        )

        bindings = dict(contract["bindings"])
        charter_binding = bindings.pop("charter_binding", None)
        self.assertIsInstance(charter_binding, list)
        _assert_prose_values(
            self,
            {
                f"charter_binding[{index}]": item
                for index, item in enumerate(charter_binding)
            },
            {"charter_binding[0]", "charter_binding[1]"},
        )
        self.assertEqual(
            bindings,
            {
                "run_id": ["charter.run_id", "root.basename", "ledger.run_id"],
                "canonical_root": [
                    "root",
                    "home-state-run-root-for-charter-repository-hash-plus-charter.run_id",
                ],
                "scope": [
                    "charter.scope.accepted_behavior",
                    "charter.accepted_behavior",
                ],
                "head": ["ledger.entries[].head", "charter.head"],
                "entry_oracle": [
                    "ledger.entries[].oracle_ids[]",
                    "charter.material_oracles[].oracle_id",
                ],
                "entry_artifact": [
                    "ledger.entries[].artifact_ids[]",
                    "draft.artifacts[].artifact_id",
                ],
                "exemption_artifact": [
                    "charter.exemption_grounding_artifact_ids[]",
                    "draft.artifacts[].artifact_id",
                ],
                "finding_artifact": [
                    "draft.findings[].artifacts[]",
                    "draft.artifacts[].artifact_id",
                ],
                "teardown_artifact": [
                    "draft.teardown.artifact_ids[]",
                    "draft.artifacts[].artifact_id",
                ],
                "finding_rule": [
                    "draft.findings[].violated_rule_ids[]",
                    "draft.rule_applicability[].rule_id",
                ],
            },
        )
        self.assertEqual(
            contract["required_actions"],
            {
                "missing": {
                    "unknown_reference_error": False,
                    "PASS": "forbidden",
                    "FAIL": "requires_other_retained_defect",
                    "BLOCKED": "supports_when_no_defect_is_proven",
                },
                "present": {
                    "ledger_entry": "required",
                    "oracle_back_binding": "required",
                    "retained_artifacts": "one_or_more_declared_artifact_ids",
                },
            },
        )
        _assert_prose_values(
            self,
            contract["formats"],
            {
                "ledger.entries[].head",
                "draft.started_at",
                "charter.workflow_id",
                "charter.repository",
            },
            {
                "charter.run_id": "[A-Za-z0-9][A-Za-z0-9._-]*",
                "charter.head": "[0-9a-f]{40,64}",
                "draft.test_side_commits[]": "[0-9a-f]{40,64}",
            },
        )
        self.assertEqual(
            contract["role_path"],
            {"check": "ignored_source_anchors", "journey": "min_one"},
        )
        self.assertEqual(
            contract["rule_pairs"],
            {
                "active": ["satisfied", "unsatisfied"],
                "inactive": ["not-applicable"],
                "unknown": ["unknown"],
            },
        )
        self.assertEqual(
            contract["terminal_unknown"],
            {
                "compose_draft": "forbidden_in_supplied_candidate",
                "finalize_non_exempt": "forbidden",
                "finalize_exempt": "rule_applicability_must_be_empty",
            },
        )
        self.assertEqual(
            contract["exempt_shape"],
            {
                "charter.material_oracles": "empty",
                "ledger.entries": "empty",
                "draft.rule_applicability": "empty",
                "draft.findings": "empty",
                "charter.exemption_grounding_artifact_ids":
                    "one_or_more_retained_artifact_references",
                "draft.teardown.status": "pass_or_not-required",
            },
        )
        _assert_prose_values(
            self,
            contract["minimum_result_support"],
            {"PASS", "FAIL", "BLOCKED"},
            containing={"EXEMPT": "exempt_shape"},
        )
        _assert_prose_values(
            self,
            contract["paths"]["composition_inputs"],
            set(),
            {
                "preparation": finalizer.PREPARATION_FILENAME,
                "delta": finalizer.DELTA_FILENAME,
                "output": finalizer.DRAFT_FILENAME,
            },
            containing={"charter_binding": "charter.json", "ledger_preflight": "ledger.json"},
        )
        self.assertEqual(
            set(contract["paths"]["artifact"]["forbidden_exact"]),
            finalizer.NON_EVIDENCE_ARTIFACT_PATHS,
        )
        composition = contract["composition"]
        _assert_prose_values(
            self,
            {
                key: composition.get(key)
                for key in ("root_binding", "semantic_boundary")
            },
            {"root_binding", "semantic_boundary"},
        )
        _assert_prose_values(
            self,
            composition["timing"],
            {"compose", "candidate_authority", "successor", "mismatch"},
        )
        for name, publication, cleanup_key in (
            ("composition", composition["publication"], "canonical_output"),
            ("terminal_publication", contract["terminal_publication"], "authority"),
        ):
            with self.subTest(publication=name):
                publication = dict(publication)
                _assert_prose_values(
                    self,
                    publication.pop("failure_cleanup", None),
                    {
                        cleanup_key,
                        "pathname_deletion",
                        "ambiguous_owned_inode",
                        "later_removal",
                    },
                    {"report": "cleanup-failed"},
                )
                _assert_prose_values(
                    self,
                    publication,
                    {"staging", "publish"},
                    {"existing_output": "reject"},
                )

    def test_machine_contract_matches_the_shipped_final_action_recorder(self) -> None:
        """The final-action recorder contract matches the shipped recorder's spec and predicate."""

        contract = _input_authoring_contract(
            (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        )["final_action_recorder"]
        recorder = _load_final_action_recorder_module()
        self.assertEqual(contract["spec_file"], recorder.SPEC_FILENAME)
        self.assertEqual(contract["schema_version"], recorder.SCHEMA_VERSION)
        self.assertEqual(contract["handoff_mode"], recorder.HANDOFF_MODE)
        self.assertEqual(set(contract["closed_fields"]), recorder.SPEC_FIELDS)
        self.assertEqual(
            contract["derived_fields"], ["expected_head", "expected_branch"]
        )
        self.assertEqual(
            set(contract["output_predicate"]["closed_fields"]),
            recorder.PREDICATE_FIELDS,
        )
        self.assertEqual(
            set(contract["output_predicate"]["modes"]), recorder.OUTPUT_MODES
        )
        _assert_prose_values(
            self,
            {"cleanup_release": contract.get("cleanup_release")},
            {"cleanup_release"},
        )

    def test_known_deadline_reserves_terminal_sprint_and_a_proven_response_margin(
        self,
    ) -> None:
        """Regression: a disproven 15-second margin cannot consume the final response."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        contract = _input_authoring_contract(contents)
        self.assertIn("deadline_reserve_seconds", contract)
        reserve = contract["deadline_reserve_seconds"]
        self.assertEqual(
            reserve,
            {
                "candidate_preparation": 360,
                "terminal_sprint": 60,
                "response_margin_min": 180,
                "total_min": 600,
            },
        )
        self.assertEqual(
            reserve["candidate_preparation"]
            + reserve["terminal_sprint"]
            + reserve["response_margin_min"],
            reserve["total_min"],
        )
        self.assertEqual(
            contract["deadline_checkpoints_seconds_remaining"],
            {
                "enter_candidate_preparation": 600,
                "handoff_start_min": 240,
                "handoff_complete": 180,
            },
        )
        self.assertEqual(
            contract["execution_budget"],
            {
                "pre_charter_tool_actions_max": 4,
                "semantic_actions_max": 8,
                "ordinary_actions_max": 7,
                "final_actions": 1,
            },
        )
        _assert_prose_values(
            self,
            contract["capture_repair"],
            {
                "trigger",
                "safe_replay",
                "parallel_replay",
                "file_change_gate",
                "empty_zero_exit",
                "precedence",
                "accounting",
                "failure",
            },
        )
        self.assertEqual(
            contract["grounding_commands"],
            {
                "bootstrap": [
                    "python3",
                    ".agents/skills/test/scripts/bootstrap_run.py",
                ],
                "head": ["git", "rev-parse", "HEAD"],
                "change": [
                    "git",
                    "show",
                    "--no-ext-diff",
                    "--no-renames",
                    "--format=fuller",
                    "--stat",
                    "--patch",
                    "HEAD",
                ],
            },
        )
        self.assertEqual(contract["default_usable_budget_seconds"], 780)

    def test_authoring_constraint_maps_discriminate_real_input_mutations(self) -> None:
        """Regression: constraint maps are executable guidance, not decorative labels."""

        contract = _input_authoring_contract(
            (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        )
        finalizer = _load_contract_finalizer_module()
        run_root, base_charter, base_ledger, base_draft = self.valid_finalizer_inputs()
        catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
        evidence_contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
        (run_root / "draft-preparation.json").write_text(
            "not evidence\n", encoding="utf-8"
        )

        cases = (
            (
                "nonempty_strings",
                "invalid-type",
                "charter.material_oracles[0].behavior",
                lambda charter, _ledger, _draft: charter["material_oracles"][0].update(
                    {"behavior": ""}
                ),
                True,
            ),
            (
                "cardinality",
                "missing-value",
                "charter.scope.inner_ring",
                lambda charter, _ledger, _draft: charter["scope"].update(
                    {"inner_ring": []}
                ),
                True,
            ),
            (
                "bindings",
                "scope-mismatch",
                "charter.scope.accepted_behavior",
                lambda charter, _ledger, _draft: charter["scope"].update(
                    {"accepted_behavior": "A different behavior."}
                ),
                True,
            ),
            (
                "formats",
                "invalid-timestamp",
                "draft.started_at",
                lambda _charter, _ledger, draft: draft.update(
                    {"started_at": "2026-99-99T99:99:99Z"}
                ),
                False,
            ),
            (
                "paths",
                "invalid-artifact-path",
                "draft.artifacts[0].path",
                lambda _charter, _ledger, draft: draft["artifacts"][0].update(
                    {"path": "draft-preparation.json"}
                ),
                False,
            ),
            (
                "role_path",
                "invalid-action",
                "ledger.entries[0].path",
                lambda _charter, ledger, _draft: ledger["entries"][0].update(
                    {"role": "journey", "path": []}
                ),
                False,
            ),
            (
                "rule_pairs",
                "unsupported-rule-outcome",
                "draft.rule_applicability[0].outcome",
                lambda _charter, _ledger, draft: draft["rule_applicability"][0].update(
                    {"outcome": "not-applicable"}
                ),
                False,
            ),
            (
                "exempt_shape",
                "invalid-exemption",
                "draft.intended_result",
                lambda _charter, _ledger, draft: draft.update(
                    {"intended_result": "EXEMPT"}
                ),
                False,
            ),
            (
                "minimum_result_support",
                "unsupported-result",
                "draft.intended_result",
                lambda charter, _ledger, _draft: charter["material_oracles"][0][
                    "required_action_ids"
                ].append("planned-but-missing"),
                True,
            ),
        )
        expected_maps = {
            "nonempty_strings",
            "cardinality",
            "bindings",
            "formats",
            "paths",
            "role_path",
            "rule_pairs",
            "exempt_shape",
            "minimum_result_support",
        }
        self.assertEqual({case[0] for case in cases}, expected_maps)
        self.assertTrue(expected_maps.issubset(contract))

        for category, code, path, mutate, rebind in cases:
            with self.subTest(category=category):
                charter = copy.deepcopy(base_charter)
                ledger = copy.deepcopy(base_ledger)
                draft = copy.deepcopy(base_draft)
                mutate(charter, ledger, draft)
                if rebind:
                    ledger["charter_digest"] = hashlib.sha256(
                        finalizer.canonical_json(charter)
                    ).hexdigest()
                issues = finalizer.validate_inputs(
                    run_root,
                    charter,
                    ledger,
                    draft,
                    catalog=catalog,
                    contract=evidence_contract,
                )
                self.assertTrue(
                    any(issue.code == code and issue.path == path for issue in issues),
                    [(issue.code, issue.path) for issue in issues],
                )
                if category == "minimum_result_support":
                    self.assertFalse(
                        any(issue.code == "unknown-reference" for issue in issues),
                        [(issue.code, issue.path) for issue in issues],
                    )

    def test_authoring_contract_semantics_are_enforced_by_real_finalizer_inputs(
        self,
    ) -> None:
        """Regression: named protocol guarantees must reject real invalid evidence."""

        contract = _input_authoring_contract(
            (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        )
        finalizer = _load_contract_finalizer_module()
        run_root, base_charter, base_ledger, base_draft = self.valid_finalizer_inputs()
        catalog = json.loads(QUALITY_CATALOG.read_text(encoding="utf-8"))
        evidence_contract = json.loads(EVIDENCE_CONTRACT.read_text(encoding="utf-8"))
        semantic_names = {
            "closed_objects",
            "canonical_root",
            "run_binding",
            "scope_binding",
            "head_binding",
            "derived_charter_binding",
            "nonempty_cardinality",
            "reference_integrity",
            "artifact_path_confinement",
            "reserved_artifact_ids",
            "catalog_order",
            "terminal_unknown_forbidden",
            "exempt_shape",
            "result_support",
        }
        self.assertEqual(set(contract["semantic_guarantees"]), semantic_names)

        def issues_after(mutator, *, rebind: bool = False):
            charter = copy.deepcopy(base_charter)
            ledger = copy.deepcopy(base_ledger)
            draft = copy.deepcopy(base_draft)
            mutator(charter, ledger, draft)
            if rebind:
                ledger["charter_digest"] = hashlib.sha256(
                    finalizer.canonical_json(charter)
                ).hexdigest()
            return finalizer.validate_inputs(
                run_root,
                charter,
                ledger,
                draft,
                catalog=catalog,
                contract=evidence_contract,
            )

        cases = []

        def add_case(name, code, path, mutator, *, rebind=False):
            cases.append((name, code, path, mutator, rebind))

        add_case(
            "closed_objects",
            "unexpected-key",
            "draft.surprise",
            lambda _c, _l, draft: draft.update({"surprise": "not allowed"}),
        )
        add_case(
            "canonical_root",
            "invalid-root",
            "charter.repository",
            lambda charter, _l, _d: charter.update(
                {"repository": str(run_root.parents[1] / "other")}
            ),
            rebind=True,
        )
        add_case(
            "run_binding",
            "run-id-mismatch",
            "charter.run_id",
            lambda charter, ledger, _d: (
                charter.update({"run_id": "another-run"}),
                ledger.update({"run_id": "another-run"}),
            ),
            rebind=True,
        )
        add_case(
            "scope_binding",
            "scope-mismatch",
            "charter.scope.accepted_behavior",
            lambda charter, _l, _d: charter["scope"].update(
                {"accepted_behavior": "Different accepted behavior."}
            ),
            rebind=True,
        )
        add_case(
            "head_binding",
            "wrong-head",
            "ledger.entries[0].head",
            lambda _c, ledger, _d: ledger["entries"][0].update(
                {"head": "b" * 40}
            ),
        )
        add_case(
            "derived_charter_binding",
            "unexpected-key",
            "ledger.charter_digest",
            lambda _c, ledger, _d: ledger.update(
                {"schema_version": "test-action-ledger.v2"}
            ),
        )
        add_case(
            "nonempty_cardinality",
            "missing-value",
            "charter.scope.inner_ring",
            lambda charter, _l, _d: charter["scope"].update({"inner_ring": []}),
            rebind=True,
        )
        add_case(
            "reference_integrity",
            "unknown-reference",
            "ledger.entries[0].oracle_ids",
            lambda _c, ledger, _d: ledger["entries"][0].update(
                {"oracle_ids": ["missing-oracle"]}
            ),
        )
        add_case(
            "artifact_path_confinement",
            "invalid-artifact-path",
            "draft.artifacts[0].path",
            lambda _c, _l, draft: draft["artifacts"][0].update(
                {"path": "../escaped.log"}
            ),
        )
        add_case(
            "reserved_artifact_ids",
            "duplicate-id",
            "draft.artifacts[0].artifact_id",
            lambda _c, ledger, draft: (
                draft["artifacts"][0].update({"artifact_id": "test-charter"}),
                ledger["entries"][0].update({"artifact_ids": ["test-charter"]}),
            ),
        )
        add_case(
            "catalog_order",
            "incomplete-rule-set",
            "draft.rule_applicability",
            lambda _c, _l, draft: draft["rule_applicability"].reverse(),
        )
        add_case(
            "terminal_unknown_forbidden",
            "unknown-state",
            "draft.rule_applicability",
            lambda _c, _l, draft: draft["rule_applicability"][0].update(
                {"status": "unknown", "outcome": "unknown"}
            ),
        )
        add_case(
            "exempt_shape",
            "invalid-exemption",
            "draft.intended_result",
            lambda _c, _l, draft: draft.update({"intended_result": "EXEMPT"}),
        )
        for unsupported in ("PASS", "FAIL", "BLOCKED"):
            def unsupported_result(_c, ledger, draft, result=unsupported):
                draft["intended_result"] = result
                if result == "PASS":
                    ledger["entries"][0]["status"] = "fail"

            add_case(
                "result_support",
                "unsupported-result",
                "draft.intended_result",
                unsupported_result,
            )

        self.assertEqual({case[0] for case in cases}, semantic_names)
        for name, code, path, mutator, rebind in cases:
            with self.subTest(semantic=name, code=code):
                issues = issues_after(mutator, rebind=rebind)
                self.assertTrue(
                    any(issue.code == code and issue.path == path for issue in issues),
                    [(issue.code, issue.path) for issue in issues],
                )

    def test_terminal_evidence_transaction_is_ordered_before_result(self) -> None:
        """Regression: the terminal transaction must precede the Result section."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        _assert_terminal_evidence_transaction(contents)

        transaction_heading = "\n## Terminal evidence transaction\n"
        result_heading = "\n## Result\n"
        for heading in (transaction_heading, result_heading):
            self.assertEqual(contents.count(heading), 1, heading)
        placeholder = "\n## Swap placeholder\n"
        swapped_headings = (
            contents.replace(transaction_heading, placeholder, 1)
            .replace(result_heading, transaction_heading, 1)
            .replace(placeholder, result_heading, 1)
        )
        mutations = (
            (
                "alternate-composer",
                "python3 .agents/skills/test/scripts/record_final_action.py handoff",
                "python3 scripts/custom_handoff.py",
            ),
        )
        for mutation, original, replacement in mutations:
            with self.subTest(mutation=mutation):
                changed = contents.replace(original, replacement, 1)
                self.assertNotEqual(changed, contents)
                with self.assertRaises(AssertionError):
                    _assert_terminal_evidence_transaction(changed)
        with self.subTest(mutation="result-before-transaction"):
            self.assertNotEqual(swapped_headings, contents)
            with self.assertRaisesRegex(
                AssertionError,
                "missing canonical Terminal evidence transaction before Result",
            ):
                _assert_terminal_evidence_transaction(swapped_headings)

    def test_pre_write_ownership_gate_precedes_grounding(self) -> None:
        """Regression: the product-write gate must load before grounding decisions."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        _assert_pre_write_ownership_gate(contents)

        boundary = "## Boundary\n"
        gate = "## Pre-write ownership gate\n"
        grounding = "## Ground and exempt\n"
        marker = "## Pre-write ownership gate marker\n"
        for heading in (boundary, gate, grounding):
            self.assertEqual(contents.count(heading), 1, heading)
        mutations = (
            (
                "gate-after-grounding",
                contents.replace(gate, marker, 1)
                .replace(grounding, gate, 1)
                .replace(marker, grounding, 1),
            ),
            ("gate-missing", contents.replace(gate, "", 1)),
            (
                "interloper-after-boundary",
                contents.replace(boundary, boundary + "\n## Interloper\n", 1),
            ),
        )
        for mutation, changed in mutations:
            with self.subTest(mutation=mutation):
                self.assertNotEqual(changed, contents)
                with self.assertRaisesRegex(
                    AssertionError,
                    "missing early Pre-write ownership gate immediately after Boundary",
                ):
                    _assert_pre_write_ownership_gate(changed)

    def test_charter_preparation_identifiers_and_freezer_are_named(self) -> None:
        """The terminal transaction names the charter preparation contract and its freezer."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        transaction = _markdown_section(contents, "## Terminal evidence transaction")
        for marker in (
            "`charter-preparation.json`",
            "`test-charter-preparation.v1`",
            "`freeze_charter.py`",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, transaction)
        self.assertTrue(CHARTER_FREEZER.is_file())

    def test_ground_section_names_the_shipped_run_bootstrap(self) -> None:
        """The Ground and exempt section names the run bootstrap script, which ships as a file."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        grounding = _markdown_section(contents, "## Ground and exempt")
        self.assertIn("`bootstrap_run.py`", grounding)
        self.assertTrue(RUN_BOOTSTRAP.is_file())

    def test_ledger_batch_identifiers_and_appender_are_named(self) -> None:
        """The execution section names the ledger batch contract and its appender."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        execution = _markdown_section(contents, "## Execute and explore")
        for marker in (
            "`ledger-batch.json`",
            "`test-ledger-batch.v1`",
            "`append_ledger.py`",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, execution)
        self.assertTrue(LEDGER_APPENDER.is_file())

    def test_terminal_transaction_names_handoff_identifiers_and_no_retired_commands(self) -> None:
        """The terminal transaction names the handoff identifiers and omits retired commands."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        transaction = _markdown_section(contents, "## Terminal evidence transaction")
        for marker in (
            "`test-action-ledger.v2`",
            "record_final_action.py handoff",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, transaction)
        collapsed = " ".join(transaction.split())
        self.assertNotIn("record_final_action.py validate", collapsed)
        self.assertNotIn("finalize_evidence.py compose-draft", collapsed)

    def test_result_section_lists_finding_kinds_terminal_states_and_fields(self) -> None:
        """The Result section lists the closed finding kinds, terminal states, and result fields."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        section = _markdown_section(contents, "## Result")
        finding_match = re.search(
            r"^### Finding kinds\s*$\n(?P<body>.*?)(?=^### |\Z)",
            section,
            flags=re.MULTILINE | re.DOTALL,
        )
        state_match = re.search(
            r"^### Terminal states\s*$\n(?P<body>.*?)(?=^### |\Z)",
            section,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(finding_match)
        self.assertIsNotNone(state_match)
        finding_kinds = re.findall(
            r"^- `([^`]+)`: ", finding_match.group("body"), flags=re.MULTILINE
        )
        terminal_states = re.findall(
            r"^- `([^`]+)`: ", state_match.group("body"), flags=re.MULTILINE
        )
        self.assertEqual(finding_kinds, FINDING_KINDS)
        self.assertEqual(terminal_states, TERMINAL_STATES)
        for field in (
            "schema_version",
            "terminal_state",
            "summary",
            "scope",
            "evidence",
            "limitations",
        ):
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", section)
        self.assertNotIn("`skipped`", section.lower())

    def test_entrypoint_is_bounded_free_of_todo_markers_and_banned_words_and_names_recorder(
        self,
    ) -> None:
        """The entrypoint stays under 500 lines, has no [TODO: marker or banned word, and names the recorder."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(contents.splitlines()), 500)
        self.assertNotIn("[TODO:", contents)
        self.assertIsNone(
            re.search(r"\b(?:quick|full|model|caps?)\b", contents, re.I)
        )
        self.assertIn("scripts/record_final_action.py", contents)

    def test_frontmatter_declares_only_the_test_skill_identity_keys(self) -> None:
        """Regression: the Test skill frontmatter must keep its exact key set and name."""

        frontmatter = _frontmatter(TEST_SKILL / "SKILL.md")
        self.assertEqual(set(frontmatter), {"name", "description"})
        self.assertEqual(frontmatter["name"], "test")

    def test_contract_headings_are_unique_and_code_spans_stay_literal(self) -> None:
        """Regression: each Test contract heading appears exactly once and each contract span stays literal."""

        contents = (TEST_SKILL / "SKILL.md").read_text(encoding="utf-8")
        for heading in (
            "## Quality rules",
            "## Scope and charter",
            "## Findings and ownership",
            "## Delegation",
            "## Diagnose and rerun",
            "## Authority and hostile content",
        ):
            with self.subTest(heading=heading):
                self.assertEqual(
                    len(re.findall(rf"^{re.escape(heading)}$", contents, re.MULTILINE)),
                    1,
                )
        for span in (
            "`.agents/skills/test/**`",
            "../../rules/evidence-report.md",
            "`git rev-parse HEAD`",
            "`git show --no-ext-diff --no-renames --format=fuller --stat --patch HEAD`",
            '`{"schema_version":"test-draft-final-delta.v2","resolutions":[]}`',
            "`payload.receipt.head`",
        ):
            with self.subTest(span=span):
                self.assertIn(span, contents)

    def test_validator_rejects_a_duplicate_test_boundary_section(self) -> None:
        """Regression: a second Boundary section cannot shadow the protected one."""

        root = self.copy_repository()
        skill_path = root / "plugins" / "expskill" / "content" / "skills" / "test" / "SKILL.md"
        original = skill_path.read_text(encoding="utf-8")
        skill_path.write_text(
            original + "\n## Boundary\n\nA second boundary section.\n",
            encoding="utf-8",
        )
        self.assertIn(
            "skill 'test' must contain exactly one Boundary section",
            validate_repository(root, include_opencode=False),
        )


if __name__ == "__main__":
    unittest.main()
