"""Public declarative contract for the OpenCode artifact.

The builder and validator deliberately consume this small module instead of
sharing private implementation helpers.  It describes the source roster,
published layout, and canonical provenance representation; each consumer is
responsible for independently walking and checking the bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


PROVENANCE_SCHEMA_VERSION = "opencode-provenance.v1"
PLATFORM_FILES = ("agents.json", "package.json", "README.md", "LICENSE", "index.js")
PLATFORM_PLUGIN_DIRECTORY = "plugins"
PLATFORM_PLUGIN_FILES = ("execution-policy.js", "unslop.js")
COPY_TREES = ("skills", "scripts")
COPY_FILES = (Path("assets/execution-policy.json"),)
COPY_LICENSES = Path("third-party/licenses")
ARTIFACT_DIRECTORY_MODE = 0o755
ARTIFACT_FILE_MODE = 0o644
ARTIFACT_MTIME = 0


def artifact_output_relative(source_relative: str | Path) -> str | None:
    """Map a repository source path to its published artifact path."""

    relative = Path(source_relative)
    package_marker = Path("plugins") / "expskill"
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
    if (
        len(within.parts) == 2
        and within.parts[0] == "opencode"
        and within.parts[1] in PLATFORM_FILES
    ):
        return within.parts[1]
    return None


def canonical_provenance(inputs: Sequence[Mapping[str, str]]) -> bytes:
    """Return the exact UTF-8 bytes required for ``provenance.json``."""

    import json

    payload = {"schema_version": PROVENANCE_SCHEMA_VERSION, "inputs": list(inputs)}
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
