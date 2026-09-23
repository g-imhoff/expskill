"""Build an installable Codex marketplace from the authored source tree."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

try:
    from scripts.build_codex_package import (
        BuildError,
        _normalize,
        _output,
        _root,
        build_codex_package,
    )
except ModuleNotFoundError:
    from build_codex_package import (  # type: ignore[no-redef]
        BuildError,
        _normalize,
        _output,
        _root,
        build_codex_package,
    )


MARKETPLACE_NAME = "expskill"
PLUGIN_NAME = "expskill"


def marketplace_manifest() -> dict[str, object]:
    return {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "ExpSkill"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {"source": "local", "path": "./plugins/expskill"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Developer Tools",
            }
        ],
    }


def build_codex_marketplace(
    repo_root: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> Path:
    output, descriptor = build_codex_marketplace_pinned(repo_root, output_dir)
    os.close(descriptor)
    return output


def build_codex_marketplace_pinned(
    repo_root: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> tuple[Path, int]:
    """Return the output and its retained directory descriptor (caller closes).

    Pin the constructed staging directory before publishing its pathname, so
    the caller never has to acquire ownership from a later path observation.
    """

    if output_dir is None:
        raise BuildError("an explicit output directory is required")
    root = _root(repo_root)
    output = _output(output_dir)
    container = Path(tempfile.mkdtemp(prefix=".codex-marketplace-build-", dir=output.parent))
    staging = container / "marketplace"
    descriptor = None
    try:
        (staging / "plugins").mkdir(parents=True)
        descriptor = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        build_codex_package(root, staging / "plugins" / PLUGIN_NAME)
        manifest_path = staging / ".agents" / "plugins" / "marketplace.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(marketplace_manifest(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _normalize(staging)
        staging.rename(output)
        shutil.rmtree(container, ignore_errors=True)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        shutil.rmtree(container, ignore_errors=True)
        raise
    assert descriptor is not None
    return output, descriptor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an installable Codex marketplace outside the source checkout."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=None)
    arguments = parser.parse_args(argv)
    try:
        build_codex_marketplace(arguments.root, arguments.output)
    except BuildError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
