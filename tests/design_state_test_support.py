from __future__ import annotations


TECHNICAL_GATE_NAMES = frozenset(
    {
        "format",
        "lint",
        "type",
        "build",
        "runtime",
        "responsive",
        "accessibility",
        "interaction",
        "reduced_motion",
    }
)


def passing_technical(digest: str, command: str = "design-gates") -> dict[str, object]:
    gates: dict[str, dict[str, object]] = {
        name: {"status": "pass", "evidence_digest": digest, "details": {}}
        for name in TECHNICAL_GATE_NAMES
    }
    gates["responsive"]["details"] = {
        "page_overflow": {"compact": False, "intermediate": False, "wide": False}
    }
    gates["accessibility"]["details"] = {"serious": 0, "critical": 0}
    return {
        "status": "pass",
        "results": [{"command": command, "exit": 0, "output_digest": digest}],
        "gates": gates,
    }
