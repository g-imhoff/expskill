"""Build an installable Codex marketplace from the authored source tree."""

from __future__ import annotations

import argparse
import json
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
    if output_dir is None:
        raise BuildError("an explicit output directory is required")
    root = _root(repo_root)
    output = _output(output_dir)
    container = Path(tempfile.mkdtemp(prefix=".codex-marketplace-build-", dir=output.parent))
    staging = container / "marketplace"
    try:
        (staging / "plugins").mkdir(parents=True)
        build_codex_package(root, staging / "plugins" / PLUGIN_NAME)
        manifest_path = staging / ".agents" / "plugins" / "marketplace.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(marketplace_manifest(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _normalize(staging)
        staging.rename(output)
    except BaseException:
        shutil.rmtree(container, ignore_errors=True)
        raise
    shutil.rmtree(container, ignore_errors=True)
    return output


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
