"""Public declarative contract for the OpenCode artifact.

The builder and validator deliberately consume this small module instead of
sharing private implementation helpers.  It describes the source roster,
published layout, and canonical provenance representation; each consumer is
responsible for independently walking and checking the bytes.
"""

from __future__ import annotations

import os
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


def artifact_output_relative(source_relative: str | os.PathLike[str]) -> str | None:
    """Map a repository source path to its published artifact path."""

    try:
        lexical = os.fspath(source_relative)
    except TypeError:
        return None
    # A str subclass can override lexical methods such as ``split``.  Do not
    # let those overrides influence the contract's exact path checks.
    if type(lexical) is not str:
        return None

    # Validate the exact text returned by os.fspath before Path can normalize
    # any lexical spelling.  The artifact contract uses portable forward
    # slash paths, so backslashes and NULs are never valid source text.
    if not lexical or "\\" in lexical or "\x00" in lexical:
        return None
    components = lexical.split("/")
    absolute = lexical.startswith("/") or (
        len(lexical) >= 3 and lexical[1] == ":" and lexical[2] == "/"
    )
    if absolute or any(component in {"", ".", ".."} for component in components):
        return None

    relative = Path(*components)
    package_marker = Path("plugins") / "expskill"
    if relative.parts[:2] != package_marker.parts:
        return None
    within = Path(*relative.parts[2:])
    if len(within.parts) > 1 and within.parts[0] in COPY_TREES:
        return within.as_posix()
    if within in COPY_FILES:
        return within.as_posix()
    if len(within.parts) > len(COPY_LICENSES.parts) and within.parts[:2] == COPY_LICENSES.parts:
        return within.as_posix()
    if (
        len(within.parts) == 3
        and within.parts[0] == "opencode"
        and within.parts[1] == PLATFORM_PLUGIN_DIRECTORY
        and within.parts[2] in PLATFORM_PLUGIN_FILES
    ):
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
