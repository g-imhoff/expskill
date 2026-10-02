"""Shared Design to Plan handoff contract.

This module is the single source of truth for what a Design delivery must
contain before the Plan typed Design join may accept it. It exists because
the Design output once failed the Plan join and needed a manual repair in
the repository to reach implement. Both helpers stay dependency-free and
keep their own validation, so this module duplicates nothing at runtime.
Instead it declares the exact fields, digests, lifecycle, and branch rules
once, and ``tests/test_design_plan_handoff_contract.py`` fails if either
helper drifts from it.

Research note: we considered a shared handoff folder created at workflow
start that both helpers would validate against. We rejected it. A third
state location adds locking, staleness, and permission failure modes on
top of two helpers that already own private state roots, while the actual
failures were mistyped or missing receipt fields and unchecked branch
assumptions. A declared contract with validation on both sides gives the
same guarantee with no new state to keep in sync.
"""

from __future__ import annotations

import json
import re
import sys

CONTRACT_VERSION = 1

DELIVER_OPERATION = "deliver"
DELIVERED_LIFECYCLE = "delivered"
DELIVERY_RECEIPT_SCHEMA_VERSION = 1
DESIGN_JOIN_RECEIPT_SCHEMA = "plan-design-join.v1"

WORKFLOW_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z|[0-9a-f]{64}\Z")

DELIVERY_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "operation",
        "workflow_id",
        "revision",
        "lifecycle",
        "identity",
        "state_digest",
        "candidate_digest",
        "candidate_inventory_digest",
        "review_evidence_digest",
        "manifest_digest",
        "evidence_digest",
        "approval_digest",
        "dependency_digest",
        "brief_digest",
        "candidate_commit",
    }
)

DELIVERY_IDENTITY_FIELDS = frozenset(
    {
        "repository",
        "branch",
        "worktree",
        "baseline",
        "head",
        "dirty_fingerprint",
        "ui_contract_digest",
    }
)

JOIN_BINDING_FIELDS = (
    "workflow_id",
    "revision",
    "baseline",
    "branch",
    "candidate_commit",
    "brief_digest",
    "approval_digest",
    "manifest_digest",
)

REQUIREMENTS = (
    "The delivery receipt carries every field in DELIVERY_RECEIPT_FIELDS, "
    "operation 'deliver', and lifecycle 'delivered'.",
    "brief_digest is the confirmed Design brief digest and candidate_commit "
    "is the checkpointed candidate commit. Direct-mode Design output has "
    "neither and is never join-eligible.",
    "The delivery baseline equals the Plan graph baseline byte for byte.",
    "The delivery branch is isolated: it differs from the Plan target branch.",
    "The candidate is exactly one commit above the baseline, and the isolated "
    "branch tip stays the candidate commit until integration.",
    "approval_digest and manifest_digest in the join match the delivery "
    "receipt. Copy them from the receipt, never retype them.",
)


def validate_delivery_receipt_shape(receipt: object) -> list[str]:
    """Return one problem string per contract violation, empty when clean."""
    problems: list[str] = []
    if not isinstance(receipt, dict) or any(not isinstance(key, str) for key in receipt):
        return ["delivery receipt must be a mapping with string field names"]
    missing = sorted(DELIVERY_RECEIPT_FIELDS - set(receipt))
    extra = sorted(set(receipt) - DELIVERY_RECEIPT_FIELDS)
    if missing:
        problems.append(f"missing receipt fields: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected receipt fields: {', '.join(extra)}")
    version = receipt.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != DELIVERY_RECEIPT_SCHEMA_VERSION:
        problems.append(
            "schema_version must be "
            f"{DELIVERY_RECEIPT_SCHEMA_VERSION}, got {receipt.get('schema_version')!r}"
        )
    if receipt.get("operation") != DELIVER_OPERATION:
        problems.append(
            f"operation must be {DELIVER_OPERATION!r}, got {receipt.get('operation')!r}"
        )
    if receipt.get("lifecycle") != DELIVERED_LIFECYCLE:
        problems.append(
            f"lifecycle must be {DELIVERED_LIFECYCLE!r}, got {receipt.get('lifecycle')!r}"
        )
    workflow_id = receipt.get("workflow_id")
    if not isinstance(workflow_id, str) or not WORKFLOW_ID_RE.fullmatch(workflow_id):
        problems.append("workflow_id must be 32 lowercase hex characters")
    revision = receipt.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        problems.append("revision must be an integer of at least 1")
    identity = receipt.get("identity")
    if not isinstance(identity, dict) or any(not isinstance(key, str) for key in identity):
        problems.append("identity must be a mapping with string field names")
    else:
        missing_identity = sorted(DELIVERY_IDENTITY_FIELDS - set(identity))
        extra_identity = sorted(set(identity) - DELIVERY_IDENTITY_FIELDS)
        if missing_identity:
            problems.append(
                f"missing identity fields: {', '.join(missing_identity)}"
            )
        if extra_identity:
            problems.append(
                f"unexpected identity fields: {', '.join(extra_identity)}"
            )
        for field in ("repository", "branch", "worktree"):
            value = identity.get(field)
            maximum = 244 if field == "branch" else 16_384
            if field in identity and (
                not isinstance(value, str) or not value.strip()
                or len(value) > maximum or "\x00" in value
            ):
                problems.append(
                    f"identity.{field} must be nonblank text of at most {maximum} characters without NUL"
                )
        for field in ("baseline", "head"):
            value = identity.get(field)
            if field in identity and (
                not isinstance(value, str) or not COMMIT_RE.fullmatch(value)
            ):
                problems.append(f"identity.{field} must be a commit SHA")
        for field in ("dirty_fingerprint", "ui_contract_digest"):
            value = identity.get(field)
            if field in identity and (
                not isinstance(value, str) or not DIGEST_RE.fullmatch(value)
            ):
                problems.append(f"identity.{field} must be a 64-character digest")
    for field in (
        "state_digest",
        "candidate_digest",
        "candidate_inventory_digest",
        "review_evidence_digest",
        "manifest_digest",
        "evidence_digest",
        "approval_digest",
        "dependency_digest",
        "brief_digest",
    ):
        if field in receipt:
            value = receipt[field]
            if not isinstance(value, str) or not DIGEST_RE.fullmatch(value):
                problems.append(f"{field} must be a 64-character digest")
    if "candidate_commit" in receipt:
        value = receipt["candidate_commit"]
        if not isinstance(value, str) or not COMMIT_RE.fullmatch(value):
            problems.append("candidate_commit must be a commit SHA")
    identity_head = (
        identity.get("head") if isinstance(identity, dict) else None
    )
    if (
        isinstance(identity_head, str)
        and "candidate_commit" in receipt
        and isinstance(receipt["candidate_commit"], str)
        and identity_head != receipt["candidate_commit"]
    ):
        problems.append("identity.head must equal candidate_commit")
    return problems


def binding_problems(delivery: dict, expected: dict) -> list[str]:
    """Name each join binding where the delivery differs from expected."""
    problems = []
    for field in JOIN_BINDING_FIELDS:
        if field == "workflow_id":
            actual = delivery.get("workflow_id")
        elif field == "revision":
            actual = delivery.get("revision")
        elif field in ("baseline", "branch"):
            actual = (delivery.get("identity") or {}).get(field)
        elif field == "candidate_commit":
            actual = delivery.get("candidate_commit")
        else:
            actual = delivery.get(field)
        if actual != expected.get(field):
            problems.append(
                f"{field}: delivery has {actual!r}, join needs {expected.get(field)!r}"
            )
    return problems


def requirements_text() -> str:
    lines = [f"Design to Plan handoff contract (version {CONTRACT_VERSION}):"]
    lines.extend(f"{index + 1}. {item}" for index, item in enumerate(REQUIREMENTS))
    return "\n".join(lines) + "\n"


def _cli() -> int:
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "contract"):
        sys.stdout.write(requirements_text())
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "check":
        try:
            raw = sys.stdin.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("receipt exceeds size limit")
            receipt = json.loads(raw)
        except Exception as exc:
            sys.stderr.write(f"invalid receipt JSON: {exc}\n")
            return 1
        problems = validate_delivery_receipt_shape(receipt)
        sys.stdout.write(json.dumps({"eligible": not problems, "problems": problems}))
        return 0 if not problems else 1
    sys.stderr.write("usage: design_plan_handoff.py [contract|check]\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
