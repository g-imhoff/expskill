from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

# Validation is a read-only contract check; do not leave import caches in the
# checkout while exercising the temporary renderer/builder artifact.
sys.dont_write_bytecode = True

try:
    from scripts.build_opencode_package import BuildError as OpencodeBuildError
    from scripts.build_opencode_package import (
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        PLATFORM_PLUGIN_FILES,
        PROVENANCE_SCHEMA_VERSION,
        _provenance_sources,
        build_opencode_package,
    )
    from scripts.render_opencode import RenderError as AgentSyncError
    from scripts.render_opencode import (
        OPENCODE_DESCRIPTION_MAX_LENGTH,
        _bounded_description,
        render_agents as _render_opencode_agents,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )
except ModuleNotFoundError:
    from build_opencode_package import BuildError as OpencodeBuildError
    from build_opencode_package import (
        ARTIFACT_DIRECTORY_MODE,
        ARTIFACT_FILE_MODE,
        ARTIFACT_MTIME,
        COPY_FILES,
        COPY_LICENSES,
        COPY_TREES,
        PLATFORM_FILES,
        PLATFORM_PLUGIN_DIRECTORY,
        PLATFORM_PLUGIN_FILES,
        PROVENANCE_SCHEMA_VERSION,
        _provenance_sources,
        build_opencode_package,
    )
    from render_opencode import RenderError as AgentSyncError
    from render_opencode import (
        OPENCODE_DESCRIPTION_MAX_LENGTH,
        _bounded_description,
        render_agents as _render_opencode_agents,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )


MARKETPLACE_NAME = "expskill"
PLUGIN_NAME = "expskill"
PLUGIN_VERSION = "0.1.0"
PLUGIN_VERSION_PATTERN = re.compile(
    rf"{re.escape(PLUGIN_VERSION)}(?:\+codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
REPOSITORY_URL = "https://github.com/g-imhoff/expskill"
PLUGIN_CATEGORY = "Developer Tools"
SKILLS_PATH = "./skills/"
AGENTS_PATH = "assets/agents"
POLICY_PATH = "assets/execution-policy.json"
HELPER_PATH = "scripts/worktrees.py"
PLAN_GRAPH_HELPER_PATH = "scripts/plan_graph.py"
UNSLOP_HOOK_CONFIG_PATH = "hooks/hooks.json"
UNSLOP_HOOK_SCRIPT_PATH = "hooks/inject_unslop.py"
UNSLOP_HOOK_SCRIPT_SHA256 = "6eea44b9a2fcccfe685c5b93c7fd2b3e868bb9618f6764a7e97557dd4f8f6403"
THIRD_PARTY_LOCK_PATH = "third-party/upstream-lock.json"
PLACEHOLDER = "[TODO:"
PLUGIN_AUTHOR_NAME = "g-imhoff"
LEGACY_PROJECT_IDENTITIES = (
    "-".join(("codex", "dev", "flow")),
    " ".join(("codex", "dev", "flow")),
    "_".join(("codex", "dev", "flow")),
    "use-" + "expand",
    "use_" + "expand",
    "dev" + "flow-",
)
PROJECT_IDENTITY_TEXT_SUFFIXES = {
    ".csv",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
PLUGIN_INTERFACE_FIELDS = {
    "displayName",
    "shortDescription",
    "longDescription",
    "developerName",
    "category",
    "capabilities",
    "defaultPrompt",
}
PUBLIC_SKILL_TOKENS = {
    "$brainstorm",
    "$plan",
    "$implement",
    "$test",
    "$use-expskill",
    "$design",
    "$grill-me",
    "$setup-ui-testing",
    "$skill-builder",
    "$unslop",
}
PUBLIC_SKILL_COUNT_TEXT = "nine independent skills and one optional lifecycle router"
SKILL_BUILDER_TOKEN = "$skill-builder"
SKILL_BUILDER_REQUIRED_REFERENCES = (
    "references/artifact-contracts.md",
    "references/evaluation-rubric.md",
)
SETUP_UI_TESTING_REQUIRED_RESOURCES = (
    "references/capability-contract.md",
    "scripts/inspect_setup.py",
)
SKILL_BUILDER_FORBIDDEN_TOKENS = tuple(
    sorted(PUBLIC_SKILL_TOKENS - {SKILL_BUILDER_TOKEN})
)
SKILL_BUILDER_BOUNDARY_SECTION = (
    "## Boundary\n\n"
    "`$skill-builder` is standalone and explicit-only. Stay inactive for ordinary "
    "development, product planning, application design, documentation that is not an "
    "agent skill, installation-only work, and lifecycle routing. Do not invoke or depend "
    "on a product lifecycle phase or an ambient authoring skill.\n\n"
    "Success exists only when one exact revision has a confirmed contract, frozen "
    "evaluation evidence, isolated trial evidence, builder-run conformance, independent "
    "review, ten independently satisfied target category scores, verification, and "
    "retained release evidence. Static validation alone is never completion.\n\n"
    "Read [artifact contracts](references/artifact-contracts.md) completely at run start "
    "and again before resuming persisted work. Read [evaluation rubric]"
    "(references/evaluation-rubric.md) completely before freezing the evaluation pack "
    "and before every review or scoring pass."
)
SKILL_BUILDER_README_LINES = (
    "- `$skill-builder` creates or improves one exact agent skill through evidence-gated "
    "research, trials, review, and verification.",
    "Use $skill-builder to create or improve one exact agent skill with retained evidence.",
)
SKILL_BUILDER_NAME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])skill(?:-|[ \t]+)builder(?![A-Za-z0-9])",
    re.IGNORECASE,
)
PUBLIC_METADATA_JARGON = re.compile(
    r"\b(?:quick|full|models?|caps?|scaffold|private[- ]marketplace|local plugin)\b",
    re.IGNORECASE,
)
EXPECTED_SKILLS = {
    "use-expskill",
    "design",
    "brainstorm",
    "plan",
    "implement",
    "test",
    "grill-me",
    "setup-ui-testing",
    "skill-builder",
    "unslop",
}
RETIRED_SKILLS = {"full-code-change", "quick-code-change", "route-code-change"}
PUBLIC_SKILL_JARGON = re.compile(r"\b(?:quick|full|model|caps?)\b", re.IGNORECASE)
SETUP_UI_TESTING_ALLOWED_FULL_CONTEXTS = re.compile(
    r"\bfull(?:\s+closed|\s+three-size|-page)\b",
    re.IGNORECASE,
)
# Keep only Test's safety-critical Boundary section closed. Later accepted
# boundary changes update this snapshot explicitly; other sections remain open.
TEST_PROTECTED_BOUNDARY = """`$test` is a standalone, explicit-only skill for behavior that is already
implemented at the exact repository head. Accept a direct request without
requiring `$use-expskill` or a Plan Graph.

Exercise the accepted behavior through a real product path and its material
dependencies in a safe non-production environment. Bind the actions, expected
and observed outcomes, limitations, and result to the exact repository head.

Do not edit production code. Do not plan work, choose a testing framework,
review source or specification compliance, route the lifecycle, integrate
branches, push, or deliver remotely. When the behavior fails or credible
evidence is unavailable, stop and report that result instead of repairing the
product or claiming success."""
TEST_QUALITY_CATALOG_RELATIVE = "skills/test/references/quality-rules.json"
TEST_QUALITY_CATALOG_VERSION = "test-quality-rules.v1"
TEST_QUALITY_CATALOG_FIELDS = {"schema_version", "rules"}
TEST_QUALITY_RULE_FIELDS = {
    "id",
    "level",
    "applies_when",
    "requirement",
    "failure_prevented",
    "required_evidence",
    "allowed_exceptions",
}
TEST_QUALITY_EXCEPTION_FIELDS = {"predicate", "required_evidence"}
TEST_QUALITY_APPLICABILITY = {
    "always",
    "durable-test-added",
    "ui-material",
    "persistence-material",
    "messaging-material",
    "contract-material",
    "external-service-material",
    "parallel-run",
    "accessibility-material",
    "visual-material",
}
TEST_QUALITY_RULE_ID_PATTERN = re.compile(
    r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*)+\Z"
)
TEST_EVIDENCE_CONTRACT_RELATIVE = "skills/test/references/evidence-contract.json"
TEST_EVIDENCE_CONTRACT_VERSION = "test-evidence-contract.v1"
TEST_EVIDENCE_CONTRACT_FIELDS = {
    "schema_version",
    "terminal_states",
    "finding_kinds",
    "bundle",
    "receipt",
    "finding",
}
TEST_EVIDENCE_TERMINAL_STATES = ["PASS", "FAIL", "BLOCKED", "EXEMPT"]
TEST_EVIDENCE_FINDING_KINDS = [
    "product-defect",
    "test-system-defect",
    "environment-blocker",
    "unresolved-cause",
]
TEST_EVIDENCE_SHA256_PATTERN = "^[0-9a-f]{64}$"
TEST_EVIDENCE_HEAD_PATTERN = "^[0-9a-f]{40,64}$"
TEST_EVIDENCE_BUNDLE_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the complete "
    "bundle with only the bundle_digest field omitted"
)
TEST_EVIDENCE_ENVIRONMENT_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the complete "
    "recorded environment identity used for this run"
)
TEST_EVIDENCE_SCOPE_DIGEST_SEMANTICS = (
    "SHA-256 lowercase hex over the RFC 8785 canonical JSON of the selected "
    "three-ring scope recorded in the retained bundle"
)


def _test_evidence_string_schema(
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


def _test_evidence_workflow_schema() -> dict[str, object]:
    return {
        "type": ["string", "null"],
        "min_length": 1,
        "nullable_when": "direct invocation without Plan ancestry",
    }


def _test_evidence_array_schema(
    items: dict[str, object], *, min_items: int = 0, unique_items: bool = False
) -> dict[str, object]:
    return {
        "type": "array",
        "items": items,
        "min_items": min_items,
        "unique_items": unique_items,
    }


def _test_evidence_object_schema(
    properties: dict[str, object],
) -> dict[str, object]:
    return {
        "type": "object",
        "additional_properties": False,
        "required": list(properties),
        "properties": properties,
    }


TEST_EVIDENCE_ARTIFACT_SCHEMA = _test_evidence_object_schema(
    {
        "artifact_id": _test_evidence_string_schema(),
        "kind": _test_evidence_string_schema(
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
        "path": _test_evidence_string_schema(),
        "sha256": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics="SHA-256 lowercase hex over the retained artifact bytes",
        ),
    }
)
TEST_EVIDENCE_FINDING_SCHEMA = _test_evidence_object_schema(
    {
        "kind": _test_evidence_string_schema(enum=TEST_EVIDENCE_FINDING_KINDS),
        "severity": _test_evidence_string_schema(
            enum=["critical", "high", "medium", "low"]
        ),
        "repository": _test_evidence_string_schema(),
        "head": _test_evidence_string_schema(pattern=TEST_EVIDENCE_HEAD_PATTERN),
        "environment_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics=TEST_EVIDENCE_ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "ring": _test_evidence_string_schema(
            enum=["inner", "adjacent", "broader"]
        ),
        "journey": _test_evidence_string_schema(),
        "expected": _test_evidence_string_schema(),
        "actual": _test_evidence_string_schema(),
        "reproduction": _test_evidence_array_schema(
            _test_evidence_string_schema(), min_items=1
        ),
        "violated_rule_ids": _test_evidence_array_schema(
            _test_evidence_string_schema(pattern="^[a-z][a-z0-9.-]+$"),
            unique_items=True,
        ),
        "artifacts": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
    }
)
TEST_EVIDENCE_SCOPE_SCHEMA = _test_evidence_object_schema(
    {
        "accepted_behavior": _test_evidence_string_schema(),
        "inner_ring": _test_evidence_array_schema(
            _test_evidence_string_schema(), min_items=1, unique_items=True
        ),
        "adjacent_ring": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
        "broader_ring": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
    }
)
TEST_EVIDENCE_RULE_APPLICABILITY_SCHEMA = _test_evidence_object_schema(
    {
        "rule_id": _test_evidence_string_schema(pattern="^[a-z][a-z0-9.-]+$"),
        "status": _test_evidence_string_schema(
            enum=["active", "inactive", "unknown"]
        ),
        "evidence": _test_evidence_array_schema(
            _test_evidence_string_schema(), min_items=1
        ),
    }
)
TEST_EVIDENCE_CHECK_SCHEMA = _test_evidence_object_schema(
    {
        "check_id": _test_evidence_string_schema(),
        "ring": _test_evidence_string_schema(
            enum=["inner", "adjacent", "broader"]
        ),
        "action": _test_evidence_string_schema(),
        "expected": _test_evidence_string_schema(),
        "actual": _test_evidence_string_schema(),
        "status": _test_evidence_string_schema(enum=["pass", "fail", "blocked"]),
        "artifact_ids": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
    }
)
TEST_EVIDENCE_JOURNEY_SCHEMA = _test_evidence_object_schema(
    {
        "journey_id": _test_evidence_string_schema(),
        "ring": _test_evidence_string_schema(
            enum=["inner", "adjacent", "broader"]
        ),
        "path": _test_evidence_array_schema(
            _test_evidence_string_schema(), min_items=1
        ),
        "expected": _test_evidence_string_schema(),
        "actual": _test_evidence_string_schema(),
        "status": _test_evidence_string_schema(enum=["pass", "fail", "blocked"]),
        "artifact_ids": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
    }
)
TEST_EVIDENCE_EXPLORATION_SCHEMA = _test_evidence_object_schema(
    {
        "mission": _test_evidence_string_schema(),
        "evidence_budget": _test_evidence_string_schema(),
        "actions": _test_evidence_array_schema(_test_evidence_string_schema()),
        "observations": _test_evidence_array_schema(_test_evidence_string_schema()),
        "stop_condition": _test_evidence_string_schema(),
        "teardown": _test_evidence_array_schema(_test_evidence_string_schema()),
    }
)
TEST_EVIDENCE_TEARDOWN_SCHEMA = _test_evidence_object_schema(
    {
        "status": _test_evidence_string_schema(
            enum=["pass", "fail", "not-required"]
        ),
        "actions": _test_evidence_array_schema(_test_evidence_string_schema()),
        "artifact_ids": _test_evidence_array_schema(
            _test_evidence_string_schema(), unique_items=True
        ),
    }
)
TEST_EVIDENCE_BUNDLE_SCHEMA = _test_evidence_object_schema(
    {
        "schema_version": _test_evidence_string_schema(
            constant="test-evidence-bundle.v1"
        ),
        "run_id": _test_evidence_string_schema(),
        "workflow_id": _test_evidence_workflow_schema(),
        "repository": _test_evidence_string_schema(),
        "branch": _test_evidence_string_schema(),
        "head": _test_evidence_string_schema(pattern=TEST_EVIDENCE_HEAD_PATTERN),
        "environment_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics=TEST_EVIDENCE_ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "scope": TEST_EVIDENCE_SCOPE_SCHEMA,
        "rule_applicability": _test_evidence_array_schema(
            TEST_EVIDENCE_RULE_APPLICABILITY_SCHEMA
        ),
        "checks": _test_evidence_array_schema(TEST_EVIDENCE_CHECK_SCHEMA),
        "journeys": _test_evidence_array_schema(TEST_EVIDENCE_JOURNEY_SCHEMA),
        "exploration": TEST_EVIDENCE_EXPLORATION_SCHEMA,
        "findings": _test_evidence_array_schema(TEST_EVIDENCE_FINDING_SCHEMA),
        "artifacts": _test_evidence_array_schema(TEST_EVIDENCE_ARTIFACT_SCHEMA),
        "teardown": TEST_EVIDENCE_TEARDOWN_SCHEMA,
        "test_side_commits": _test_evidence_array_schema(
            _test_evidence_string_schema(pattern=TEST_EVIDENCE_HEAD_PATTERN),
            unique_items=True,
        ),
        "limitations": _test_evidence_array_schema(_test_evidence_string_schema()),
        "result": _test_evidence_string_schema(enum=TEST_EVIDENCE_TERMINAL_STATES),
        "bundle_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics=TEST_EVIDENCE_BUNDLE_DIGEST_SEMANTICS,
        ),
        "started_at": _test_evidence_string_schema(
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z$"
        ),
        "completed_at": _test_evidence_string_schema(
            pattern="^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+Z$"
        ),
    }
)
TEST_EVIDENCE_RECEIPT_SCHEMA = _test_evidence_object_schema(
    {
        "schema_version": _test_evidence_string_schema(
            constant="test-evidence-receipt.v1"
        ),
        "run_id": _test_evidence_string_schema(),
        "workflow_id": _test_evidence_workflow_schema(),
        "repository": _test_evidence_string_schema(),
        "branch": _test_evidence_string_schema(),
        "head": _test_evidence_string_schema(pattern=TEST_EVIDENCE_HEAD_PATTERN),
        "environment_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics=TEST_EVIDENCE_ENVIRONMENT_DIGEST_SEMANTICS,
        ),
        "selected_scope_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics=TEST_EVIDENCE_SCOPE_DIGEST_SEMANTICS,
        ),
        "bundle_digest": _test_evidence_string_schema(
            pattern=TEST_EVIDENCE_SHA256_PATTERN,
            semantics="Exact bundle_digest from the retained evidence bundle",
        ),
        "test_side_commits": _test_evidence_array_schema(
            _test_evidence_string_schema(pattern=TEST_EVIDENCE_HEAD_PATTERN),
            unique_items=True,
        ),
        "result": _test_evidence_string_schema(enum=TEST_EVIDENCE_TERMINAL_STATES),
    }
)
TEST_EVIDENCE_EXPECTED_SCHEMAS = {
    "bundle": TEST_EVIDENCE_BUNDLE_SCHEMA,
    "receipt": TEST_EVIDENCE_RECEIPT_SCHEMA,
    "finding": TEST_EVIDENCE_FINDING_SCHEMA,
}
BRAINSTORM_CATALOG_RELATIVE = "skills/brainstorm/references/brainstorm-techniques.csv"
BRAINSTORM_CATALOG_SHA256 = "0ab5878b1dbc9e3fa98cb72abfc3920a586b9e2b42609211bb0516eefd542039"
BRAINSTORM_CATALOG_PREAMBLE = (
    "# Source: https://github.com/bmad-code-org/BMAD-METHOD/blob/"
    "890fcda760bade4d6080f5fa09aa8f658bc4a4a5/"
    "web-bundles/brainstorming-coach/brain-methods.csv\n"
    "# Source-Revision: 890fcda760bade4d6080f5fa09aa8f658bc4a4a5\n"
    "# Upstream-SHA256: 0ab5878b1dbc9e3fa98cb72abfc3920a586b9e2b42609211bb0516eefd542039\n"
    "#\n"
    "# MIT License\n"
    "#\n"
    "# Copyright (c) 2025 BMad Code, LLC\n"
    "#\n"
    "# This project incorporates contributions from the open source community.\n"
    "# See [CONTRIBUTORS.md](CONTRIBUTORS.md) for contributor attribution.\n"
    "#\n"
    "# Permission is hereby granted, free of charge, to any person obtaining a copy\n"
    "# of this software and associated documentation files (the \"Software\"), to deal\n"
    "# in the Software without restriction, including without limitation the rights\n"
    "# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
    "# copies of the Software, and to permit persons to whom the Software is\n"
    "# furnished to do so, subject to the following conditions:\n"
    "#\n"
    "# The above copyright notice and this permission notice shall be included in all\n"
    "# copies or substantial portions of the Software.\n"
    "#\n"
    "# THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n"
    "# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
    "# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
    "# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
    "# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
    "# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
    "# SOFTWARE.\n"
    "#\n"
    "# TRADEMARK NOTICE:\n"
    "# BMad™, BMad Method™, and BMad Core™ are trademarks of BMad Code, LLC, covering all\n"
    "# casings and variations (including BMAD, bmad, BMadMethod, BMAD-METHOD, etc.). The use of\n"
    "# these trademarks in this software does not grant any rights to use the trademarks\n"
    "# for any other purpose. See [TRADEMARK.md](TRADEMARK.md) for detailed guidelines.\n"
    "#\n"
)

EXPECTED_THIRD_PARTY_SOURCES = {
    "pstack-unslop": {
        "repository": "https://github.com/cursor/plugins",
        "revision": "93b00b89ef425a9c1bac0d0b317dfc49c930ac99",
        "source_path": "pstack/skills/unslop/SKILL.md",
        "vendored_path": "sources/pstack/unslop/SKILL.md",
        "sha256": "2789ab80477b7e382292e4d7acca1057784df19713fffb74622ff0f83b2f3733",
        "license_path": "licenses/pstack-MIT.txt",
        "license_sha256": "bc957ca6bee02792566a1a028d105e02e247c6e77cf057061674273da77b200e",
    },
    "mattpocock-grill-me": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grill-me/SKILL.md",
        "vendored_path": "sources/mattpocock/grill-me/SKILL.md",
        "sha256": "caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
    "mattpocock-grilling": {
        "repository": "https://github.com/mattpocock/skills",
        "revision": "3cca18b368ae95cdbdebbff572ccafa662551015",
        "source_path": "skills/productivity/grilling/SKILL.md",
        "vendored_path": "sources/mattpocock/grilling/SKILL.md",
        "sha256": "10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2",
        "license_path": "licenses/mattpocock-skills-MIT.txt",
        "license_sha256": "0e7ac423bf2c6e223b7c5b156f8cf72da49d748e56a1641402c31f22ad07dbb5",
    },
}

EXPECTED_UNSLOP_HOOKS = {
    "description": "Apply Unslop to prose written by the root conversation.",
    "hooks": {
        "SessionStart": [
            {
                "matcher": "^(startup|resume|clear|compact)$",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "${PLUGIN_ROOT}/hooks/inject_unslop.py"',
                        "timeout": 3,
                        "additionalContextLimit": 5000,
                    }
                ],
            }
        ]
    },
}

EXPECTED_AGENTS = {
    "expskill-explorer": ("gpt-5.6-luna", "max", "read-only"),
    "expskill-test-engineer": ("gpt-5.6-luna", "max", "read-only"),
    "expskill-planner": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-designer": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-implementer": ("gpt-5.6-luna", "max", "workspace-write"),
    "expskill-review": ("gpt-5.6-sol", "xhigh", "read-only"),
    "expskill-spec": ("gpt-5.6-sol", "xhigh", "read-only"),
}

REVIEW_HANDOFF_PATHS = (
    "skills/implement/SKILL.md",
    "skills/skill-builder/SKILL.md",
    "skills/skill-builder/references/evaluation-rubric.md",
)
REVIEW_HANDOFF_HEADING = "## Review context contract\n"
REVIEW_HANDOFF_CLAUSES = (
    "this final section is the only authoritative review-context policy in this file.",
    "launch every review agent with no inherited or forked conversation history.",
    "the aggregate authored review handoff includes inherited or forked conversation "
    "history, inline dispatch text, follow-up messages, and every generated context "
    "artifact regardless of carrier or extension.",
    "it is a locator, not a payload, and totals at most 300 physical lines.",
    "count the complete handoff before launch and before every follow-up.",
    "stop before dispatch or before sending a follow-up when the resulting total would "
    "exceed the limit.",
    "a real accepted specification file is referenced separately when it exists.",
    "the exception applies only to a specification file that existed before review "
    "dispatch.",
    "it does not permit a review-time summary, copy, or relabelled context package.",
    "do not copy or embed diffs, source files, test logs, terminal output, transcripts, "
    "or other repository content.",
    "do not attach binary or opaque review context.",
)
REVIEW_HANDOFF_CANONICAL_SHA256 = {
    "skills/implement/SKILL.md": "49c97c7e9530baf2e4f42d81972dd1edf0485a8d7fb2a62dbc26ff28920c9704",
    "skills/skill-builder/SKILL.md": "49c97c7e9530baf2e4f42d81972dd1edf0485a8d7fb2a62dbc26ff28920c9704",
    "skills/skill-builder/references/evaluation-rubric.md": "49c97c7e9530baf2e4f42d81972dd1edf0485a8d7fb2a62dbc26ff28920c9704",
}
REVIEW_AGENT_HANDOFF_CLAUSES = (
    "accept only a locator handoff whose aggregate authored review context includes "
    "inherited or forked conversation history, inline dispatch text, follow-up messages, "
    "and generated context artifacts regardless of carrier or extension, and totals at "
    "most 300 physical lines.",
    "prefer a context-free launch.",
    "recount the total after every follow-up.",
    "a real accepted specification file may be referenced separately only when it "
    "existed before review dispatch.",
    "if the total is unknown or exceeds the limit, or the handoff includes a review-time "
    "summary, copied repository content, a binary payload, or an opaque attachment, stop "
    "and return `invalid handoff` without a review verdict.",
    "self-inspect the pinned repository or candidate and its base and candidate "
    "revisions using repository tools.",
    "do not request a copied diff, source files, test logs, terminal output, or "
    "transcripts.",
)
REVIEW_AGENT_INSTRUCTIONS_CANONICAL_SHA256 = {
    "expskill-review": "1a8b62670b6c6ed69ac4ecae3992ecf2996c0103b6a599b5c433815ca29364ab",
    "expskill-spec": "5e9e5b4e98c4e2016a6335f09b1f0681434172e74ff184f058211af0224d2e4c",
}

REQUIRED_AGENT_FIELDS = (
    "name",
    "description",
    "model",
    "model_reasoning_effort",
    "sandbox_mode",
    "developer_instructions",
)

AGENT_BOUNDARIES = {
    "expskill-explorer": ("read-only", "no fixes", "no delegation"),
    "expskill-test-engineer": (
        "test strategy",
        "shared acceptance tests",
        "regression",
        "no product implementation",
    ),
    "expskill-implementer": (
        "exactly one accepted node",
        "red-green-refactor",
        "one owned branch",
        "no delegation",
        "no scope expansion",
    ),
    "expskill-planner": (
        "private plan graph",
        "never edit",
        "only plan graph writer",
        "do not delegate",
    ),
    "expskill-designer": (
        "isolated helper-owned worktree",
        "one coherent local candidate commit",
        "do not write the plan graph",
        "do not delegate",
    ),
    "expskill-review": REVIEW_AGENT_HANDOFF_CLAUSES + (
        "read-only",
        "severity",
        "evidence",
        "impact",
        "correction",
        "ready",
        "not ready",
    ),
    "expskill-spec": REVIEW_AGENT_HANDOFF_CLAUSES + (
        "every accepted behavior",
        "criterion-by-criterion evidence",
        "no tracked-source edits",
        "pass or fail",
        "do not implement fixes",
        "do not expand scope",
        "do not delegate",
    ),
}

EXPECTED_POLICY_PROFILES = {
    "expskill-explorer": {
        "agent_type": "expskill-explorer",
        "role": "explorer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "expskill-test-engineer": {
        "agent_type": "expskill-test-engineer",
        "role": "test-engineer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "expskill-implementer": {
        "agent_type": "expskill-implementer",
        "role": "implementer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "expskill-planner": {
        "agent_type": "expskill-planner",
        "role": "planner",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "expskill-designer": {
        "agent_type": "expskill-designer",
        "role": "designer",
        "model": "gpt-5.6-luna",
        "effort": "max",
        "sandbox_mode": "workspace-write",
        "escalation": None,
    },
    "expskill-review": {
        "agent_type": "expskill-review",
        "role": "review",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
    "expskill-spec": {
        "agent_type": "expskill-spec",
        "role": "spec",
        "model": "gpt-5.6-sol",
        "effort": "xhigh",
        "sandbox_mode": "read-only",
        "escalation": None,
    },
}

EXPECTED_POLICY_ROUTES = {
    "use-expskill": {
        "parallel-plan-design": {
            "allowed_profiles": [
                "expskill-planner",
                "expskill-designer",
            ],
            "selected": [
                {
                    "role": "planner",
                    "profile": "expskill-planner",
                    "agent_type": "expskill-planner",
                },
                {
                    "role": "designer",
                    "profile": "expskill-designer",
                    "agent_type": "expskill-designer",
                },
            ],
            "max_agent_calls": 2,
            "max_concurrency": 2,
            "max_elapsed_ms": 7200000,
        },
    },
    "implement": {
        "standard": {
            "allowed_profiles": [
                "expskill-implementer",
                "expskill-review",
                "expskill-spec",
            ],
            "selected": [
                {
                    "role": "implementer",
                    "profile": "expskill-implementer",
                    "agent_type": "expskill-implementer",
                },
                {
                    "role": "review",
                    "profile": "expskill-review",
                    "agent_type": "expskill-review",
                },
                {
                    "role": "spec",
                    "profile": "expskill-spec",
                    "agent_type": "expskill-spec",
                },
            ],
            "max_agent_calls": 30,
            "max_concurrency": 6,
            "max_elapsed_ms": 7200000,
        },
    },
}


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _is_symlink(path: Path) -> bool:
    metadata = _lstat(path)
    return metadata is not None and stat.S_ISLNK(metadata.st_mode)


def _symlink_component(root: Path, relative: str) -> Path | None:
    current = root
    if _is_symlink(current):
        return current
    for component in Path(relative).parts:
        current /= component
        if _is_symlink(current):
            return current
    return None


def _validate_plugin_root(plugin_root: Path, errors: list[str]) -> bool:
    if _is_symlink(plugin_root):
        errors.append(f"plugin root must not be a symlink: {plugin_root}")
        return False
    metadata = _lstat(plugin_root)
    if metadata is None:
        errors.append(f"plugin directory is missing: {plugin_root}")
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"plugin root must be a directory: {plugin_root}")
        return False
    try:
        resolved = plugin_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        errors.append(f"plugin root cannot be resolved: {plugin_root}: {error}")
        return False
    if resolved != plugin_root:
        errors.append(f"plugin root resolves outside its lexical path: {plugin_root}")
        return False
    return True


def _required_package_path(
    plugin_root: Path,
    relative: str,
    label: str,
    kind: str,
    errors: list[str],
    missing_label: str | None = None,
) -> Path | None:
    path = plugin_root / relative
    symlink = _symlink_component(plugin_root, relative)
    if symlink is not None:
        errors.append(f"{label} contains a symlink: {symlink}")
        return None
    try:
        canonical_root = plugin_root.resolve(strict=True)
        resolved = path.resolve(strict=False)
        resolved.relative_to(canonical_root)
    except ValueError:
        errors.append(f"{label} resolves outside the plugin root: {path}")
        return None
    except (OSError, RuntimeError) as error:
        errors.append(f"{label} cannot be resolved: {path}: {error}")
        return None
    metadata = _lstat(path)
    if metadata is None:
        errors.append(f"{missing_label or label} is missing: {path}")
        return None
    if kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"{label} must be a directory: {path}")
        return None
    if kind == "file" and not stat.S_ISREG(metadata.st_mode):
        errors.append(f"{label} must be a regular file: {path}")
        return None
    return path


def _required_nonempty_package_file(
    package_root: Path,
    relative: str,
    label: str,
    errors: list[str],
) -> Path | None:
    path = _required_package_path(
        package_root,
        relative,
        label,
        "file",
        errors,
    )
    if path is None:
        return None
    metadata = _lstat(path)
    if metadata is None or metadata.st_size == 0:
        errors.append(f"{label} must be a non-empty regular file: {path}")
        return None
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"{label} must be readable UTF-8 text: {path}: {error}")
        return None
    return path


def _contains_exact_skill_token(contents: str, token: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])",
        contents,
    ) is not None


def _markdown_level_two_section(contents: str, heading: str) -> str | None:
    lines = contents.splitlines()
    marker = f"## {heading}"
    starts = [index for index, line in enumerate(lines) if line == marker]
    if len(starts) != 1:
        return None
    start = starts[0]
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end]).rstrip()


def _lexical_package_entries(plugin_root: Path) -> list[tuple[Path, os.stat_result]]:
    """Enumerate package entries without traversing symlink directories."""

    pending = [plugin_root]
    entries: list[tuple[Path, os.stat_result]] = []
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
                for child in children:
                    try:
                        metadata = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    child_path = Path(child.path)
                    entries.append((child_path, metadata))
                    if stat.S_ISDIR(metadata.st_mode):
                        pending.append(child_path)
        except OSError:
            continue
    return entries


def validate_repository(root: Path) -> tuple[str, ...]:
    repository_root = Path(root).expanduser()
    try:
        repository_root = repository_root.resolve(strict=True)
    except (OSError, RuntimeError):
        repository_root = repository_root.resolve(strict=False)
    errors: list[str] = []
    marketplace_path = repository_root / ".agents" / "plugins" / "marketplace.json"
    marketplace = _load_json_object(marketplace_path, "marketplace.json", errors)
    if marketplace is not None:
        _validate_marketplace(marketplace, repository_root, errors)

    plugin_root = repository_root / "packages" / "expskill"
    if _validate_plugin_root(plugin_root, errors):
        manifest_path = _required_package_path(
            plugin_root,
            ".codex-plugin/plugin.json",
            "plugin manifest",
            "file",
            errors,
        )
        manifest = (
            _load_json_object(manifest_path, "plugin.json", errors)
            if manifest_path is not None
            else None
        )
        if manifest is not None:
            _validate_plugin_manifest(manifest, plugin_root, errors)

        _validate_agents(plugin_root, errors)
        _validate_review_handoff_contract(plugin_root, errors)
        _validate_policy(plugin_root, errors)
        _validate_unslop_hook(plugin_root, errors)
        _validate_third_party_sources(plugin_root, errors)
        _validate_helper_and_package_layout(plugin_root, errors)
    _validate_public_readme(repository_root, errors)
    _validate_removed_repository_local_skill(repository_root, errors)
    _validate_skill_punctuation(repository_root, errors)
    _validate_no_legacy_project_identity(repository_root, errors)
    _validate_opencode_package(repository_root, errors)
    return tuple(errors)


def _validate_review_handoff_contract(plugin_root: Path, errors: list[str]) -> None:
    for relative in REVIEW_HANDOFF_PATHS:
        path = plugin_root / relative
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"review handoff contract could not be read at {relative}: {error}")
            continue
        normalized = " ".join(contents.lower().split())
        normalized_markdown = contents.replace("\r\n", "\n").replace("\r", "\n")
        heading_index = _review_contract_heading_index(normalized_markdown)
        if heading_index is None:
            errors.append(
                f"review handoff contract at {relative} must contain one final "
                "Review context contract section"
            )
        elif not _review_contract_heading_is_live(normalized_markdown, heading_index):
            errors.append(
                f"review handoff contract at {relative} must begin at a live top-level "
                "Markdown heading"
            )
        for clause in REVIEW_HANDOFF_CLAUSES:
            if clause not in normalized:
                errors.append(
                    f"review handoff contract at {relative} must include {clause!r}"
                )
        canonical = _canonical_review_markdown(contents)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != REVIEW_HANDOFF_CANONICAL_SHA256[relative]:
            errors.append(
                f"review handoff contract at {relative} differs from its validated "
                "normalized content"
            )


def _canonical_review_markdown(contents: str) -> str:
    normalized = contents.replace("\r\n", "\n").replace("\r", "\n")
    heading_index = _review_contract_heading_index(normalized)
    if heading_index is None:
        return normalized
    return normalized[heading_index:]


def _review_contract_heading_index(contents: str) -> int | None:
    matches: list[int] = []
    offset = 0
    for line in contents.splitlines(keepends=True):
        if line == REVIEW_HANDOFF_HEADING:
            matches.append(offset)
        offset += len(line)
    return matches[0] if len(matches) == 1 else None


def _review_contract_heading_is_live(contents: str, heading_index: int) -> bool:
    prefix = contents[:heading_index]
    fence_character: str | None = None
    fence_length = 0
    html_closer: str | None = None

    for line in prefix.splitlines():
        if fence_character is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                line,
            )
            if closing is not None:
                fence_character = None
                fence_length = 0
            continue

        if html_closer is not None:
            closer_index = line.lower().find(html_closer.lower())
            if closer_index < 0:
                continue
            line = line[closer_index + len(html_closer) :]
            html_closer = None

        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            continue

        remaining = line
        while "<!--" in remaining:
            opener = remaining.index("<!--")
            closer = remaining.find("-->", opener + 4)
            if closer < 0:
                html_closer = "-->"
                break
            remaining = remaining[closer + 3 :]
        if html_closer is not None:
            continue

        raw_html = re.match(
            r"^ {0,3}<(script|pre|style|textarea)(?:\s|>|$)",
            line,
            re.IGNORECASE,
        )
        if raw_html is not None:
            closer = f"</{raw_html.group(1)}>"
            if closer.lower() not in line[raw_html.end() :].lower():
                html_closer = closer

    return fence_character is None and html_closer is None


def _canonical_review_agent_instructions(contents: str) -> str:
    normalized = contents.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    has_structure = any(
        not line or line.startswith((" ", "\t")) or line.endswith("  ")
        for line in lines
    )
    return normalized if has_structure else " ".join(lines)


def _load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"{label} could not be read: {error}")
        return None
    except json.JSONDecodeError as error:
        errors.append(f"{label} is not valid JSON: {error.msg}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    _reject_placeholders(payload, label, errors)
    return payload


def _reject_placeholders(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        if PLACEHOLDER in value:
            errors.append(f"{path} contains a placeholder")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_placeholders(item, f"{path}[{index}]", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders(item, f"{path}.{key}", errors)


def _validate_marketplace(
    marketplace: dict[str, Any], repository_root: Path, errors: list[str]
) -> None:
    if marketplace.get("name") != MARKETPLACE_NAME:
        errors.append(
            f"marketplace name must be {MARKETPLACE_NAME!r}, got {marketplace.get('name')!r}"
        )
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        errors.append("marketplace plugins must be an array")
        return

    plugin_names: list[str] = []
    matching_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(plugins):
        label = f"marketplace plugins[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name must be a non-empty string")
            continue
        plugin_names.append(name)
        if name != PLUGIN_NAME:
            continue
        matching_entries.append(entry)
        source = entry.get("source")
        if not isinstance(source, dict):
            errors.append(f"{label}.source must be an object")
        else:
            if source.get("source") != "local":
                errors.append(f"{label}.source.source must be 'local'")
            source_path = source.get("path")
            if source_path != "./packages/expskill":
                errors.append(
                    f"{label}.source.path must be './packages/expskill', got {source_path!r}"
                )
            elif not (repository_root / "packages" / "expskill").is_dir():
                errors.append(f"{label}.source.path does not resolve to the plugin directory")
        policy = entry.get("policy")
        if not isinstance(policy, dict):
            errors.append(f"{label}.policy must be an object")
        else:
            if policy.get("installation") != "AVAILABLE":
                errors.append(f"{label}.policy.installation must be 'AVAILABLE'")
            if policy.get("authentication") != "ON_INSTALL":
                errors.append(f"{label}.policy.authentication must be 'ON_INSTALL'")
        if entry.get("category") != PLUGIN_CATEGORY:
            errors.append(f"{label}.category must be {PLUGIN_CATEGORY!r}")

    duplicates = sorted({name for name in plugin_names if plugin_names.count(name) > 1})
    for name in duplicates:
        errors.append(f"marketplace plugin name {name!r} is duplicated")
    if not matching_entries:
        errors.append("marketplace is missing plugin 'expskill'")
    elif len(matching_entries) > 1:
        errors.append("marketplace plugin 'expskill' is duplicated")


def _validate_plugin_manifest(
    manifest: dict[str, Any], plugin_root: Path, errors: list[str]
) -> None:
    if manifest.get("name") != PLUGIN_NAME:
        errors.append(f"plugin name must be {PLUGIN_NAME!r}, got {manifest.get('name')!r}")
    version = manifest.get("version")
    if not isinstance(version, str) or PLUGIN_VERSION_PATTERN.fullmatch(version) is None:
        errors.append(
            f"plugin version must be {PLUGIN_VERSION!r} or a Codex cachebuster, got {version!r}"
        )
    if manifest.get("repository") != REPOSITORY_URL:
        errors.append(
            f"plugin repository must be {REPOSITORY_URL!r}, got {manifest.get('repository')!r}"
        )
    if manifest.get("skills") != SKILLS_PATH:
        errors.append(
            f"plugin skills path must be {SKILLS_PATH!r}, got {manifest.get('skills')!r}"
        )
    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip() or len(description) > 120:
        errors.append("plugin description must be a non-empty string of at most 120 characters")
    else:
        normalized_description = description.lower()
        if PUBLIC_SKILL_COUNT_TEXT not in normalized_description:
            errors.append(
                "plugin description must advertise nine independent skills and one optional "
                "lifecycle router"
            )
        if PUBLIC_METADATA_JARGON.search(description):
            errors.append("plugin description exposes private implementation or scaffold jargon")
    if manifest.get("author") != {"name": PLUGIN_AUTHOR_NAME}:
        errors.append(
            f"plugin author must identify {PLUGIN_AUTHOR_NAME!r}, got {manifest.get('author')!r}"
        )

    forbidden_fields = {"hooks", "mcpServers", "apps", "icons", "authentication"}
    for field in sorted(forbidden_fields.intersection(manifest)):
        errors.append(f"plugin manifest must not define {field!r}")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin interface must be an object")
    else:
        missing = sorted(PLUGIN_INTERFACE_FIELDS - set(interface))
        unexpected = sorted(set(interface) - PLUGIN_INTERFACE_FIELDS)
        if missing:
            errors.append(f"plugin interface is missing fields: {missing!r}")
        if unexpected:
            errors.append(f"plugin interface has unexpected fields: {unexpected!r}")
        if interface.get("displayName") != "ExpSkill":
            errors.append("plugin interface displayName must preserve the product identity")
        if interface.get("developerName") != PLUGIN_AUTHOR_NAME:
            errors.append("plugin interface developerName must match the plugin author")
        if interface.get("category") != PLUGIN_CATEGORY:
            errors.append(
                f"plugin interface category must be {PLUGIN_CATEGORY!r}, got {interface.get('category')!r}"
            )
        if interface.get("capabilities") != []:
            errors.append("plugin interface capabilities must be an empty list for this skills-only plugin")

        short_description = interface.get("shortDescription")
        if (
            not isinstance(short_description, str)
            or not short_description.strip()
            or len(short_description) > 80
            or "skills" not in short_description.lower()
            or "routing" not in short_description.lower()
            or PUBLIC_METADATA_JARGON.search(short_description) is not None
        ):
            errors.append(
                "plugin interface shortDescription must concisely advertise skills and optional routing "
                "without private implementation or scaffold jargon"
            )

        long_description = interface.get("longDescription")
        if not isinstance(long_description, str) or not long_description.strip() or len(long_description) > 320:
            errors.append("plugin interface longDescription must be a non-empty string of at most 320 characters")
        else:
            if ("$" + "acceptance") in long_description:
                errors.append("plugin interface longDescription contains removed public token " + "$" + "acceptance")
            for token in sorted(PUBLIC_SKILL_TOKENS):
                if not _contains_exact_skill_token(long_description, token):
                    errors.append(f"plugin interface longDescription must advertise {token}")
            for phrase in ("directly", "next lifecycle step", "implementation review", "specification gates"):
                if phrase not in long_description.lower():
                    errors.append(f"plugin interface longDescription must explain {phrase!r}")
            if PUBLIC_METADATA_JARGON.search(long_description):
                errors.append("plugin interface longDescription exposes private implementation or scaffold jargon")

        default_prompt = interface.get("defaultPrompt")
        if (
            not isinstance(default_prompt, str)
            or not default_prompt.strip()
            or len(default_prompt) > 160
            or not _contains_exact_skill_token(default_prompt, "$use-expskill")
            or "next lifecycle step" not in default_prompt.lower()
        ):
            errors.append(
                "plugin interface defaultPrompt must explicitly invoke $use-expskill for the next lifecycle step"
            )
        elif PUBLIC_METADATA_JARGON.search(default_prompt):
            errors.append("plugin interface defaultPrompt exposes private implementation or scaffold jargon")

    _validate_skills(plugin_root / "skills", errors)


def _validate_skills(skills_root: Path, errors: list[str]) -> None:
    plugin_root = skills_root.parent
    if _required_package_path(
        plugin_root,
        "skills",
        "skills path",
        "directory",
        errors,
        missing_label="skills directory",
    ) is None:
        return
    entries = {path.name: path for path in skills_root.iterdir()}
    for name in sorted(EXPECTED_SKILLS - entries.keys()):
        errors.append(f"required skill {name!r} is missing")
    for name in sorted(entries.keys() - EXPECTED_SKILLS):
        errors.append(f"unexpected skill entry {name!r}")
    names: list[str] = []
    for skill_root in sorted(entries.values(), key=lambda path: path.name):
        relative_skill = f"skills/{skill_root.name}"
        if _required_package_path(
            plugin_root,
            relative_skill,
            f"skill {skill_root.name!r}",
            "directory",
            errors,
        ) is None:
            continue
        expected_files = {"SKILL.md", "agents/openai.yaml"}
        if skill_root.name == "brainstorm":
            expected_files.add("references/brainstorm-techniques.csv")
        if skill_root.name == "design":
            expected_files.update({
                "references/rules-index.md", "references/geometry.md", "references/typography.md",
                "references/interaction.md", "references/forms.md", "references/responsive.md",
                "references/accessibility.md", "references/motion.md", "references/data-display.md",
            })
        if skill_root.name == "skill-builder":
            expected_files.update({
                *SKILL_BUILDER_REQUIRED_REFERENCES,
                "scripts/run_state.py",
            })
        if skill_root.name == "setup-ui-testing":
            expected_files.update(SETUP_UI_TESTING_REQUIRED_RESOURCES)
        if skill_root.name == "test":
            expected_files.update({
                "references/quality-rules.json",
                "references/evidence-contract.json",
                "scripts/append_ledger.py",
                "scripts/bootstrap_run.py",
                "scripts/finalize_evidence.py",
                "scripts/freeze_charter.py",
                "scripts/record_final_action.py",
            })
        expected_directories = {"agents"}
        if skill_root.name == "brainstorm":
            expected_directories.add("references")
        if skill_root.name == "design":
            expected_directories.add("references")
        if skill_root.name == "skill-builder":
            expected_directories.update({"references", "scripts"})
        if skill_root.name == "setup-ui-testing":
            expected_directories.update({"references", "scripts"})
        if skill_root.name == "test":
            expected_directories.add("references")
            expected_directories.add("scripts")
        actual_files = {
            path.relative_to(skill_root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_file()
        }
        actual_directories = {
            path.relative_to(skill_root).as_posix()
            for path in skill_root.rglob("*")
            if path.is_dir()
        }
        for relative in sorted(actual_directories - expected_directories):
            errors.append(f"skill {skill_root.name!r} contains unexpected directory {relative!r}")
        for relative in sorted(actual_files - expected_files):
            errors.append(f"skill {skill_root.name!r} contains unexpected file {relative!r}")
        for path in skill_root.rglob("*"):
            if path.is_symlink():
                errors.append(f"skill {skill_root.name!r} contains a symlink: {path}")
        if skill_root.name == "skill-builder":
            for relative in SKILL_BUILDER_REQUIRED_REFERENCES:
                _required_nonempty_package_file(
                    plugin_root,
                    f"{relative_skill}/{relative}",
                    f"skill 'skill-builder' required reference {relative!r}",
                    errors,
                )
        if skill_root.name == "setup-ui-testing":
            for relative in SETUP_UI_TESTING_REQUIRED_RESOURCES:
                _required_nonempty_package_file(
                    plugin_root,
                    f"{relative_skill}/{relative}",
                    f"skill 'setup-ui-testing' required resource {relative!r}",
                    errors,
                )
        skill_path = _required_package_path(
            plugin_root,
            f"{relative_skill}/SKILL.md",
            f"skill {skill_root.name!r} entrypoint",
            "file",
            errors,
        )
        if skill_path is None:
            continue
        try:
            contents = skill_path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"skill {skill_root.name!r} could not be read: {error}")
            continue
        if PLACEHOLDER in contents:
            errors.append(f"skill {skill_root.name!r} contains a placeholder")
        frontmatter = _parse_frontmatter(contents, skill_root.name, errors)
        if frontmatter is None:
            continue
        if set(frontmatter) not in ({"name", "description"}, {"name", "description", "metadata"}):
            errors.append(
                f"skill {skill_root.name!r} frontmatter keys must be exactly name and description"
            )
        _validate_shared_skill_metadata(skill_root, frontmatter, errors)
        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"skill {skill_root.name!r} has no frontmatter name")
        else:
            names.append(name)
            if name != skill_root.name:
                errors.append(
                    f"skill {skill_root.name!r} frontmatter name must match directory"
                )
            if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", name):
                errors.append(f"skill {skill_root.name!r} has an invalid frontmatter name")
        if not isinstance(description, str) or not description.strip():
            errors.append(f"skill {skill_root.name!r} has no frontmatter description")
        else:
            maximum_description_length = 400 if skill_root.name == "setup-ui-testing" else 300
            if not 20 <= len(description) <= maximum_description_length:
                errors.append(
                    f"skill {skill_root.name!r} description must be "
                    f"20-{maximum_description_length} characters"
                )
        if isinstance(description, str) and any(character in description for character in "<>\r\n"):
            errors.append(f"skill {skill_root.name!r} description contains forbidden characters")
        body_lines = contents.splitlines()[_frontmatter_line_count(contents):]
        if len(body_lines) >= 500:
            errors.append(f"skill {skill_root.name!r} SKILL.md body is overlong")
        normalized_contents = contents.lower()
        if any(retired in normalized_contents for retired in RETIRED_SKILLS):
            errors.append(f"skill {skill_root.name!r} references a retired skill")
        policy_contents = contents
        if skill_root.name == "setup-ui-testing":
            policy_contents = SETUP_UI_TESTING_ALLOWED_FULL_CONTEXTS.sub("", contents)
        if PUBLIC_SKILL_JARGON.search(policy_contents):
            errors.append(f"skill {skill_root.name!r} contains private policy vocabulary")
        _validate_skill_metadata(skill_root, errors)
        if skill_root.name == "brainstorm":
            _validate_brainstorm_catalog(skill_root, errors)
        if skill_root.name == "test":
            _validate_test_boundary(contents, errors)
            _validate_test_quality_catalog(skill_root, errors)
            _validate_test_evidence_contract(skill_root, errors)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        errors.append(f"skill name {name!r} is duplicated")
    _validate_skill_builder_separation(skills_root, errors)


def _validate_skill_builder_separation(skills_root: Path, errors: list[str]) -> None:
    builder_path = skills_root / "skill-builder" / "SKILL.md"
    if builder_path.is_file() and not builder_path.is_symlink():
        try:
            builder = builder_path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"skill-builder contract could not be read: {error}")
        else:
            boundary = _markdown_level_two_section(builder, "Boundary")
            if boundary != SKILL_BUILDER_BOUNDARY_SECTION:
                errors.append(
                    "skill-builder canonical boundary section must match the pinned contract"
                )
            if not _contains_exact_skill_token(builder, SKILL_BUILDER_TOKEN):
                errors.append("skill-builder contract must identify $skill-builder directly")
            if any(
                _contains_exact_skill_token(builder, token)
                for token in SKILL_BUILDER_FORBIDDEN_TOKENS
            ):
                errors.append(
                    "skill-builder contains another product skill invocation token"
                )
            if boundary is None:
                outside_boundary = builder
            else:
                boundary_start = builder.find(boundary)
                outside_boundary = (
                    builder[:boundary_start]
                    + builder[boundary_start + len(boundary) :]
                )
            if re.search(r"\blifecycle\b", outside_boundary, re.IGNORECASE):
                errors.append(
                    "skill-builder contains lifecycle wording outside the canonical boundary"
                )

    router_path = skills_root / "use-expskill" / "SKILL.md"
    if router_path.is_file() and not router_path.is_symlink():
        try:
            router = router_path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"use-expskill contract could not be read: {error}")
        else:
            if SKILL_BUILDER_NAME_PATTERN.search(router):
                errors.append("use-expskill must not name skill-builder")


def _validate_test_boundary(contents: str, errors: list[str]) -> None:
    sections = re.findall(
        r"^## Boundary[ \t]*\n(?P<body>.*?)(?=^## [^\n]+[ \t]*$|\Z)",
        contents,
        flags=re.MULTILINE | re.DOTALL,
    )
    expected = " ".join(TEST_PROTECTED_BOUNDARY.split())
    if len(sections) != 1 or " ".join(sections[0].split()) != expected:
        errors.append("skill 'test' protected Boundary contract drift")


def _validate_test_quality_catalog(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    catalog_path = _required_package_path(
        plugin_root,
        TEST_QUALITY_CATALOG_RELATIVE,
        "test quality catalog",
        "file",
        errors,
    )
    if catalog_path is None:
        return
    catalog = _load_json_object(catalog_path, "test quality catalog", errors)
    if catalog is None:
        return
    if set(catalog) != TEST_QUALITY_CATALOG_FIELDS:
        errors.append(
            "test quality catalog keys must be exactly schema_version and rules"
        )
        return
    if catalog.get("schema_version") != TEST_QUALITY_CATALOG_VERSION:
        errors.append(
            f"test quality catalog schema_version must be {TEST_QUALITY_CATALOG_VERSION!r}"
        )
    rules = catalog.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("test quality catalog rules must be a non-empty list")
        return

    identifiers: set[str] = set()
    for index, rule in enumerate(rules):
        label = f"test quality catalog rules[{index}]"
        if not isinstance(rule, dict):
            errors.append(f"{label} must be an object")
            continue
        if set(rule) != TEST_QUALITY_RULE_FIELDS:
            errors.append(
                f"{label} keys must be exactly id, level, applies_when, requirement, "
                "failure_prevented, required_evidence, and allowed_exceptions"
            )
            continue

        identifier = rule["id"]
        if (
            not isinstance(identifier, str)
            or TEST_QUALITY_RULE_ID_PATTERN.fullmatch(identifier) is None
        ):
            errors.append(f"{label}.id must be a kebab/dot identifier")
        elif identifier in identifiers:
            errors.append(f"{label}.id is a duplicate rule id: {identifier!r}")
        else:
            identifiers.add(identifier)

        if rule["level"] != "hard":
            errors.append(f"{label}.level must be 'hard'")

        applicability = rule["applies_when"]
        if not isinstance(applicability, list) or not applicability:
            errors.append(f"{label}.applies_when must be a non-empty list")
        else:
            for applicability_index, condition in enumerate(applicability):
                if (
                    not isinstance(condition, str)
                    or condition not in TEST_QUALITY_APPLICABILITY
                ):
                    errors.append(
                        f"{label}.applies_when[{applicability_index}] must be one of "
                        f"{sorted(TEST_QUALITY_APPLICABILITY)!r}"
                    )

        for field in ("requirement", "failure_prevented"):
            value = rule[field]
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}.{field} must be a non-empty string")

        _validate_non_empty_string_list(
            rule["required_evidence"], f"{label}.required_evidence", errors
        )

        exceptions = rule["allowed_exceptions"]
        if not isinstance(exceptions, list):
            errors.append(f"{label}.allowed_exceptions must be a list")
            continue
        for exception_index, exception in enumerate(exceptions):
            exception_label = f"{label}.allowed_exceptions[{exception_index}]"
            if not isinstance(exception, dict):
                errors.append(f"{exception_label} must be an object")
                continue
            if set(exception) != TEST_QUALITY_EXCEPTION_FIELDS:
                errors.append(
                    f"{exception_label} keys must be exactly predicate and required_evidence"
                )
                continue
            predicate = exception["predicate"]
            if not isinstance(predicate, str) or not predicate.strip():
                errors.append(f"{exception_label}.predicate must be a non-empty string")
            _validate_non_empty_string_list(
                exception["required_evidence"],
                f"{exception_label}.required_evidence",
                errors,
            )


def _validate_test_evidence_contract(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    contract_path = _required_package_path(
        plugin_root,
        TEST_EVIDENCE_CONTRACT_RELATIVE,
        "test evidence contract",
        "file",
        errors,
    )
    if contract_path is None:
        return
    contract = _load_json_object(contract_path, "test evidence contract", errors)
    if contract is None:
        return
    if set(contract) != TEST_EVIDENCE_CONTRACT_FIELDS:
        errors.append(
            "test evidence contract keys must be exactly schema_version, "
            "terminal_states, finding_kinds, bundle, receipt, and finding"
        )
        return
    if contract.get("schema_version") != TEST_EVIDENCE_CONTRACT_VERSION:
        errors.append(
            "test evidence contract schema_version must be "
            f"{TEST_EVIDENCE_CONTRACT_VERSION!r}"
        )
    if contract.get("terminal_states") != TEST_EVIDENCE_TERMINAL_STATES:
        errors.append(
            "test evidence contract terminal_states must be exactly "
            "PASS, FAIL, BLOCKED, and EXEMPT"
        )
    if contract.get("finding_kinds") != TEST_EVIDENCE_FINDING_KINDS:
        errors.append(
            "test evidence contract finding_kinds must be exactly product-defect, "
            "test-system-defect, environment-blocker, and unresolved-cause"
        )

    for name, expected_schema in TEST_EVIDENCE_EXPECTED_SCHEMAS.items():
        _validate_test_evidence_schema(
            contract.get(name),
            expected_schema,
            f"test evidence contract {name}",
            errors,
        )
    _validate_test_evidence_bindings(contract, errors)


def _validate_test_evidence_schema(
    schema: object,
    expected: dict[str, object],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(schema, dict):
        errors.append(f"{label} must be an object schema")
        return

    expected_type = expected["type"]
    if schema.get("type") != expected_type:
        errors.append(f"{label} must match the required schema")
        return

    if expected_type == "object":
        if schema.get("additional_properties") is not False:
            errors.append(f"{label}.additional_properties must be false")
        if set(schema) != set(expected):
            errors.append(f"{label} must match the required schema")

        expected_required = expected["required"]
        required = schema.get("required")
        if (
            not isinstance(required, list)
            or len(required) != len(expected_required)
            or any(not isinstance(field, str) for field in required)
            or set(required) != set(expected_required)
        ):
            errors.append(
                f"{label}.required must contain each required field exactly once"
            )

        expected_properties = expected["properties"]
        properties = schema.get("properties")
        if not isinstance(properties, dict) or set(properties) != set(expected_properties):
            errors.append(
                f"{label}.properties keys must be exactly "
                f"{', '.join(expected_properties)}"
            )
            return
        for field, expected_child in expected_properties.items():
            _validate_test_evidence_schema(
                properties[field],
                expected_child,
                f"{label}.properties.{field}",
                errors,
            )
        return

    if expected_type == "array":
        if set(schema) != set(expected):
            errors.append(f"{label} must match the required schema")
            return
        for field in ("min_items", "unique_items"):
            if schema.get(field) != expected[field]:
                errors.append(f"{label} must match the required schema")
        _validate_test_evidence_schema(
            schema.get("items"),
            expected["items"],
            f"{label}.items",
            errors,
        )
        return

    if schema != expected:
        errors.append(f"{label} must match the required schema")


def _test_evidence_property_schema(
    contract: dict[str, Any], definition: str, field: str
) -> object:
    schema = contract.get(definition)
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    return properties.get(field)


def _validate_test_evidence_bindings(
    contract: dict[str, Any], errors: list[str]
) -> None:
    for definition in ("bundle", "receipt"):
        if (
            _test_evidence_property_schema(contract, definition, "workflow_id")
            != _test_evidence_workflow_schema()
        ):
            errors.append(
                "test evidence contract workflow_id must be nullable only for "
                "direct invocation"
            )

    for definition in ("bundle", "receipt", "finding"):
        head = _test_evidence_property_schema(contract, definition, "head")
        if not isinstance(head, dict) or head.get("pattern") != TEST_EVIDENCE_HEAD_PATTERN:
            errors.append(
                f"test evidence contract {definition}.head must bind exact head"
            )

        environment = _test_evidence_property_schema(
            contract, definition, "environment_digest"
        )
        if (
            not isinstance(environment, dict)
            or environment.get("pattern") != TEST_EVIDENCE_SHA256_PATTERN
            or environment.get("semantics")
            != TEST_EVIDENCE_ENVIRONMENT_DIGEST_SEMANTICS
        ):
            errors.append(
                f"test evidence contract {definition}.environment_digest must bind "
                "exact environment"
            )

    bundle_digest = _test_evidence_property_schema(
        contract, "bundle", "bundle_digest"
    )
    if (
        not isinstance(bundle_digest, dict)
        or bundle_digest.get("pattern") != TEST_EVIDENCE_SHA256_PATTERN
        or bundle_digest.get("semantics")
        != TEST_EVIDENCE_BUNDLE_DIGEST_SEMANTICS
    ):
        errors.append(
            "test evidence contract bundle.bundle_digest must omit bundle_digest "
            "from RFC 8785 canonical JSON before SHA-256"
        )

    receipt_bundle_digest = _test_evidence_property_schema(
        contract, "receipt", "bundle_digest"
    )
    if (
        not isinstance(receipt_bundle_digest, dict)
        or receipt_bundle_digest.get("pattern") != TEST_EVIDENCE_SHA256_PATTERN
        or receipt_bundle_digest.get("semantics")
        != "Exact bundle_digest from the retained evidence bundle"
    ):
        errors.append(
            "test evidence contract receipt.bundle_digest must bind retained bundle"
        )

    selected_scope_digest = _test_evidence_property_schema(
        contract, "receipt", "selected_scope_digest"
    )
    if (
        not isinstance(selected_scope_digest, dict)
        or selected_scope_digest.get("pattern") != TEST_EVIDENCE_SHA256_PATTERN
        or selected_scope_digest.get("semantics")
        != TEST_EVIDENCE_SCOPE_DIGEST_SEMANTICS
    ):
        errors.append(
            "test evidence contract receipt.selected_scope_digest must bind selected scope"
        )

    for definition in ("bundle", "receipt"):
        result = _test_evidence_property_schema(contract, definition, "result")
        if not isinstance(result, dict) or result.get("enum") != contract.get(
            "terminal_states"
        ):
            errors.append(
                f"test evidence contract {definition} result enum must match terminal_states"
            )
    finding_kind = _test_evidence_property_schema(contract, "finding", "kind")
    if not isinstance(finding_kind, dict) or finding_kind.get("enum") != contract.get(
        "finding_kinds"
    ):
        errors.append(
            "test evidence contract finding kind enum must match finding_kinds"
        )


def _validate_non_empty_string_list(
    value: object, label: str, errors: list[str]
) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{label}[{index}] must be a non-empty string")


def _validate_brainstorm_catalog(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    catalog_path = _required_package_path(
        plugin_root,
        BRAINSTORM_CATALOG_RELATIVE,
        "brainstorm catalog",
        "file",
        errors,
    )
    if catalog_path is None:
        return
    try:
        contents = catalog_path.read_bytes()
    except OSError as error:
        errors.append(f"brainstorm catalog could not be read: {error}")
        return
    preamble = BRAINSTORM_CATALOG_PREAMBLE.encode("utf-8")
    if not contents.startswith(preamble):
        errors.append("brainstorm catalog has an unexpected attribution preamble")
        return
    payload = contents[len(preamble) :]
    if hashlib.sha256(payload).hexdigest() != BRAINSTORM_CATALOG_SHA256:
        errors.append("brainstorm catalog payload does not match the pinned upstream digest")


def _validate_unslop_hook(plugin_root: Path, errors: list[str]) -> None:
    hooks_root = _required_package_path(
        plugin_root,
        "hooks",
        "Unslop hook directory",
        "directory",
        errors,
    )
    if hooks_root is None:
        return

    expected_files = {"hooks.json", "inject_unslop.py"}
    actual_files = {
        path.relative_to(hooks_root).as_posix()
        for path in hooks_root.rglob("*")
        if path.is_file() or path.is_symlink()
        if "__pycache__" not in path.relative_to(hooks_root).parts
    }
    if actual_files != expected_files:
        errors.append(
            "Unslop hook files must be exactly hooks.json and inject_unslop.py"
        )

    config_path = _required_package_path(
        plugin_root,
        UNSLOP_HOOK_CONFIG_PATH,
        "Unslop hook configuration",
        "file",
        errors,
    )
    script_path = _required_package_path(
        plugin_root,
        UNSLOP_HOOK_SCRIPT_PATH,
        "Unslop hook script",
        "file",
        errors,
    )
    if config_path is not None:
        config = _load_json_object(config_path, "Unslop hook configuration", errors)
        if config is not None and config != EXPECTED_UNSLOP_HOOKS:
            errors.append("Unslop hook configuration does not match the root SessionStart contract")
    if script_path is None:
        return
    try:
        script_bytes = script_path.read_bytes()
        script = script_bytes.decode("utf-8")
    except OSError as error:
        errors.append(f"Unslop hook script could not be read: {error}")
        return
    except UnicodeDecodeError:
        errors.append("Unslop hook script must be UTF-8 text")
        return
    if not script.strip():
        errors.append("Unslop hook script must be non-empty")
        return
    if hashlib.sha256(script_bytes).hexdigest() != UNSLOP_HOOK_SCRIPT_SHA256:
        errors.append("Unslop hook script digest does not match the reviewed implementation")
    try:
        ast.parse(script, filename=str(script_path))
    except SyntaxError as error:
        errors.append(f"Unslop hook script is not valid Python: {error.msg}")
    normalized_script = script.lower()
    for marker in (
        "sessionstart",
        '"unslop"',
        "additionalcontext",
        "user-facing prose",
        "machine-readable data",
        "higher-priority instructions",
    ):
        if marker not in normalized_script:
            errors.append(f"Unslop hook script is missing required marker {marker!r}")


def _validate_third_party_sources(plugin_root: Path, errors: list[str]) -> None:
    third_party_root = _required_package_path(
        plugin_root,
        "third-party",
        "third-party source directory",
        "directory",
        errors,
    )
    if third_party_root is None:
        return
    lock_path = _required_package_path(
        plugin_root,
        THIRD_PARTY_LOCK_PATH,
        "third-party upstream lock",
        "file",
        errors,
    )
    if lock_path is None:
        return
    lock = _load_json_object(lock_path, "third-party upstream lock", errors)
    if lock is None:
        return
    expected_lock = {
        "schema_version": "third-party-sources.v1",
        "sources": EXPECTED_THIRD_PARTY_SOURCES,
    }
    if lock != expected_lock:
        errors.append("third-party upstream lock does not match the pinned source contract")

    expected_files = {"upstream-lock.json"}
    for source in EXPECTED_THIRD_PARTY_SOURCES.values():
        expected_files.add(source["vendored_path"])
        expected_files.add(source["license_path"])
    actual_files = {
        path.relative_to(third_party_root).as_posix()
        for path in third_party_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_files != expected_files:
        errors.append("third-party source package does not contain the exact pinned file set")

    for name, source in EXPECTED_THIRD_PARTY_SOURCES.items():
        for path_field, digest_field, label in (
            ("vendored_path", "sha256", "upstream source"),
            ("license_path", "license_sha256", "upstream license"),
        ):
            relative = source[path_field]
            path = _required_package_path(
                plugin_root,
                f"third-party/{relative}",
                f"{name} {label}",
                "file",
                errors,
            )
            if path is None:
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                errors.append(f"{name} {label} could not be read: {error}")
                continue
            if digest != source[digest_field]:
                errors.append(f"{name} {label} digest does not match the pinned upstream digest")

    _validate_public_third_party_derivations(plugin_root, third_party_root, errors)


def _validate_public_third_party_derivations(
    plugin_root: Path,
    third_party_root: Path,
    errors: list[str],
) -> None:
    try:
        unslop_source = (
            third_party_root / "sources" / "pstack" / "unslop" / "SKILL.md"
        ).read_bytes()
        public_unslop = (plugin_root / "skills" / "unslop" / "SKILL.md").read_bytes()
    except OSError as error:
        errors.append(f"public Unslop derived-copy validation failed: {error}")
    else:
        expected_unslop = unslop_source.replace(
            b"disable-model-invocation: true\n",
            b"",
            1,
        )
        if _without_shared_metadata_block(public_unslop) != expected_unslop:
            errors.append("public skill 'unslop' does not match its declared derived upstream copy")

    try:
        wrapper = (
            third_party_root / "sources" / "mattpocock" / "grill-me" / "SKILL.md"
        ).read_bytes()
        engine = (
            third_party_root / "sources" / "mattpocock" / "grilling" / "SKILL.md"
        ).read_bytes()
        public_grill = (plugin_root / "skills" / "grill-me" / "SKILL.md").read_bytes()
        wrapper_end = wrapper.index(b"\n---\n", 4) + len(b"\n---\n")
        engine_end = engine.index(b"\n---\n", 4) + len(b"\n---\n")
    except (OSError, ValueError) as error:
        errors.append(f"public Grill Me derived-copy validation failed: {error}")
    else:
        frontmatter = wrapper[:wrapper_end].replace(
            b"disable-model-invocation: true\n",
            b"",
            1,
        )
        body = engine[engine_end:].lstrip(b"\n")
        body = body.replace(b"it; don't", b"it. Don't").replace(
            b"report; ask",
            b"report. Ask",
        )
        expected_grill = frontmatter + b"\n" + body
        if _without_shared_metadata_block(public_grill) != expected_grill:
            errors.append("public skill 'grill-me' does not match its declared derived upstream copy")


def _validate_public_readme(repository_root: Path, errors: list[str]) -> None:
    readme_path = _required_nonempty_package_file(
        repository_root,
        "README.md",
        "README",
        errors,
    )
    if readme_path is None:
        return
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"README could not be read: {error}")
        return
    normalized = " ".join(readme.lower().split())
    if PUBLIC_SKILL_COUNT_TEXT not in normalized:
        errors.append(
            "README must describe nine independent skills and one optional lifecycle router"
        )
    if re.search(r"\b(?:seven|eight) independent skills\b", normalized):
        errors.append("README contains stale public-skill count wording")
    if not _contains_exact_skill_token(readme, SKILL_BUILDER_TOKEN):
        errors.append("README must advertise $skill-builder")
    readme_lines = readme.splitlines()
    builder_mentions = tuple(
        line for line in readme_lines if SKILL_BUILDER_NAME_PATTERN.search(line)
    )
    if builder_mentions != SKILL_BUILDER_README_LINES:
        errors.append(
            "README Skill Builder mentions must be exactly the public-list and "
            "direct-invocation lines"
        )
    if SKILL_BUILDER_README_LINES[0] not in readme_lines:
        errors.append(
            "README must describe $skill-builder as the evidence-gated creator or improver "
            "of one exact agent skill"
        )
    if SKILL_BUILDER_README_LINES[1] not in readme_lines:
        errors.append("README must include a direct $skill-builder invocation example")


def _validate_removed_repository_local_skill(
    repository_root: Path, errors: list[str]
) -> None:
    duplicate = repository_root / ".agents" / "skills" / "improve-skill"
    if _lstat(duplicate) is not None:
        errors.append("repository-local skill '.agents/skills/improve-skill' must be absent")


def _validate_skill_punctuation(repository_root: Path, errors: list[str]) -> None:
    roots = (
        repository_root / "packages" / "expskill" / "skills",
        repository_root / ".agents" / "skills",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                contents = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(repository_root)
            if "\N{EM DASH}" in contents:
                errors.append(f"skill text {relative} contains an em dash")
            if ";" in contents:
                errors.append(f"skill text {relative} contains a semicolon")
    opencode_root = repository_root / "packages" / "expskill" / "opencode"
    if opencode_root.is_dir():
        for path in sorted(opencode_root.rglob("*")):
            if "skills" in path.relative_to(opencode_root).parts:
                continue
            if path.suffix.lower() != ".md" or not path.is_file() or path.is_symlink():
                continue
            try:
                contents = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(repository_root)
            if "\N{EM DASH}" in contents:
                errors.append(f"skill text {relative} contains an em dash")
            if ";" in contents:
                errors.append(f"skill text {relative} contains a semicolon")


def _validate_no_legacy_project_identity(
    repository_root: Path, errors: list[str]
) -> None:
    candidates: list[Path] = [repository_root / "README.md"]
    for relative_root in (".agents", "docs", "packages", "scripts", "tests"):
        root = repository_root / relative_root
        if root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.is_file())

    for path in sorted(set(candidates)):
        try:
            relative = path.relative_to(repository_root)
        except ValueError:
            continue
        if path.is_symlink() or path.suffix.lower() not in PROJECT_IDENTITY_TEXT_SUFFIXES:
            continue
        if "third-party" in relative.parts or "__pycache__" in relative.parts:
            continue
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        searchable = f"{relative.as_posix()}\n{contents}".casefold()
        for marker in LEGACY_PROJECT_IDENTITIES:
            if marker.casefold() in searchable:
                errors.append(
                    f"legacy project identity {marker!r} remains in {relative.as_posix()}"
                )


def _parse_frontmatter(
    contents: str, skill_directory: str, errors: list[str]
) -> dict[str, object] | None:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"skill {skill_directory!r} must start with frontmatter")
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"skill {skill_directory!r} frontmatter is not closed")
        return None
    values: dict[str, object] = {}
    nested_parent: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            nested_parent = None
            continue
        indentation = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indentation]:
            errors.append(f"skill {skill_directory!r} frontmatter contains a tab")
            continue
        key, separator, raw_value = line.strip().partition(":")
        if not separator:
            if line.strip():
                errors.append(f"skill {skill_directory!r} frontmatter contains an invalid line")
            nested_parent = None
            continue
        key = key.strip()
        value = raw_value.strip()
        if not key:
            errors.append(f"skill {skill_directory!r} frontmatter contains an empty key")
            nested_parent = None
            continue
        if indentation == 0:
            nested_parent = None
            if key in values:
                errors.append(f"skill {skill_directory!r} frontmatter key {key!r} is duplicated")
                continue
            if not value:
                if key != "metadata":
                    errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a scalar")
                    continue
                values[key] = {}
                nested_parent = key
                continue
            if value.startswith(("[", "{")):
                errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a scalar")
                continue
            values[key] = _parse_frontmatter_scalar(value, skill_directory, key, errors)
            continue
        if nested_parent != "metadata" or indentation != 2:
            errors.append(f"skill {skill_directory!r} frontmatter contains an invalid line")
            continue
        nested = values["metadata"]
        assert isinstance(nested, dict)
        if key in nested:
            errors.append(f"skill {skill_directory!r} frontmatter key {key!r} is duplicated")
            continue
        if not value:
            errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a scalar")
            continue
        parsed = _parse_frontmatter_scalar(value, skill_directory, key, errors)
        if parsed is None:
            continue
        nested[key] = parsed
    return values


def _parse_frontmatter_scalar(
    value: str, skill_directory: str, key: str, errors: list[str]
) -> str | None:
    if value.startswith(("[", "{")):
        errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a scalar")
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is invalid")
            return None
        if not isinstance(parsed, str):
            errors.append(f"skill {skill_directory!r} frontmatter value {key!r} is not a string")
            return None
        return parsed
    return value


SHARED_SKILL_METADATA_KEYS = ("opencode/slash", "opencode/autoinvoke")
SHARED_METADATA_BLOCK_EXPLICIT_ONLY = (
    'metadata:\n  opencode/slash: "true"\n  opencode/autoinvoke: "false"\n'
)
SHARED_METADATA_BLOCK_ROUTER = (
    'metadata:\n  opencode/slash: "true"\n  opencode/autoinvoke: "true"\n'
)


def _frontmatter_line_count(contents: str) -> int:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        return 0
    try:
        return lines.index("---", 1) + 1
    except ValueError:
        return 0


def _without_shared_metadata_block(contents: bytes) -> bytes:
    for block in (SHARED_METADATA_BLOCK_EXPLICIT_ONLY, SHARED_METADATA_BLOCK_ROUTER):
        marker = block.encode("utf-8")
        if marker in contents:
            return contents.replace(marker, b"", 1)
    return contents


def _validate_shared_skill_metadata(
    skill_root: Path, frontmatter: dict[str, object], errors: list[str]
) -> None:
    metadata = frontmatter.get("metadata")
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        errors.append(f"skill {skill_root.name!r} frontmatter metadata must be a mapping")
        return
    for key in sorted(str(item) for item in metadata):
        if key not in SHARED_SKILL_METADATA_KEYS:
            errors.append(f"skill {skill_root.name!r} frontmatter metadata key {key!r} is unexpected")
    for key in SHARED_SKILL_METADATA_KEYS:
        if key not in metadata:
            continue
        value = metadata[key]
        if value not in ("true", "false"):
            errors.append(
                f"skill {skill_root.name!r} frontmatter metadata {key!r} must be 'true' or 'false'"
            )


def _validate_skill_metadata(skill_root: Path, errors: list[str]) -> None:
    plugin_root = skill_root.parent.parent
    agents_path = _required_package_path(
        plugin_root,
        f"skills/{skill_root.name}/agents",
        f"skill {skill_root.name!r} agents directory",
        "directory",
        errors,
    )
    if agents_path is None:
        return
    metadata_path = _required_package_path(
        plugin_root,
        f"skills/{skill_root.name}/agents/openai.yaml",
        f"skill {skill_root.name!r} metadata",
        "file",
        errors,
    )
    if metadata_path is None:
        return
    try:
        contents = metadata_path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"skill {skill_root.name!r} metadata could not be read: {error}")
        return
    metadata = _parse_skill_metadata(contents, skill_root.name, errors)
    if metadata is None:
        return
    if set(metadata) != {"interface", "policy"}:
        errors.append(f"skill {skill_root.name!r} metadata keys must be exactly interface and policy")
        return
    interface = metadata.get("interface")
    policy = metadata.get("policy")
    if not isinstance(interface, dict):
        errors.append(f"skill {skill_root.name!r} metadata interface must be a mapping")
        return
    if not isinstance(policy, dict):
        errors.append(f"skill {skill_root.name!r} metadata policy must be a mapping")
        return
    if set(interface) != {"display_name", "short_description", "default_prompt"}:
        errors.append(f"skill {skill_root.name!r} interface keys are not exact")
    if set(policy) != {"allow_implicit_invocation"}:
        errors.append(f"skill {skill_root.name!r} policy keys are not exact")
    for field in ("display_name", "short_description", "default_prompt"):
        value = interface.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"skill {skill_root.name!r} interface.{field} must be a non-empty string")
            continue
        if any(character in value for character in "<>\r\n"):
            errors.append(f"skill {skill_root.name!r} interface.{field} contains forbidden characters")
        if PUBLIC_SKILL_JARGON.search(value):
            errors.append(f"skill {skill_root.name!r} interface.{field} contains private policy vocabulary")
    short_description = interface.get("short_description")
    if isinstance(short_description, str) and not 25 <= len(short_description) <= 64:
        errors.append(f"skill {skill_root.name!r} short_description must be 25-64 characters")
    default_prompt = interface.get("default_prompt")
    if isinstance(default_prompt, str) and not _contains_exact_skill_token(
        default_prompt, f"${skill_root.name}"
    ):
        errors.append(f"skill {skill_root.name!r} default_prompt must invoke the matching skill")
    implicit = policy.get("allow_implicit_invocation")
    if not isinstance(implicit, bool):
        errors.append(f"skill {skill_root.name!r} allow_implicit_invocation must be a boolean")
    elif implicit is not (skill_root.name == "use-expskill"):
        errors.append(f"skill {skill_root.name!r} implicit invocation policy drift")


def _parse_skill_metadata(
    contents: str, skill_name: str, errors: list[str]
) -> dict[str, dict[str, object]] | None:
    values: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(contents.splitlines(), start=1):
        if not line.strip():
            continue
        if "\t" in line:
            errors.append(f"skill {skill_name!r} metadata line {line_number} contains a tab")
            continue
        indentation = len(line) - len(line.lstrip(" "))
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            errors.append(f"skill {skill_name!r} metadata line {line_number} is invalid")
            continue
        raw_value = raw_value.strip()
        if indentation == 0:
            if raw_value:
                errors.append(f"skill {skill_name!r} metadata root {key!r} must be a mapping")
                continue
            if key in values:
                errors.append(f"skill {skill_name!r} metadata key {key!r} is duplicated")
                continue
            values[key] = {}
            continue
        if indentation != 2:
            errors.append(f"skill {skill_name!r} metadata line {line_number} has invalid indentation")
            continue
        if not values:
            errors.append(f"skill {skill_name!r} metadata child appears before a root mapping")
            continue
        parent = next(reversed(values))
        mapping = values[parent]
        if key in mapping:
            errors.append(f"skill {skill_name!r} metadata key {parent}.{key!s} is duplicated")
            continue
        if parent == "interface":
            if len(raw_value) < 2 or raw_value[0] != raw_value[-1] or raw_value[0] not in {'"', "'"}:
                errors.append(f"skill {skill_name!r} interface.{key} must be a quoted string")
                continue
            try:
                value = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                errors.append(f"skill {skill_name!r} interface.{key} is invalid")
                continue
            if not isinstance(value, str):
                errors.append(f"skill {skill_name!r} interface.{key} must be a string")
                continue
            mapping[key] = value
        elif parent == "policy":
            if raw_value not in {"true", "false"}:
                errors.append(f"skill {skill_name!r} policy.{key} must be a YAML boolean literal")
                continue
            mapping[key] = raw_value == "true"
        else:
            mapping[key] = raw_value
    return values


def _validate_policy(plugin_root: Path, errors: list[str]) -> None:
    if _required_package_path(plugin_root, "assets", "asset directory", "directory", errors) is None:
        return
    policy_entries = [
        path.relative_to(plugin_root).as_posix()
        for path, _ in _lexical_package_entries(plugin_root)
        if path.name == "execution-policy.json"
    ]
    if policy_entries != [POLICY_PATH]:
        observed = ", ".join(sorted(policy_entries)) or "none"
        errors.append(
            f"execution policy artifacts must contain exactly {POLICY_PATH}; found {observed}"
        )
    policy_path = _required_package_path(
        plugin_root,
        POLICY_PATH,
        "execution policy",
        "file",
        errors,
    )
    if policy_path is None or policy_entries != [POLICY_PATH]:
        return
    policy = _load_json_object(policy_path, "execution policy", errors)
    if policy is None:
        return
    expected = {
        "policy_version": "execution-budget-policy.v1",
        "profiles": EXPECTED_POLICY_PROFILES,
        "routes": EXPECTED_POLICY_ROUTES,
    }
    if set(policy) != set(expected):
        errors.append("execution policy keys must be exactly policy_version, profiles, and routes")
        return
    if policy.get("policy_version") != expected["policy_version"]:
        errors.append("execution policy policy_version is not execution-budget-policy.v1")
    if policy.get("profiles") != EXPECTED_POLICY_PROFILES:
        errors.append("execution policy profiles do not match the exact validated roster")
    if policy.get("routes") != EXPECTED_POLICY_ROUTES:
        errors.append("execution policy routes do not match the exact validated plans")


def _validate_helper_and_package_layout(plugin_root: Path, errors: list[str]) -> None:
    if _required_package_path(plugin_root, "scripts", "plugin scripts directory", "directory", errors) is None:
        return
    helper = _required_package_path(
        plugin_root,
        HELPER_PATH,
        "worktree helper",
        "file",
        errors,
    )
    helpers = sorted(
        (
            path.relative_to(plugin_root).as_posix(),
            path,
        )
        for path in plugin_root.rglob("worktrees.py")
        if path.is_file() or path.is_symlink()
    )
    if [relative for relative, _ in helpers] != [HELPER_PATH]:
        observed = ", ".join(relative for relative, _ in helpers) or "none"
        errors.append(f"worktree helper must exist only at {HELPER_PATH}; found {observed}")
    plan_helper = _required_package_path(
        plugin_root,
        PLAN_GRAPH_HELPER_PATH,
        "plan graph helper",
        "file",
        errors,
    )
    plan_helpers = sorted(
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("plan_graph.py")
        if path.is_file() or path.is_symlink()
    )
    if plan_helpers != [PLAN_GRAPH_HELPER_PATH]:
        observed = ", ".join(plan_helpers) or "none"
        errors.append(
            f"plan graph helper must exist only at {PLAN_GRAPH_HELPER_PATH}; found {observed}"
        )
    for label, path in (("worktree", helper), ("plan graph", plan_helper)):
        if path is not None and (path.is_symlink() or not path.is_file() or path.stat().st_size == 0):
            errors.append(f"{label} helper must be a non-empty regular file")
    design_helper_path = "scripts/design_state.py"
    design_matches = sorted(
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("design_state.py")
        if path.is_file() or path.is_symlink()
    )
    if design_matches != [design_helper_path]:
        errors.append(f"design state helper must exist only at {design_helper_path}; found {', '.join(design_matches) or 'none'}")
    design_helper = plugin_root / design_helper_path
    if design_helper.is_symlink() or not design_helper.is_file() or design_helper.stat().st_size == 0:
        errors.append("design state helper must be a non-empty regular file")


def _validate_agents(plugin_root: Path, errors: list[str]) -> None:
    agents_root = plugin_root / AGENTS_PATH
    if _required_package_path(plugin_root, AGENTS_PATH, "agent directory", "directory", errors) is None:
        return

    expected_filenames = {f"{name}.toml" for name in EXPECTED_AGENTS}
    for path in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            errors.append(f"agent profile {path.name!r} must not be a symlink")
            continue
        if not path.is_file():
            if path.name.startswith("expskill-"):
                errors.append(f"unexpected agent profile {path.name!r}")
            continue
        if path.suffix == ".toml" and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.stem!r}")
        elif path.name.startswith("expskill-") and path.name not in expected_filenames:
            errors.append(f"unexpected agent profile {path.name!r}")
    for expected_name in EXPECTED_AGENTS:
        path = _required_package_path(
            plugin_root,
            f"{AGENTS_PATH}/{expected_name}.toml",
            f"agent profile {expected_name!r}",
            "file",
            errors,
        )
        if path is None:
            continue
        _validate_agent_profile(path, expected_name, errors)


def _validate_agent_profile(path: Path, expected_name: str, errors: list[str]) -> None:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as error:
        errors.append(f"agent profile {expected_name!r} could not be read: {error}")
        return
    if PLACEHOLDER in contents:
        errors.append(f"agent profile {expected_name!r} contains a placeholder")
    try:
        profile = tomllib.loads(contents)
    except tomllib.TOMLDecodeError as error:
        errors.append(f"agent profile {expected_name!r} is invalid TOML: {error}")
        return
    if not isinstance(profile, dict):
        errors.append(f"agent profile {expected_name!r} must be a TOML table")
        return

    for field in REQUIRED_AGENT_FIELDS:
        value = profile.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"agent profile {expected_name!r} field {field!r} must be non-empty")
    actual_name = profile.get("name")
    if actual_name != expected_name:
        errors.append(
            f"agent profile {expected_name!r} name must be {expected_name!r}, got {actual_name!r}"
        )

    expected_model, expected_effort, expected_sandbox = EXPECTED_AGENTS[expected_name]
    for field, expected_value in (
        ("model", expected_model),
        ("model_reasoning_effort", expected_effort),
        ("sandbox_mode", expected_sandbox),
    ):
        actual_value = profile.get(field)
        if actual_value != expected_value:
            errors.append(
                f"agent profile {expected_name!r} {field} must be {expected_value!r}, "
                f"got {actual_value!r}"
            )

    instructions = profile.get("developer_instructions")
    if isinstance(instructions, str):
        normalized = " ".join(instructions.lower().split())
        for phrase in AGENT_BOUNDARIES[expected_name]:
            if phrase not in normalized:
                errors.append(
                    f"agent profile {expected_name!r} instructions must include {phrase!r}"
                )
        expected_digest = REVIEW_AGENT_INSTRUCTIONS_CANONICAL_SHA256.get(expected_name)
        if expected_digest is not None:
            canonical = _canonical_review_agent_instructions(instructions)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if digest != expected_digest:
                errors.append(
                    f"agent profile {expected_name!r} instructions differ from their "
                    "validated normalized content"
                )


OPENCODE_PACKAGE_NAME = "opencode-expskill"
OPENCODE_PLATFORM_FILES = ("agents.json", "package.json", "README.md", "LICENSE", "index.js")
OPENCODE_AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
# These four profiles are intentionally stricter than the planner: their
# Bash permission is an allow-list of bounded Git inspection forms.  The
# revision/path tails are safe only after Git's option terminators; arbitrary
# subcommands and arbitrary option-bearing Git invocations remain denied.
OPENCODE_READ_ONLY_GIT_AGENTS = (
    "expskill-explorer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
OPENCODE_READ_ONLY_GIT_RULE_ORDER = (
    ("*", "deny"),
    ("git status", "allow"),
    ("git status --short", "allow"),
    ("git status --short --branch", "allow"),
    ("git status --porcelain", "allow"),
    ("git status --porcelain=v1", "allow"),
    ("git branch", "allow"),
    ("git branch --show-current", "allow"),
    ("git branch --list", "allow"),
    ("git branch --list -- *", "allow"),
    ("git --no-pager diff --no-ext-diff --no-textconv --no-renames", "allow"),
    (
        "git --no-pager diff --no-ext-diff --no-textconv --no-renames --end-of-options *",
        "allow",
    ),
    ("git --no-pager diff --no-ext-diff --no-textconv --no-renames -- *", "allow"),
    ("git --no-pager log --no-ext-diff --no-textconv --no-renames", "allow"),
    (
        "git --no-pager log --no-ext-diff --no-textconv --no-renames --end-of-options *",
        "allow",
    ),
    ("git --no-pager show --no-ext-diff --no-textconv --no-renames", "allow"),
    (
        "git --no-pager show --no-ext-diff --no-textconv --no-renames --end-of-options *",
        "allow",
    ),
    ("git * --output*", "deny"),
    ("git * -o*", "deny"),
    ("git * --ext-diff*", "deny"),
    ("git * --textconv*", "deny"),
    ("git *>*", "deny"),
    ("git *<*", "deny"),
)
OPENCODE_READ_ONLY_GIT_RULES = dict(OPENCODE_READ_ONLY_GIT_RULE_ORDER)
OPENCODE_PLUGINS = ("unslop.js", "execution-policy.js")
OPENCODE_PACKAGE_EXPORTS = {".": "./index.js"}
OPENCODE_PACKAGE_FILES = (
    "LICENSE",
    "README.md",
    "index.js",
    "agents.json",
    "catalog.json",
    "provenance.json",
    "agents/",
    "assets/",
    "commands/",
    "plugins/",
    "scripts/",
    "skills/",
    "third-party/licenses/",
    "!**/__pycache__/**",
    "!**/*.pyc",
    "!**/*.pyo",
)
OPENCODE_AGENT_ALLOWED_FRONTMATTER = {
    "description",
    "mode",
    "model",
    "reasoningEffort",
    "temperature",
    "permission",
}
OPENCODE_AGENT_BOUNDARIES = {
    "expskill-explorer": ("read-only mode", "no delegation", "no scope expansion"),
    "expskill-planner": (
        "private plan graph",
        "never edit",
        "only plan graph writer",
        "do not delegate",
    ),
    "expskill-designer": (
        "isolated helper-owned worktree",
        "one coherent local candidate commit",
        "do not write the plan graph",
        "do not delegate",
    ),
    "expskill-test-engineer": ("no product implementation", "no delegation"),
    "expskill-implementer": (
        "red-green-refactor",
        "no delegation",
        "no scope expansion",
        "never push",
    ),
    "expskill-review": (
        "read-only mode",
        "invalid handoff",
        "do not treat another agent's conclusion as evidence",
    ),
    "expskill-spec": (
        "no tracked-source edits",
        "invalid handoff",
        "pass or fail",
    ),
}
OPENCODE_READ_ONLY_AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
OPENCODE_WORKSPACE_AGENTS = (
    "expskill-designer",
    "expskill-implementer",
)


def _validate_opencode_root(package_root: Path, errors: list[str]) -> bool:
    if package_root.is_symlink():
        errors.append(f"opencode package root must not be a symlink: {package_root}")
        return False
    metadata = _lstat(package_root)
    if metadata is None:
        errors.append(f"opencode package directory is missing: {package_root}")
        return False
    if not stat.S_ISDIR(metadata.st_mode):
        errors.append(f"opencode package root must be a directory: {package_root}")
        return False
    try:
        resolved = package_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        errors.append(f"opencode package root cannot be resolved: {package_root}: {error}")
        return False
    if resolved != package_root:
        errors.append(f"opencode package root resolves outside its lexical path: {package_root}")
        return False
    return True


def _validate_opencode_platform_source(package_root: Path, errors: list[str]) -> None:
    """Reject any checked-in platform entry outside the exact source roster."""

    expected = set(OPENCODE_PLATFORM_FILES) | {"plugins"}
    try:
        entries = {path.name: path for path in package_root.iterdir()}
    except OSError as error:
        errors.append(f"opencode platform source could not be listed: {error}")
        return
    unexpected = sorted(set(entries) - expected)
    missing = sorted(expected - set(entries))
    if unexpected:
        errors.append(f"opencode platform source has unexpected entries: {unexpected!r}")
    if missing:
        errors.append(f"opencode platform source is missing entries: {missing!r}")
    for name in OPENCODE_PLATFORM_FILES:
        path = package_root / name
        metadata = _lstat(path)
        if metadata is None or not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            errors.append(f"opencode platform source file is not regular: {path}")
    plugins = package_root / "plugins"
    metadata = _lstat(plugins)
    if metadata is None or not stat.S_ISDIR(metadata.st_mode) or plugins.is_symlink():
        errors.append(f"opencode plugin source directory is not regular: {plugins}")
        return
    expected_plugins = set(OPENCODE_PLUGINS)
    try:
        plugin_entries = {path.name: path for path in plugins.iterdir()}
    except OSError as error:
        errors.append(f"opencode plugin source could not be listed: {error}")
        return
    if set(plugin_entries) != expected_plugins:
        errors.append(
            "opencode plugin source roster must be exactly "
            f"{sorted(expected_plugins)!r}, found {sorted(plugin_entries)!r}"
        )
    for name in expected_plugins:
        path = plugins / name
        metadata = _lstat(path)
        if metadata is None or not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            errors.append(f"opencode plugin source file is not regular: {path}")


def _parse_overlay_frontmatter(
    contents: str, label: str, errors: list[str]
) -> tuple[dict[str, str], set[str], str] | None:
    lines = contents.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"{label} must start with frontmatter")
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        errors.append(f"{label} frontmatter is not closed")
        return None
    scalars: dict[str, str] = {}
    mappings: set[str] = set()
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = len(line) - len(line.lstrip(" "))
        if indentation != 0:
            continue
        key, separator, raw_value = line.strip().partition(":")
        if not separator or not key:
            errors.append(f"{label} frontmatter contains an invalid line")
            continue
        if key in scalars or key in mappings:
            errors.append(f"{label} frontmatter key {key!r} is duplicated")
            continue
        value = raw_value.strip()
        if not value:
            mappings.add(key)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                errors.append(f"{label} frontmatter value {key!r} is invalid")
                continue
            if not isinstance(parsed, str):
                errors.append(f"{label} frontmatter value {key!r} is not a string")
                continue
            value = parsed
        scalars[key] = value
    return scalars, mappings, "\n".join(lines[1:end])


def _read_overlay_text(path: Path, label: str, errors: list[str]) -> str | None:
    if path.is_symlink():
        errors.append(f"{label} must not be a symlink: {path}")
        return None
    try:
        metadata = _lstat(path)
    except OSError:
        metadata = None
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        errors.append(f"{label} is missing: {path}")
        return None
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        errors.append(f"{label} could not be read: {error}")
        return None
    if not contents.strip():
        errors.append(f"{label} must be non-empty: {path}")
        return None
    if PLACEHOLDER in contents:
        errors.append(f"{label} contains a placeholder")
        return None
    return contents


def _active_opencode_model(package_root: Path) -> tuple[str | None, str | None]:
    try:
        spec = json.loads((package_root / "agents.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(spec, dict):
        return None, None
    profiles = spec.get("model_profiles")
    default_name = spec.get("default_model_profile")
    if not isinstance(profiles, dict) or not isinstance(default_name, str):
        return None, None
    active = profiles.get(default_name)
    if not isinstance(active, dict):
        return None, None
    model = active.get("model")
    effort = active.get("reasoningEffort")
    return (
        model if isinstance(model, str) and model.strip() else None,
        effort if isinstance(effort, str) and effort.strip() else None,
    )


def _artifact_output_relative(source_relative: str) -> str | None:
    """Map one provenance input to its published artifact path."""

    relative = Path(source_relative)
    package_marker = Path("packages") / "expskill"
    if relative.parts[:2] != package_marker.parts:
        return None
    within = Path(*relative.parts[2:])
    if within.parts and within.parts[0] in COPY_TREES:
        return within.as_posix()
    if within in COPY_FILES:
        return within.as_posix()
    if within.parts[:2] == COPY_LICENSES.parts:
        return within.as_posix()
    if within.parts[:2] == ("opencode", PLATFORM_PLUGIN_DIRECTORY):
        return Path(*within.parts[1:]).as_posix()
    if within.as_posix() in PLATFORM_FILES:
        return within.as_posix()
    return None


def _artifact_inventory(
    artifact: Path,
) -> tuple[dict[str, os.stat_result], list[str]]:
    """Enumerate every artifact entry without following symlinks."""

    entries: dict[str, os.stat_result] = {}
    errors: list[str] = []
    try:
        root_metadata = os.lstat(artifact)
    except OSError as error:
        return {}, [f"opencode artifact root cannot be inspected: {error}"]
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        return {}, [f"opencode artifact root must be a regular directory: {artifact}"]
    pending = [artifact]
    while pending:
        current = pending.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name)
        except OSError as error:
            errors.append(f"opencode artifact directory cannot be listed: {current}: {error}")
            continue
        for child in children:
            relative = child.relative_to(artifact).as_posix()
            try:
                metadata = os.lstat(child)
            except OSError as error:
                errors.append(f"opencode artifact entry cannot be inspected: {child}: {error}")
                continue
            entries[relative] = metadata
            if stat.S_ISLNK(metadata.st_mode):
                errors.append(f"opencode artifact entry must not be a symlink: {child}")
            elif stat.S_ISDIR(metadata.st_mode):
                pending.append(child)
            elif not stat.S_ISREG(metadata.st_mode):
                errors.append(f"opencode artifact entry must be regular: {child}")
    return entries, errors


def _validate_built_opencode_artifact(
    repository_root: Path,
    artifact: Path,
    rendered: dict[str, str],
    errors: list[str],
) -> None:
    """Validate the exact built bytes, provenance, inventory, and metadata."""

    expected_files: dict[str, bytes] = {}
    package_root = repository_root / "packages" / "expskill"
    platform_root = package_root / "opencode"
    try:
        for name in PLATFORM_FILES:
            expected_files[name] = (platform_root / name).read_bytes()
        for name in PLATFORM_PLUGIN_FILES:
            expected_files[f"{PLATFORM_PLUGIN_DIRECTORY}/{name}"] = (
                platform_root / PLATFORM_PLUGIN_DIRECTORY / name
            ).read_bytes()
        for relative, source in _provenance_sources(repository_root):
            output_relative = _artifact_output_relative(relative)
            if output_relative is not None:
                expected_files[output_relative] = source.read_bytes()
        expected_files.update(
            {relative: contents.encode("utf-8") for relative, contents in rendered.items()}
        )
        expected_inputs = [
            {"path": relative, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
            for relative, source in _provenance_sources(repository_root)
        ]
    except (OSError, OpencodeBuildError, RuntimeError) as error:
        errors.append(f"opencode artifact inputs could not be inventoried: {error}")
        return

    provenance_path = artifact / "provenance.json"
    provenance: object | None = None
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        errors.append(f"opencode artifact provenance is invalid: {error}")
    if isinstance(provenance, dict):
        if set(provenance) != {"schema_version", "inputs"}:
            errors.append("opencode artifact provenance must contain exactly schema_version and inputs")
        if provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
            errors.append(
                f"opencode artifact provenance schema_version must be {PROVENANCE_SCHEMA_VERSION!r}"
            )
        inputs = provenance.get("inputs")
        if not isinstance(inputs, list):
            errors.append("opencode artifact provenance inputs must be a list")
        else:
            normalized_inputs: list[dict[str, str]] = []
            for index, entry in enumerate(inputs):
                if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                    errors.append(f"opencode artifact provenance input {index} is malformed")
                    continue
                path = entry.get("path")
                digest = entry.get("sha256")
                if not isinstance(path, str) or not isinstance(digest, str):
                    errors.append(f"opencode artifact provenance input {index} has invalid fields")
                    continue
                normalized_inputs.append({"path": path, "sha256": digest})
            if [item["path"] for item in normalized_inputs] != sorted(
                item["path"] for item in normalized_inputs
            ):
                errors.append("opencode artifact provenance paths must be sorted")
            if len({item["path"] for item in normalized_inputs}) != len(normalized_inputs):
                errors.append("opencode artifact provenance paths must be unique")
            if normalized_inputs != expected_inputs:
                errors.append("opencode artifact provenance digests do not match every expected input")
            expected_files["provenance.json"] = (
                json.dumps(
                    {"schema_version": PROVENANCE_SCHEMA_VERSION, "inputs": expected_inputs},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
    expected_paths: set[str] = set(expected_files)
    expected_paths.add("provenance.json")
    expected_entries = set(expected_paths)
    for relative in expected_paths:
        parent = Path(relative).parent
        while parent != Path("."):
            expected_entries.add(parent.as_posix())
            parent = parent.parent
    actual_entries, inventory_errors = _artifact_inventory(artifact)
    errors.extend(inventory_errors)
    try:
        artifact_metadata = os.lstat(artifact)
    except OSError as error:
        artifact_metadata = None
        errors.append(f"opencode artifact root cannot be read for metadata: {error}")
    if artifact_metadata is not None:
        if stat.S_IMODE(artifact_metadata.st_mode) != ARTIFACT_DIRECTORY_MODE:
            errors.append("opencode artifact root has non-normalized mode")
        if artifact_metadata.st_mtime_ns != ARTIFACT_MTIME:
            errors.append("opencode artifact root has non-normalized mtime")
    actual_paths = set(actual_entries)
    for relative in sorted(actual_paths - expected_entries):
        errors.append(f"opencode artifact contains unexpected entry: {relative}")
    for relative in sorted(expected_entries - actual_paths):
        errors.append(f"opencode artifact is missing entry: {relative}")
    for relative, metadata in actual_entries.items():
        if stat.S_ISDIR(metadata.st_mode):
            expected_mode = ARTIFACT_DIRECTORY_MODE
        elif stat.S_ISREG(metadata.st_mode):
            expected_mode = ARTIFACT_FILE_MODE
        else:
            continue
        if stat.S_IMODE(metadata.st_mode) != expected_mode:
            errors.append(f"opencode artifact entry {relative} has non-normalized mode")
        if metadata.st_mtime_ns != ARTIFACT_MTIME:
            errors.append(f"opencode artifact entry {relative} has non-normalized mtime")
    for relative, expected in expected_files.items():
        path = artifact / relative
        try:
            actual = path.read_bytes()
        except OSError as error:
            errors.append(f"opencode artifact file {relative} could not be read: {error}")
            continue
        if actual != expected:
            errors.append(f"opencode artifact file {relative} does not match its accepted bytes")


def _validate_opencode_package(repository_root: Path, errors: list[str]) -> None:
    package_root = repository_root / "packages" / "expskill" / "opencode"
    if not _validate_opencode_root(package_root, errors):
        return
    _validate_opencode_platform_source(package_root, errors)
    try:
        skill_names = _skill_inventory(repository_root)
        rendered = _render_opencode_all(repository_root)
    except AgentSyncError as error:
        errors.append(f"opencode sources cannot be rendered: {error}")
        return

    # Platform-owned files are checked in, while commands, agents, shared
    # trees, and the runtime catalog are deliberately validated from a fresh
    # temporary artifact.  Validation therefore exercises the same pure
    # renderer and builder used by releases without mutating this checkout.
    _validate_opencode_manifest(package_root, errors)
    _validate_opencode_agent_spec(package_root, errors)
    _validate_opencode_plugins(package_root, errors)
    with tempfile.TemporaryDirectory(prefix="expskill-opencode-validate-") as temporary:
        artifact = Path(temporary) / "artifact"
        try:
            build_opencode_package(repository_root, artifact)
        except (OpencodeBuildError, OSError) as error:
            errors.append(f"opencode artifact could not be built: {error}")
            return
        _validate_built_opencode_artifact(repository_root, artifact, rendered, errors)
        _validate_opencode_shared_skills(repository_root / "packages" / "expskill", artifact, errors, skill_names)
        _validate_opencode_commands(artifact, errors, skill_names)
        _validate_opencode_agents(repository_root, repository_root / "packages" / "expskill", artifact, errors)
        _validate_opencode_policy_asset(repository_root / "packages" / "expskill", artifact, errors)
        _validate_opencode_catalog(artifact, rendered, errors)


def _validate_opencode_manifest(package_root: Path, errors: list[str]) -> None:
    manifest_path = package_root / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"opencode package manifest could not be read: {error}")
        return
    except json.JSONDecodeError as error:
        errors.append(f"opencode package manifest is not valid JSON: {error.msg}")
        return
    if not isinstance(manifest, dict):
        errors.append("opencode package manifest must contain a JSON object")
        return
    _reject_placeholders(manifest, "opencode package.json", errors)
    if manifest.get("name") != OPENCODE_PACKAGE_NAME:
        errors.append(
            f"opencode package name must be {OPENCODE_PACKAGE_NAME!r}, "
            f"got {manifest.get('name')!r}"
        )
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("opencode package version must be a non-empty string")
    elif version != PLUGIN_VERSION:
        errors.append(
            f"opencode package version must match the Codex base version "
            f"{PLUGIN_VERSION!r}, got {version!r}"
        )
    if manifest.get("private") is True:
        errors.append("opencode package manifest must be publishable")
    if manifest.get("type") != "module":
        errors.append("opencode package manifest type must be 'module'")
    if manifest.get("main") != "./index.js":
        errors.append("opencode package manifest main must be './index.js'")
    if manifest.get("exports") != OPENCODE_PACKAGE_EXPORTS:
        errors.append("opencode package manifest must declare the exact root export")
    if manifest.get("files") != list(OPENCODE_PACKAGE_FILES):
        errors.append("opencode package manifest must declare the exact publish file roster")
    if manifest.get("license") != "MIT":
        errors.append("opencode package manifest license must be 'MIT'")


def _validate_opencode_agent_spec(package_root: Path, errors: list[str]) -> None:
    spec_path = package_root / "agents.json"
    spec = _load_json_object(spec_path, "opencode agent spec", errors)
    if spec is None:
        return
    if spec.get("schema_version") != "opencode-agents.v1":
        errors.append("opencode agent spec schema_version must be 'opencode-agents.v1'")
    profiles = spec.get("model_profiles")
    if not isinstance(profiles, dict) or not profiles:
        errors.append("opencode agent spec must declare model_profiles")
        return
    default_name = spec.get("default_model_profile")
    if not isinstance(default_name, str) or default_name not in profiles:
        errors.append("opencode agent spec default_model_profile must name a declared profile")
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            errors.append(f"opencode model profile {profile_name!r} must be a mapping")
            continue
        model = profile.get("model")
        effort = profile.get("reasoningEffort")
        if not isinstance(model, str) or not model.strip() or "/" not in model:
            errors.append(
                f"opencode model profile {profile_name!r} model must be a provider/model string"
            )
        if not isinstance(effort, str) or not effort.strip():
            errors.append(
                f"opencode model profile {profile_name!r} must declare reasoningEffort"
            )
    if not isinstance(spec.get("runtime_paragraph"), str) or not str(
        spec.get("runtime_paragraph")
    ).strip():
        errors.append("opencode agent spec must declare a non-empty runtime_paragraph")
    entries = spec.get("agents")
    if not isinstance(entries, dict) or set(entries) != set(OPENCODE_AGENTS):
        errors.append("opencode agent spec agents must cover the exact agent roster")
        return
    for name in OPENCODE_READ_ONLY_GIT_AGENTS:
        entry = entries.get(name)
        if not isinstance(entry, dict):
            continue
        permission = entry.get("permission")
        if not isinstance(permission, dict):
            continue
        bash = permission.get("bash")
        if bash != OPENCODE_READ_ONLY_GIT_RULES:
            errors.append(
                f"opencode agent {name!r} read-only Git permission must use the bounded "
                "inspection rule set"
            )
        elif list(bash.items()) != list(OPENCODE_READ_ONLY_GIT_RULE_ORDER):
            errors.append(
                f"opencode agent {name!r} read-only Git permission must preserve the "
                "canonical rule order because OpenCode uses the last matching rule"
            )


def _validate_opencode_shared_skills(
    codex_root: Path,
    package_root: Path,
    errors: list[str],
    skill_names: tuple[str, ...],
) -> None:
    skills_entry = package_root / "skills"
    if not skills_entry.exists():
        errors.append(f"opencode shared skills entry is missing: {skills_entry}")
        return
    for name in skill_names:
        label = f"opencode shared skill {name!r}"
        try:
            shared = (codex_root / "skills" / name / "SKILL.md").read_bytes()
        except OSError as error:
            errors.append(f"{label} canonical skill could not be read: {error}")
            continue
        try:
            exposed = (skills_entry / name / "SKILL.md").read_bytes()
        except OSError:
            errors.append(f"{label} is missing: {skills_entry / name / 'SKILL.md'}")
            continue
        if exposed != shared:
            errors.append(f"{label} is not the exact shared base")
            continue
        try:
            contents = shared.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{label} canonical skill is not UTF-8 text")
            continue
        probe: list[str] = []
        frontmatter = _parse_frontmatter(contents, name, probe)
        if probe or frontmatter is None:
            continue
        metadata = frontmatter.get("metadata")
        if not isinstance(metadata, dict) or set(metadata) != set(SHARED_SKILL_METADATA_KEYS):
            errors.append(f"{label} frontmatter metadata must declare the exact opencode keys")
            continue
        if metadata.get("opencode/slash") != "true":
            errors.append(f"{label} frontmatter metadata opencode/slash must be 'true'")
        expected_autoinvoke = "true" if name == "use-expskill" else "false"
        if metadata.get("opencode/autoinvoke") != expected_autoinvoke:
            errors.append(
                f"{label} frontmatter metadata opencode/autoinvoke must be "
                f"{expected_autoinvoke!r}"
            )
        description = frontmatter.get("description")
        if isinstance(description, str) and len(description) > 1024:
            errors.append(f"{label} description exceeds the opencode discovery limit")


def _validate_opencode_commands(
    package_root: Path, errors: list[str], skill_names: tuple[str, ...]
) -> None:
    commands_root = package_root / "commands"
    if not commands_root.is_dir() or commands_root.is_symlink():
        errors.append(f"opencode commands directory is missing: {commands_root}")
        return
    actual = {
        path.name
        for path in commands_root.iterdir()
        if not path.is_symlink() and path.is_file()
    }
    expected = {f"{name}.md" for name in skill_names}
    for name in sorted(expected - actual):
        errors.append(f"opencode command {name!r} is missing")
    for name in sorted(actual - expected):
        errors.append(f"opencode unexpected command entry {name!r}")
    for skill in skill_names:
        path = commands_root / f"{skill}.md"
        contents = _read_overlay_text(path, f"opencode command {skill!r}", errors)
        if contents is None:
            continue
        parsed = _parse_overlay_frontmatter(contents, f"opencode command {skill!r}", errors)
        if parsed is None:
            continue
        scalars, _mappings, _block = parsed
        if set(scalars) != {"description"}:
            errors.append(f"opencode command {skill!r} frontmatter keys must be exactly description")
            continue
        description = scalars["description"]
        if not description.strip() or len(description) > 160:
            errors.append(f"opencode command {skill!r} description must be 1-160 characters")
        if any(character in description for character in "<>\r\n"):
            errors.append(f"opencode command {skill!r} description contains forbidden characters")
        body = contents.splitlines()
        try:
            end = body.index("---", 1)
            text = "\n".join(body[end + 1 :])
        except ValueError:
            continue
        for marker in (f"`{skill}`", "skill tool", "$ARGUMENTS"):
            if marker not in text:
                errors.append(f"opencode command {skill!r} body must reference {marker}")


def _validate_opencode_agents(
    repository_root: Path, codex_root: Path, package_root: Path, errors: list[str]
) -> None:
    agents_root = package_root / "agents"
    if not agents_root.is_dir() or agents_root.is_symlink():
        errors.append(f"opencode agents directory is missing: {agents_root}")
        return
    actual = {
        path.name
        for path in agents_root.iterdir()
        if not path.is_symlink() and path.is_file()
    }
    expected = {f"{name}.md" for name in OPENCODE_AGENTS}
    for name in sorted(expected - actual):
        errors.append(f"opencode agent {name!r} is missing")
    for name in sorted(actual - expected):
        errors.append(f"opencode unexpected agent entry {name!r}")
    try:
        rendered = _render_opencode_agents(repository_root)
    except AgentSyncError as error:
        errors.append(f"opencode agents cannot be rendered from shared sources: {error}")
        return
    stale: list[str] = []
    for name in OPENCODE_AGENTS:
        path = agents_root / f"{name}.md"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if current != rendered[name]:
            stale.append(name)
    if stale:
        errors.append(
            "opencode agents differ from their shared sources "
            f"({', '.join(stale)}); rebuild the explicit OpenCode output"
        )
        return
    profile_model, profile_effort = _active_opencode_model(package_root)
    for name in OPENCODE_AGENTS:
        path = agents_root / f"{name}.md"
        contents = _read_overlay_text(path, f"opencode agent {name!r}", errors)
        if contents is None:
            continue
        parsed = _parse_overlay_frontmatter(contents, f"opencode agent {name!r}", errors)
        if parsed is None:
            continue
        scalars, mappings, block = parsed
        keys = set(scalars) | mappings
        required = {"description", "mode", "model", "reasoningEffort", "permission"}
        if not required <= keys <= OPENCODE_AGENT_ALLOWED_FRONTMATTER:
            errors.append(
                f"opencode agent {name!r} frontmatter keys must be exactly "
                "description, mode, model, reasoningEffort, temperature, and permission"
            )
            continue
        if name in ("expskill-review", "expskill-spec") and "temperature" not in scalars:
            errors.append(f"opencode agent {name!r} frontmatter must declare temperature")
        if "permission" not in mappings:
            errors.append(f"opencode agent {name!r} permission must be a mapping")
        if scalars.get("mode") != "subagent":
            errors.append(f"opencode agent {name!r} mode must be subagent")
        if profile_model is not None and scalars.get("model") != profile_model:
            errors.append(
                f"opencode agent {name!r} model must match the active model profile"
            )
        if profile_effort is not None and scalars.get("reasoningEffort") != profile_effort:
            errors.append(
                f"opencode agent {name!r} reasoningEffort must match the active model profile"
            )
        try:
            profile = tomllib.loads(
                (codex_root / "assets" / "agents" / f"{name}.toml").read_text(encoding="utf-8")
            )
        except (OSError, tomllib.TOMLDecodeError) as error:
            errors.append(f"opencode agent {name!r} canonical profile could not be read: {error}")
            continue
        try:
            expected_description = _bounded_description(
                profile.get("description"), f"canonical agent profile {name!r} description"
            )
        except AgentSyncError as error:
            errors.append(str(error))
            expected_description = None
        if expected_description is not None and scalars.get("description") != expected_description:
            errors.append(
                f"opencode agent {name!r} description must match the bounded canonical profile"
            )
        for marker in ("task: deny", "question: deny"):
            if marker not in block:
                errors.append(f"opencode agent {name!r} permission must declare {marker}")
        if name in OPENCODE_READ_ONLY_AGENTS:
            if "edit: deny" not in block:
                errors.append(f"opencode agent {name!r} must be read-only")
        else:
            if "edit: allow" not in block:
                errors.append(f"opencode agent {name!r} must allow workspace edits")
            for marker in ('git push *": deny', 'git merge *": deny', 'gh *": deny'):
                if marker not in block:
                    errors.append(f"opencode agent {name!r} permission must declare {marker}")
        instructions = profile.get("developer_instructions", "")
        normalized = " ".join(contents.lower().split())
        if isinstance(instructions, str) and instructions.strip():
            first_sentence = instructions.strip().split("\n")[0].strip().lower()
            if first_sentence and first_sentence not in normalized:
                errors.append(
                    f"opencode agent {name!r} body must carry the canonical instructions"
                )
        for phrase in OPENCODE_AGENT_BOUNDARIES[name]:
            if phrase not in normalized:
                errors.append(f"opencode agent {name!r} body must include {phrase!r}")


def _validate_opencode_policy_asset(
    codex_root: Path,
    package_root: Path,
    errors: list[str],
) -> None:
    canonical = codex_root / POLICY_PATH
    mirror = _required_package_path(
        package_root,
        POLICY_PATH,
        "opencode execution policy asset",
        "file",
        errors,
    )
    if mirror is None:
        return
    try:
        canonical_bytes = canonical.read_bytes()
        mirror_bytes = mirror.read_bytes()
    except OSError as error:
        errors.append(f"opencode execution policy asset could not be read: {error}")
        return
    if mirror_bytes != canonical_bytes:
        errors.append("opencode execution policy asset must mirror the canonical Codex asset")


def _validate_opencode_catalog(
    package_root: Path, rendered: dict[str, str], errors: list[str]
) -> None:
    catalog_path = package_root / "catalog.json"
    catalog = _load_json_object(catalog_path, "opencode runtime catalog", errors)
    if catalog is None:
        return
    try:
        expected = json.loads(rendered["catalog.json"])
    except (KeyError, json.JSONDecodeError) as error:
        errors.append(f"opencode runtime catalog renderer output is invalid: {error}")
        return
    if catalog != expected:
        errors.append("opencode runtime catalog differs from the pure renderer output")
    commands = catalog.get("commands")
    if not isinstance(commands, dict):
        errors.append("opencode runtime catalog commands must be an object")
        return
    for name, entry in commands.items():
        if not isinstance(entry, dict):
            errors.append(f"opencode runtime catalog command {name!r} must be an object")
            continue
        description = entry.get("description")
        if not isinstance(description, str) or not 1 <= len(description) <= OPENCODE_DESCRIPTION_MAX_LENGTH:
            errors.append(
                f"opencode runtime catalog command {name!r} description must be 1-160 characters"
            )


def _validate_opencode_plugins(package_root: Path, errors: list[str]) -> None:
    plugins_root = package_root / "plugins"
    if not plugins_root.is_dir() or plugins_root.is_symlink():
        errors.append(f"opencode plugins directory is missing: {plugins_root}")
        return
    actual = {
        path.name
        for path in plugins_root.iterdir()
        if not path.is_symlink() and path.is_file()
    }
    if actual != set(OPENCODE_PLUGINS):
        errors.append(
            f"opencode plugins must be exactly {sorted(OPENCODE_PLUGINS)!r}, "
            f"found {sorted(actual)!r}"
        )
    unslop = _read_overlay_text(plugins_root / "unslop.js", "opencode unslop plugin", errors)
    if unslop is not None:
        for marker in (
            "experimental.chat.system.transform",
            "experimental.session.compacting",
            "5000",
            "unslop-scope",
            "SKILL.md",
        ):
            if marker not in unslop:
                errors.append(f"opencode unslop plugin is missing required marker {marker!r}")
    policy = _read_overlay_text(
        plugins_root / "execution-policy.js", "opencode execution-policy plugin", errors
    )
    if policy is not None:
        for marker in (
            "tool.execute.before",
            "execution-policy.json",
            "max_agent_calls",
            "maxAgentCalls",
            "allowed_profiles",
            "selected",
            "expskill-",
            "implement.standard",
            "use-expskill.parallel-plan-design",
            "routeBudgets",
            "failed to load",
        ):
            if marker not in policy:
                errors.append(f"opencode execution-policy plugin is missing required marker {marker!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the expskill repository contract.")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = validate_repository(args.root)
    if errors:
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Repository contract passed: {Path(args.root).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
