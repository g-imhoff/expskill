from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

# ``install.py`` is a documented direct CLI.  Suppress local bytecode before
# importing the repository validator/builder so dry runs remain read-only.
sys.dont_write_bytecode = True

try:
    from scripts.build_opencode_package import BuildError as OpencodeBuildError
    from scripts.build_opencode_package import (
        _provenance_sources,
        _reject_symlink_components,
        build_opencode_package,
    )
    from scripts.render_opencode import (
        EXPECTED_AGENT_NAMES as _OPENCODE_AGENT_NAMES,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )
    from scripts.validate import validate_repository
except ModuleNotFoundError:
    from build_opencode_package import BuildError as OpencodeBuildError
    from build_opencode_package import (
        _provenance_sources,
        _reject_symlink_components,
        build_opencode_package,
    )
    from render_opencode import (
        EXPECTED_AGENT_NAMES as _OPENCODE_AGENT_NAMES,
        render_all as _render_opencode_all,
        skill_inventory as _skill_inventory,
    )
    from validate import validate_repository


MARKETPLACE_NAME = "expskill"
PLUGIN_NAME = "expskill"
PLUGIN_SELECTOR = "expskill@expskill"
PROFILE_NAMES = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
RETIRED_PROFILE_NAMES = (
    "expskill-critical-reviewer",
    "expskill-implementer-high",
    "expskill-reviewer",
    "expskill-verifier",
    "expskill-verifier-low",
)
RECEIPT_DIRECTORY = "expskill"
RECEIPT_FILENAME = "install.json"
OPENCODE_RECEIPT_FILENAME = "install-opencode.json"
OPENCODE_PACKAGE_NAME = "opencode-expskill"
OPENCODE_ARTIFACT_DIRECTORY = "opencode-artifact"
# Exact source/destination roster emitted by the parent-repository installer
# before receipt-owned artifacts were introduced.  These names are deliberately
# frozen: receipt migration must not turn arbitrary receipt text into deletion
# authority.
LEGACY_OPENCODE_SKILLS = (
    "brainstorm",
    "design",
    "grill-me",
    "implement",
    "plan",
    "setup-ui-testing",
    "skill-builder",
    "test",
    "unslop",
    "use-expskill",
)
LEGACY_OPENCODE_AGENTS = (
    "expskill-explorer",
    "expskill-planner",
    "expskill-designer",
    "expskill-implementer",
    "expskill-test-engineer",
    "expskill-review",
    "expskill-spec",
)
LEGACY_OPENCODE_PLUGINS = ("unslop.js", "execution-policy.js")


class InstallError(RuntimeError):
    pass


class Runner(Protocol):
    def __call__(self, command: list[str]) -> object:
        ...


@dataclass(frozen=True)
class ProfileLink:
    source: Path
    destination: Path


@dataclass(frozen=True)
class InstallResult:
    links: tuple[ProfileLink, ...] = ()
    created_links: tuple[ProfileLink, ...] = ()
    removed_links: tuple[ProfileLink, ...] = ()
    marketplace_added: bool = False
    plugin_installed: bool = False


@dataclass(frozen=True)
class _Receipt:
    repository_root: Path
    links: tuple[ProfileLink, ...]
    marketplace_added: bool
    plugin_installed: bool
    artifact_root: Path | None = None


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _canonical_repository_root(repo_root: Path, require_directory: bool = True) -> Path:
    candidate = Path(repo_root).expanduser()
    _reject_symlink_components(candidate, "repository root")
    try:
        canonical = candidate.resolve(strict=require_directory)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"repository root cannot be resolved: {candidate}: {error}") from error
    if require_directory and not canonical.is_dir():
        raise InstallError(f"repository root is not a directory: {canonical}")
    return canonical


def _validate_repository(repository_root: Path) -> None:
    errors = validate_repository(repository_root)
    # The upstream-derived copies are intentionally editable canonical skill
    # sources.  Their parity diagnostics are release hygiene checks, not
    # semantic defects that should prevent rebuilding a local artifact after a
    # legitimate source edit.  Preserve every other validator failure here.
    errors = tuple(
        error
        for error in errors
        if "does not match its declared derived upstream copy" not in error
    )
    if errors:
        raise InstallError("repository validation failed: " + "; ".join(errors))


def _assert_relative_no_symlink_components(root: Path, relative: Sequence[str]) -> None:
    current = root
    for component in relative:
        current = current / component
        if current.is_symlink():
            raise InstallError(f"repository profile path contains a symlink: {current}")


def _profile_sources(repository_root: Path) -> tuple[Path, ...]:
    _assert_relative_no_symlink_components(
        repository_root,
        ("packages", "expskill", "assets", "agents"),
    )
    agents_root = repository_root / "packages" / "expskill" / "assets" / "agents"
    if not agents_root.is_dir():
        raise InstallError(f"agent source directory is missing: {agents_root}")
    try:
        resolved_agents_root = agents_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"agent source directory cannot be resolved: {agents_root}: {error}") from error
    if resolved_agents_root != agents_root:
        raise InstallError(f"agent source directory resolves outside the repository: {agents_root}")
    discovered = tuple(sorted(agents_root.glob("expskill-*.toml"), key=lambda path: path.name))
    expected_names = {f"{name}.toml" for name in PROFILE_NAMES}
    discovered_names = {path.name for path in discovered}
    if discovered_names != expected_names or len(discovered) != len(expected_names):
        found = ", ".join(sorted(discovered_names)) or "none"
        expected = ", ".join(sorted(expected_names))
        raise InstallError(
            f"agent sources must be exactly the validated profiles; found {found}; expected {expected}"
        )
    sources: list[Path] = []
    for path in discovered:
        if path.is_symlink() or not path.is_file():
            raise InstallError(f"agent source is not a regular file: {path}")
        try:
            profile = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise InstallError(f"agent source is not valid TOML: {path}: {error}") from error
        if not isinstance(profile, dict) or profile.get("name") != path.stem:
            raise InstallError(f"agent source profile name does not match {path.name}")
        try:
            source = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise InstallError(f"agent source cannot be resolved: {path}: {error}") from error
        try:
            source.relative_to(agents_root)
        except ValueError as error:
            raise InstallError(f"agent source resolves outside the agent directory: {path}") from error
        if source.parent != agents_root:
            raise InstallError(f"agent source resolves outside the exact agent directory: {path}")
        sources.append(source)
    return tuple(sources)


def _validate_agent_directory(agents_directory: Path) -> None:
    if _lexists(agents_directory):
        if agents_directory.is_symlink():
            raise InstallError(f"agent destination directory is a symlink: {agents_directory}")
        if not agents_directory.is_dir():
            raise InstallError(f"agent destination directory is not a directory: {agents_directory}")
        return
    current = agents_directory.parent
    while current != current.parent:
        if _lexists(current) and not current.is_dir():
            raise InstallError(f"agent destination parent is not a directory: {current}")
        current = current.parent


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.normpath(os.path.abspath(os.fspath(path))))


def _has_dot_components(path: Path) -> bool:
    return any(component in {".", ".."} for component in path.parts)


def _same_owned_link(destination: Path, source: Path) -> bool:
    if not destination.is_symlink():
        return False
    try:
        return destination.resolve(strict=False) == source.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _expected_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    _validate_repository(canonical_root)
    sources = _profile_sources(canonical_root)
    canonical_codex_home = Path(codex_home).expanduser().resolve(strict=False)
    agents_directory = canonical_codex_home / "agents"
    _validate_agent_directory(agents_directory)
    return tuple(
        ProfileLink(source=source, destination=agents_directory / source.name)
        for source in sources
    )


def _allowlisted_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    canonical_codex_home = Path(codex_home).expanduser().resolve(strict=False)
    agents_directory = canonical_codex_home / "agents"
    _validate_agent_directory(agents_directory)
    source_directory = canonical_root / "packages" / "expskill" / "assets" / "agents"
    return tuple(
        ProfileLink(
            source=_lexical_absolute(source_directory / f"{name}.toml"),
            destination=agents_directory / f"{name}.toml",
        )
        for name in (*PROFILE_NAMES, *RETIRED_PROFILE_NAMES)
    )


def preflight_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    links = _expected_links(repo_root, codex_home)
    for link in links:
        if not _lexists(link.destination):
            continue
        if not _same_owned_link(link.destination, link.source):
            raise InstallError(f"refusing conflicting agent destination: {link.destination}")
    return links


def _receipt_path(state_home: Path) -> Path:
    canonical_state_home = Path(state_home).expanduser().resolve(strict=False)
    return canonical_state_home / RECEIPT_DIRECTORY / RECEIPT_FILENAME


def _receipt_links(
    value: object,
    receipt_path: Path,
    expected_links: Sequence[ProfileLink],
) -> tuple[ProfileLink, ...]:
    if not isinstance(value, list):
        raise InstallError(f"receipt links are malformed: {receipt_path}")
    expected = {(link.source, link.destination): link for link in expected_links}
    links: list[ProfileLink] = []
    seen_pairs: set[tuple[Path, Path]] = set()
    seen_destinations: set[Path] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source_value = entry.get("source")
        destination_value = entry.get("destination")
        if not isinstance(source_value, str) or not isinstance(destination_value, str):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source = Path(source_value).expanduser()
        destination = Path(destination_value).expanduser()
        if not source.is_absolute() or not destination.is_absolute():
            raise InstallError(f"receipt links must be absolute paths: {receipt_path}")
        if _has_dot_components(source) or _has_dot_components(destination):
            raise InstallError(f"receipt link contains traversal: {receipt_path}")
        canonical_source = _lexical_absolute(source)
        lexical_destination = _lexical_absolute(destination)
        pair = (canonical_source, lexical_destination)
        if pair not in expected:
            raise InstallError(f"receipt link is outside the selected repository or Codex home: {receipt_path}")
        if pair in seen_pairs or lexical_destination in seen_destinations:
            raise InstallError(f"receipt link is duplicated: {receipt_path}")
        seen_pairs.add(pair)
        seen_destinations.add(lexical_destination)
        links.append(expected[pair])
    return tuple(links)


def _read_receipt(
    receipt_path: Path,
    repository_root: Path,
    expected_links: Sequence[ProfileLink],
) -> _Receipt | None:
    if not _lexists(receipt_path):
        return None
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    repository_value = payload.get("repository_root")
    if not isinstance(repository_value, str):
        raise InstallError(f"receipt repository is missing: {receipt_path}")
    try:
        recorded_root = Path(repository_value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"receipt repository cannot be resolved: {receipt_path}") from error
    if recorded_root != repository_root:
        raise InstallError(
            f"receipt repository mismatch: recorded {recorded_root}, requested {repository_root}"
        )
    marketplace_added = payload.get("marketplace_added")
    plugin_installed = payload.get("plugin_installed")
    if not isinstance(marketplace_added, bool) or not isinstance(plugin_installed, bool):
        raise InstallError(f"receipt ownership flags are malformed: {receipt_path}")
    artifact_value = payload.get("artifact_root")
    artifact_root: Path | None = None
    if artifact_value is not None:
        if not isinstance(artifact_value, str):
            raise InstallError(f"receipt artifact root is malformed: {receipt_path}")
        artifact_root = Path(artifact_value).expanduser()
        if not artifact_root.is_absolute() or _has_dot_components(artifact_root):
            raise InstallError(f"receipt artifact root must be an absolute path: {receipt_path}")
        artifact_root = _lexical_absolute(artifact_root)
    links = _receipt_links(payload.get("links"), receipt_path, expected_links)
    return _Receipt(
        repository_root=recorded_root,
        links=links,
        marketplace_added=marketplace_added,
        plugin_installed=plugin_installed,
        artifact_root=artifact_root,
    )


def _write_receipt(receipt_path: Path, receipt: _Receipt) -> None:
    receipt_directory = receipt_path.parent
    if _lexists(receipt_path) and (receipt_path.is_symlink() or not receipt_path.is_file()):
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        receipt_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InstallError(f"cannot create receipt directory: {receipt_directory}: {error}") from error
    payload = {
        "links": [
            {"destination": str(link.destination), "source": str(link.source)}
            for link in sorted(receipt.links, key=lambda item: str(item.destination))
        ],
        "marketplace_added": receipt.marketplace_added,
        "plugin_installed": receipt.plugin_installed,
        "repository_root": str(receipt.repository_root),
    }
    if receipt.artifact_root is not None:
        payload["artifact_root"] = str(receipt.artifact_root)
    temporary_path: Path | None = None
    write_error: InstallError | None = None
    write_cause: OSError | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{RECEIPT_FILENAME}.", suffix=".tmp", dir=receipt_directory
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, receipt_path)
        temporary_path = None
    except OSError as error:
        write_cause = error
        write_error = InstallError(f"cannot write receipt: {receipt_path}: {error}")
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError as error:
                cleanup_failure = f"temporary receipt cleanup failed: {temporary_path}: {error}"
                if write_error is None:
                    write_error = InstallError(cleanup_failure)
                    write_cause = error
                else:
                    write_error = InstallError(f"{write_error}; {cleanup_failure}")
    if write_error is not None:
        raise write_error from write_cause


def _invoke_runner(run: Runner | Callable[[Sequence[str]], object], command: list[str]) -> _CommandResult:
    try:
        if callable(run):
            raw_result = run(command)
        else:
            runner_method = getattr(run, "run", None)
            if not callable(runner_method):
                raise TypeError("runner must be callable or provide run()")
            raw_result = runner_method(command)
    except subprocess.CalledProcessError as error:
        stdout = error.stdout if isinstance(error.stdout, str) else ""
        stderr = error.stderr if isinstance(error.stderr, str) else str(error)
        return _CommandResult(error.returncode, stdout, stderr)
    except OSError as error:
        raise InstallError(f"command could not be executed: {' '.join(command)}: {error}") from error
    except Exception as error:
        raise InstallError(f"command runner failed for {' '.join(command)}: {error}") from error
    if isinstance(raw_result, Mapping):
        return _CommandResult(0, json.dumps(raw_result), "")
    returncode = getattr(raw_result, "returncode", getattr(raw_result, "exit_code", None))
    stdout = getattr(raw_result, "stdout", "")
    stderr = getattr(raw_result, "stderr", "")
    if returncode is None and isinstance(raw_result, tuple) and len(raw_result) == 3:
        returncode, stdout, stderr = raw_result
    if returncode is None and isinstance(raw_result, str):
        returncode = 0
        stdout = raw_result
    if not isinstance(returncode, int):
        raise InstallError(f"command runner returned an unsupported result for {' '.join(command)}")
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    if not isinstance(stdout, str):
        stdout = str(stdout)
    if not isinstance(stderr, str):
        stderr = str(stderr)
    return _CommandResult(returncode, stdout, stderr)


def _run_command(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> _CommandResult:
    return _invoke_runner(run, command)


def _require_success(command: list[str], result: _CommandResult) -> None:
    if result.returncode == 0:
        return
    details = result.stderr.strip() or result.stdout.strip()
    suffix = f": {details}" if details else ""
    raise InstallError(
        f"command failed with exit code {result.returncode}: {' '.join(command)}{suffix}"
    )


def _parse_json(command: list[str], result: _CommandResult) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InstallError(f"command returned invalid JSON: {' '.join(command)}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"command returned non-object JSON: {' '.join(command)}")
    return payload


def _run_json(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> dict[str, Any]:
    result = _run_command(run, command)
    _require_success(command, result)
    return _parse_json(command, result)


def _canonical_source(value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _marketplace_state(payload: Mapping[str, Any], repository_root: Path) -> str:
    marketplaces = payload.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallError("marketplace list JSON did not contain marketplaces")
    found = False
    for marketplace in marketplaces:
        if not isinstance(marketplace, dict):
            raise InstallError("marketplace list JSON contained a non-object entry")
        if marketplace.get("name") != MARKETPLACE_NAME:
            continue
        found = True
        candidates = [_canonical_source(marketplace.get("root"))]
        marketplace_source = marketplace.get("marketplaceSource")
        if isinstance(marketplace_source, dict):
            candidates.append(_canonical_source(marketplace_source.get("source")))
        if any(candidate == repository_root for candidate in candidates):
            return "owned"
    return "foreign" if found else "absent"


def _validate_marketplace_add(payload: Mapping[str, Any], repository_root: Path) -> None:
    if payload.get("marketplaceName") != MARKETPLACE_NAME:
        raise InstallError("marketplace add JSON identified the wrong marketplace")
    installed_root = _canonical_source(payload.get("installedRoot"))
    if installed_root != repository_root:
        raise InstallError("marketplace add JSON identified the wrong repository")
    if not isinstance(payload.get("alreadyAdded"), bool):
        raise InstallError("marketplace add JSON did not report alreadyAdded")


def _plugin_entries(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    installed = payload.get("installed")
    if not isinstance(installed, list):
        raise InstallError("plugin list JSON did not contain installed plugins")
    entries: list[dict[str, Any]] = []
    for entry in installed:
        if not isinstance(entry, dict):
            raise InstallError("plugin list JSON contained a non-object entry")
        entries.append(entry)
    return entries


def _plugin_presence(payload: Mapping[str, Any]) -> str:
    for entry in _plugin_entries(payload):
        if entry.get("pluginId") == PLUGIN_SELECTOR:
            return "present"
    return "absent"


def _plugin_state(payload: Mapping[str, Any], repository_root: Path) -> str:
    for entry in _plugin_entries(payload):
        if entry.get("pluginId") != PLUGIN_SELECTOR:
            continue
        if entry.get("marketplaceName") != MARKETPLACE_NAME:
            return "foreign"
        marketplace_source = entry.get("marketplaceSource")
        source = None
        if isinstance(marketplace_source, dict):
            source = _canonical_source(marketplace_source.get("source"))
        if source == repository_root:
            return "owned"
        return "foreign"
    return "absent"


def _validated_manifest_version(repository_root: Path) -> str:
    manifest_path = repository_root / "packages" / "expskill" / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"validated plugin manifest could not be read: {manifest_path}: {error}") from error
    version = manifest.get("version")
    if not isinstance(version, str):
        raise InstallError(f"validated plugin manifest has no version: {manifest_path}")
    return version


def _validate_plugin_add(payload: Mapping[str, Any], expected_version: str) -> None:
    if payload.get("pluginId") != PLUGIN_SELECTOR:
        raise InstallError("plugin add JSON identified the wrong plugin")
    if payload.get("name") != PLUGIN_NAME:
        raise InstallError("plugin add JSON identified the wrong plugin name")
    if payload.get("marketplaceName") != MARKETPLACE_NAME:
        raise InstallError("plugin add JSON identified the wrong marketplace")
    if payload.get("version") != expected_version:
        raise InstallError("plugin add JSON identified the wrong version")
    if not isinstance(payload.get("installedPath"), str) or not payload["installedPath"]:
        raise InstallError("plugin add JSON did not report an installed path")


def _create_links(links: Sequence[ProfileLink], created: list[ProfileLink]) -> None:
    parents: list[Path] = []
    for link in links:
        if link.destination.parent not in parents:
            parents.append(link.destination.parent)
    for parent in parents:
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallError(
                f"cannot create agent destination directory: {parent}: {error}"
            ) from error
    for link in links:
        if _lexists(link.destination):
            if not _same_owned_link(link.destination, link.source):
                raise InstallError(f"refusing conflicting agent destination: {link.destination}")
            continue
        try:
            link.destination.symlink_to(link.source)
        except OSError as error:
            raise InstallError(f"cannot create agent link: {link.destination}: {error}") from error
        created.append(link)


def _rollback_links(links: Sequence[ProfileLink]) -> list[str]:
    failures: list[str] = []
    for link in reversed(tuple(links)):
        if not _lexists(link.destination):
            continue
        if not _same_recorded_link(link.destination, link.source):
            failures.append(f"link preserved because ownership changed: {link.destination}")
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
    return failures


def _same_recorded_link(destination: Path, source: Path) -> bool:
    if not destination.is_symlink():
        return False
    try:
        stored_target = Path(os.readlink(destination))
    except OSError:
        return False
    if not stored_target.is_absolute():
        stored_target = destination.parent / stored_target
    return _lexical_absolute(stored_target) == _lexical_absolute(source)


def _prune_retired_links(
    receipt_path: Path,
    receipt: _Receipt,
    current_links: Sequence[ProfileLink],
) -> tuple[_Receipt, tuple[ProfileLink, ...]]:
    current = receipt
    removed: list[ProfileLink] = []
    current_destinations = {link.destination for link in current_links}
    for link in receipt.links:
        if link.destination in current_destinations:
            continue
        remaining = tuple(item for item in current.links if item != link)
        if not _lexists(link.destination) or not _same_recorded_link(
            link.destination, link.source
        ):
            current = _persist_receipt(receipt_path, current, links=remaining)
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            raise InstallError(
                f"cannot remove retired agent link: {link.destination}: {error}"
            ) from error
        removed.append(link)
        current = _persist_receipt(receipt_path, current, links=remaining)
    return current, tuple(removed)


def _remove_command(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> str | None:
    try:
        result = _run_command(run, command)
    except InstallError as error:
        return str(error)
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        suffix = f": {details}" if details else ""
        return f"command failed with exit code {result.returncode}: {' '.join(command)}{suffix}"
    try:
        _parse_json(command, result)
    except InstallError as error:
        return str(error)
    return None


def _cleanup_after_install_failure(
    original: Exception,
    run: Runner | Callable[[Sequence[str]], object],
    created_links: Sequence[ProfileLink],
    plugin_new: bool,
    marketplace_new: bool,
) -> None:
    failures: list[str] = []
    if plugin_new:
        failure = _remove_command(
            run,
            ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"],
        )
        if failure:
            failures.append(f"plugin rollback: {failure}")
    if marketplace_new:
        failure = _remove_command(
            run,
            ["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"],
        )
        if failure:
            failures.append(f"marketplace rollback: {failure}")
    failures.extend(f"link rollback: {failure}" for failure in _rollback_links(created_links))
    if failures:
        raise InstallError(f"{original}; residual state or rollback failures: {'; '.join(failures)}") from original
    if isinstance(original, InstallError):
        raise original
    raise InstallError(str(original)) from original


def install(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
    agents_only: bool = False,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = preflight_links(canonical_root, codex_home)
    receipt_links = _allowlisted_links(canonical_root, codex_home)
    plugin_version = _validated_manifest_version(canonical_root)
    receipt_path_value = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path_value, canonical_root, receipt_links)
    if not agents_only:
        marketplace_payload = _run_json(
            run,
            ["codex", "plugin", "marketplace", "list", "--json"],
        )
        marketplace_state = _marketplace_state(marketplace_payload, canonical_root)
        if marketplace_state == "foreign":
            raise InstallError("marketplace name conflict from another repository")
    created_links: list[ProfileLink] = []
    marketplace_new = False
    plugin_new = False
    removed_links: tuple[ProfileLink, ...] = ()
    try:
        _create_links(links, created_links)
        if not agents_only:
            marketplace_add_command = [
                "codex",
                "plugin",
                "marketplace",
                "add",
                str(canonical_root),
                "--json",
            ]
            marketplace_add_result = _run_command(run, marketplace_add_command)
            _require_success(marketplace_add_command, marketplace_add_result)
            marketplace_new = marketplace_state == "absent"
            marketplace_add_json = _parse_json(marketplace_add_command, marketplace_add_result)
            _validate_marketplace_add(marketplace_add_json, canonical_root)
            plugin_payload = _run_json(run, ["codex", "plugin", "list", "--json"])
            plugin_state = _plugin_presence(plugin_payload)
            plugin_add_command = ["codex", "plugin", "add", PLUGIN_SELECTOR, "--json"]
            plugin_add_result = _run_command(run, plugin_add_command)
            _require_success(plugin_add_command, plugin_add_result)
            plugin_new = plugin_state == "absent"
            plugin_add_json = _parse_json(plugin_add_command, plugin_add_result)
            _validate_plugin_add(plugin_add_json, plugin_version)
        if receipt is not None:
            receipt, removed_links = _prune_retired_links(
                receipt_path_value,
                receipt,
                links,
            )
        previous_links = () if receipt is None else receipt.links
        merged_links = list(previous_links)
        known_destinations = {link.destination for link in merged_links}
        for link in created_links:
            if link.destination not in known_destinations:
                merged_links.append(link)
                known_destinations.add(link.destination)
        merged_receipt = _Receipt(
            repository_root=canonical_root,
            links=tuple(merged_links),
            marketplace_added=(receipt.marketplace_added if receipt else False) or marketplace_new,
            plugin_installed=(receipt.plugin_installed if receipt else False) or plugin_new,
        )
        _write_receipt(receipt_path_value, merged_receipt)
    except Exception as error:
        _cleanup_after_install_failure(
            error,
            run,
            created_links,
            plugin_new,
            marketplace_new,
        )
    return InstallResult(
        links=links,
        created_links=tuple(created_links),
        removed_links=removed_links,
        marketplace_added=marketplace_new,
        plugin_installed=plugin_new,
    )


def _marketplace_matches(repository_root: Path, payload: Mapping[str, Any]) -> str:
    return _marketplace_state(payload, repository_root)


def _persist_receipt(
    receipt_path: Path,
    receipt: _Receipt,
    *,
    links: Sequence[ProfileLink] | None = None,
    marketplace_added: bool | None = None,
    plugin_installed: bool | None = None,
) -> _Receipt:
    updated = _Receipt(
        repository_root=receipt.repository_root,
        links=tuple(receipt.links if links is None else links),
        marketplace_added=receipt.marketplace_added if marketplace_added is None else marketplace_added,
        plugin_installed=receipt.plugin_installed if plugin_installed is None else plugin_installed,
        artifact_root=receipt.artifact_root,
    )
    _write_receipt(receipt_path, updated)
    return updated


def _remove_owned_links(
    receipt_path: Path,
    receipt: _Receipt,
) -> tuple[_Receipt, tuple[ProfileLink, ...], list[str]]:
    current = receipt
    removed: list[ProfileLink] = []
    failures: list[str] = []
    for link in receipt.links:
        if not _lexists(link.destination):
            current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
            continue
        if not _same_recorded_link(link.destination, link.source):
            current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
            continue
        removed.append(link)
        current = _persist_receipt(receipt_path, current, links=tuple(item for item in current.links if item != link))
    return current, tuple(removed), failures


def uninstall(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
    agents_only: bool = False,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = _allowlisted_links(canonical_root, codex_home)
    receipt_path_value = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path_value, canonical_root, links)
    if receipt is None:
        return InstallResult(links=links)
    current = receipt
    plugin_state: str | None = None
    marketplace_state: str | None = None
    if not agents_only and receipt.plugin_installed:
        plugin_payload = _run_json(run, ["codex", "plugin", "list", "--json"])
        plugin_state = _plugin_state(plugin_payload, canonical_root)
    if not agents_only and receipt.marketplace_added:
        marketplace_payload = _run_json(
            run,
            ["codex", "plugin", "marketplace", "list", "--json"],
        )
        marketplace_state = _marketplace_matches(canonical_root, marketplace_payload)
    if plugin_state in {"owned", "absent"}:
        plugin_remove = ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"]
        plugin_remove_result = _run_command(run, plugin_remove)
        _require_success(plugin_remove, plugin_remove_result)
        _parse_json(plugin_remove, plugin_remove_result)
        current = _persist_receipt(receipt_path_value, current, plugin_installed=False)
    elif plugin_state == "foreign":
        current = _persist_receipt(receipt_path_value, current, plugin_installed=False)
    if marketplace_state == "owned":
        marketplace_remove = [
            "codex",
            "plugin",
            "marketplace",
            "remove",
            MARKETPLACE_NAME,
            "--json",
        ]
        marketplace_remove_result = _run_command(run, marketplace_remove)
        _require_success(marketplace_remove, marketplace_remove_result)
        _parse_json(marketplace_remove, marketplace_remove_result)
        current = _persist_receipt(receipt_path_value, current, marketplace_added=False)
    elif marketplace_state in {"absent", "foreign"}:
        current = _persist_receipt(receipt_path_value, current, marketplace_added=False)
    current, removed_links, link_failures = _remove_owned_links(receipt_path_value, current)
    if link_failures:
        raise InstallError("owned link cleanup failed: " + "; ".join(link_failures))
    if current.links:
        raise InstallError("owned link cleanup did not converge")
    if current.marketplace_added or current.plugin_installed:
        return InstallResult(
            links=links,
            removed_links=removed_links,
            marketplace_added=current.marketplace_added,
            plugin_installed=current.plugin_installed,
        )
    if receipt_path_value.is_symlink() or not receipt_path_value.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path_value}")
    try:
        receipt_path_value.unlink()
    except OSError as error:
        raise InstallError(f"cannot remove receipt: {receipt_path_value}: {error}") from error
    return InstallResult(
        links=links,
        removed_links=removed_links,
        marketplace_added=receipt.marketplace_added,
        plugin_installed=receipt.plugin_installed,
    )


def _subprocess_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def _default_state_home() -> Path:
    return Path(
        os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    ).expanduser()


def _print_dry_run(repo_root: Path, codex_home: Path, agents_only: bool = False) -> None:
    links = preflight_links(repo_root, codex_home)
    for link in links:
        print(f"link {link.destination} -> {link.source}")
    if agents_only:
        print("codex agent links only: the plugin itself stays managed through codex plugin CLI")
        return
    repository = links[0].source.parent.parent.parent.parent.parent
    print(f"codex plugin marketplace add {repository} --json")
    print(f"codex plugin add {PLUGIN_SELECTOR} --json")


def _default_opencode_config_dir() -> Path:
    explicit = os.environ.get("OPENCODE_CONFIG_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        xdg_path = Path(xdg_config_home)
        if xdg_path.is_absolute():
            return xdg_path / "opencode"
    return Path.home() / ".config" / "opencode"


def _opencode_receipt_path(state_home: Path) -> Path:
    # Inspect the caller's lexical path before resolving it.  A symlinked
    # state ancestor must never be silently redirected to an external tree.
    _assert_no_symlink_components(Path(state_home).expanduser(), "opencode state")
    try:
        canonical_state_home = Path(state_home).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"opencode state home cannot be resolved: {state_home}: {error}") from error
    receipt_directory = canonical_state_home / RECEIPT_DIRECTORY
    _assert_no_symlink_components(receipt_directory, "opencode state")
    if _lexists(receipt_directory) and not receipt_directory.is_dir():
        raise InstallError(f"opencode state directory is not a directory: {receipt_directory}")
    return receipt_directory / OPENCODE_RECEIPT_FILENAME


def _opencode_artifact_path(state_home: Path) -> Path:
    return _opencode_receipt_path(state_home).parent / OPENCODE_ARTIFACT_DIRECTORY


def _legacy_opencode_expected_links(
    repo_root: Path, config_dir: Path
) -> tuple[ProfileLink, ...]:
    """Return only the historical parent-installer link roster."""

    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    package_root = canonical_root / "packages" / "expskill"
    links: list[ProfileLink] = []
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "skills" / name),
                destination=canonical_config / "skills" / name,
            )
        )
    for name in LEGACY_OPENCODE_SKILLS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "commands" / f"{name}.md"),
                destination=canonical_config / "commands" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_AGENTS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "agents" / f"{name}.md"),
                destination=canonical_config / "agents" / f"{name}.md",
            )
        )
    for name in LEGACY_OPENCODE_PLUGINS:
        links.append(
            ProfileLink(
                source=_lexical_absolute(package_root / "opencode" / "plugins" / name),
                destination=canonical_config / "plugins" / name,
            )
        )
    for link in links:
        _validate_opencode_destination(link.destination, canonical_config)
    return tuple(links)


def _assert_no_symlink_components(path: Path, label: str) -> None:
    """Reject symlinked ancestors before reading or removing owned state."""

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for component in candidate.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError as error:
            raise InstallError(f"{label} cannot be inspected: {current}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise InstallError(f"{label} path component must not be a symlink: {current}")


def _canonical_opencode_config(config_dir: Path) -> Path:
    _assert_no_symlink_components(Path(config_dir).expanduser(), "OpenCode config")
    try:
        canonical = Path(config_dir).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"OpenCode config directory cannot be resolved: {config_dir}: {error}") from error
    if _lexists(canonical) and not canonical.is_dir():
        raise InstallError(f"OpenCode config path is not a directory: {canonical}")
    return canonical


def _validate_opencode_destination(destination: Path, config_dir: Path) -> None:
    """Ensure a destination's existing ancestors cannot redirect cleanup."""

    canonical_config = _canonical_opencode_config(config_dir)
    candidate = _lexical_absolute(destination)
    try:
        relative = candidate.relative_to(canonical_config)
    except ValueError as error:
        raise InstallError(f"opencode destination is outside the config directory: {candidate}") from error
    current = canonical_config
    for component in relative.parts[:-1]:
        current /= component
        if current.is_symlink():
            raise InstallError(f"opencode destination parent must not be a symlink: {current}")
        if _lexists(current) and not current.is_dir():
            raise InstallError(f"opencode destination parent is not a directory: {current}")


def _path_is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _fixed_opencode_artifact(state_home: Path, candidate: Path | None = None) -> Path:
    """Return the sole artifact path permitted for this state home."""

    expected = _lexical_absolute(_opencode_artifact_path(state_home))
    _assert_no_symlink_components(expected, "opencode artifact")
    if candidate is not None:
        observed = _lexical_absolute(Path(candidate).expanduser())
        if observed != expected:
            raise InstallError(
                f"receipt artifact is outside the fixed OpenCode state: {observed}"
            )
    if _lexists(expected):
        if expected.is_symlink() or not expected.is_dir():
            raise InstallError(f"opencode artifact is not a regular directory: {expected}")
    return expected


def _remove_opencode_artifact(path: Path) -> None:
    if not _lexists(path):
        return
    _assert_no_symlink_components(path, "opencode artifact")
    if path.is_symlink() or not path.is_dir():
        raise InstallError(f"opencode artifact is not a regular directory: {path}")
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise InstallError(f"cannot remove opencode artifact: {path}: {error}") from error


def _garbage_collect_opencode_backups(state_home: Path) -> None:
    """Best-effort cleanup of builder-owned post-commit backup directories."""

    parent = _opencode_receipt_path(state_home).parent
    _assert_no_symlink_components(parent, "opencode state")
    if not parent.is_dir():
        return
    for backup in sorted(parent.glob(f".{OPENCODE_ARTIFACT_DIRECTORY}.old-*")):
        if backup.is_symlink() or not backup.is_dir():
            continue
        try:
            _remove_opencode_artifact(backup)
        except (InstallError, OSError):
            # Cleanup is deliberately post-commit and retryable.  A failed
            # garbage collection must never turn a live install into a
            # reported failure.
            continue


def _remove_new_opencode_state(
    receipt_directory: Path,
    existed_before: bool,
    state_home: Path,
    state_home_existed_before: bool,
) -> None:
    """Remove only an empty receipt directory created by this install."""

    if existed_before or not receipt_directory.is_dir():
        return
    try:
        if not any(receipt_directory.iterdir()):
            receipt_directory.rmdir()
    except OSError:
        pass
    if state_home_existed_before or not state_home.is_dir():
        return
    try:
        if not any(state_home.iterdir()):
            state_home.rmdir()
    except OSError:
        pass


def _artifact_matches_sources(repo_root: Path, artifact: Path) -> bool:
    """Check exact artifact inventory and bytes against the current sources."""

    expected: dict[str, bytes] = {}
    try:
        _reject_symlink_components(
            repo_root / "packages" / "expskill", "canonical package"
        )
        package_root = repo_root / "packages" / "expskill"
        platform_root = package_root / "opencode"
        for name in ("agents.json", "package.json", "README.md", "LICENSE", "index.js"):
            expected[name] = (platform_root / name).read_bytes()
        for name in ("execution-policy.js", "unslop.js"):
            expected[f"plugins/{name}"] = (platform_root / "plugins" / name).read_bytes()
        for relative, path in _provenance_sources(repo_root):
            source_relative = Path(relative)
            package_marker = Path("packages") / "expskill"
            if source_relative.parts[:2] == package_marker.parts:
                within = Path(*source_relative.parts[2:])
                if within.parts and within.parts[0] in {"skills", "scripts"}:
                    expected[within.as_posix()] = path.read_bytes()
                elif within == Path("assets/execution-policy.json"):
                    expected[within.as_posix()] = path.read_bytes()
                elif within.parts[:2] == ("third-party", "licenses"):
                    expected[within.as_posix()] = path.read_bytes()
                elif within.parts[:2] == ("opencode", "plugins"):
                    expected[Path(*within.parts[1:]).as_posix()] = path.read_bytes()
            elif source_relative.parts[:1] == ("scripts",):
                # Builder and renderer scripts are provenance inputs only.
                continue
        rendered = _render_opencode_all(repo_root)
        expected.update({relative: contents.encode("utf-8") for relative, contents in rendered.items()})
        provenance_payload = json.loads((artifact / "provenance.json").read_text(encoding="utf-8"))
        inputs = provenance_payload.get("inputs")
        expected_inputs = [
            {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for relative, path in _provenance_sources(repo_root)
        ]
        if (
            provenance_payload.get("schema_version") != "opencode-provenance.v1"
            or inputs != expected_inputs
        ):
            return False
        expected["provenance.json"] = (
            json.dumps(
                {"schema_version": "opencode-provenance.v1", "inputs": expected_inputs},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        actual: dict[str, bytes] = {}
        actual_entries: set[str] = set()
        for path in artifact.rglob("*"):
            if path.is_symlink() or (not path.is_file() and not path.is_dir()):
                return False
            actual_entries.add(path.relative_to(artifact).as_posix())
            if path.is_file():
                actual[path.relative_to(artifact).as_posix()] = path.read_bytes()
        expected_entries: set[str] = set(expected)
        for relative in expected:
            parent = Path(relative).parent
            while parent != Path("."):
                expected_entries.add(parent.as_posix())
                parent = parent.parent
        return actual == expected and actual_entries == expected_entries
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, RuntimeError):
        return False


def _receipt_owns_artifact(receipt_path: Path, artifact: Path) -> bool:
    if not _lexists(receipt_path) or receipt_path.is_symlink() or not receipt_path.is_file():
        return False
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        recorded = payload.get("artifact_root")
        if not isinstance(recorded, str):
            return False
        return _lexical_absolute(Path(recorded).expanduser()) == _lexical_absolute(artifact)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return False


def _validate_receipt_artifact(receipt: _Receipt, state_home: Path) -> None:
    if receipt.artifact_root is not None:
        _fixed_opencode_artifact(state_home, receipt.artifact_root)


def _restore_opencode_artifact(artifact: Path, backup: Path | None) -> None:
    if backup is None:
        return
    if _lexists(artifact):
        _remove_opencode_artifact(artifact)
    try:
        backup.replace(artifact)
    except OSError as error:
        raise InstallError(f"cannot restore opencode artifact: {artifact}: {error}") from error


def _ensure_opencode_artifact(
    repo_root: Path, state_home: Path
) -> tuple[Path, bool, Path | None]:
    """Build a fresh candidate and atomically publish receipt-owned state."""

    artifact = _fixed_opencode_artifact(state_home)
    receipt_path = _opencode_receipt_path(state_home)
    _garbage_collect_opencode_backups(state_home)
    existing = _lexists(artifact)
    if existing and not _receipt_owns_artifact(receipt_path, artifact):
        raise InstallError(f"opencode artifact is stale and not receipt-owned: {artifact}")
    parent_existed = artifact.parent.exists()
    try:
        artifact.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise InstallError(f"cannot create OpenCode artifact directory: {error}") from error
    candidate = artifact.parent / f".{artifact.name}.next-{uuid.uuid4().hex}"
    backup: Path | None = None
    if _lexists(artifact):
        if artifact.is_symlink() or not artifact.is_dir():
            raise InstallError(f"opencode artifact is not a regular directory: {artifact}")
    try:
        build_opencode_package(repo_root, candidate)
        if not _artifact_matches_sources(repo_root, candidate):
            raise InstallError("fresh OpenCode artifact failed exact inventory or byte validation")
        if _lexists(artifact):
            # Re-check ownership immediately before replacing the old artifact.
            if not _receipt_owns_artifact(receipt_path, artifact):
                raise InstallError(f"opencode artifact ownership changed: {artifact}")
            backup = artifact.parent / f".{artifact.name}.old-{uuid.uuid4().hex}"
            artifact.replace(backup)
            try:
                candidate.replace(artifact)
            except OSError:
                if _lexists(backup) and not _lexists(artifact):
                    backup.replace(artifact)
                raise
            return artifact, False, backup
        candidate.replace(artifact)
        return artifact, True, None
    except (OpencodeBuildError, OSError, InstallError) as error:
        if _lexists(candidate):
            try:
                _remove_opencode_artifact(candidate)
            except InstallError:
                pass
        if backup is not None and _lexists(backup) and not _lexists(artifact):
            try:
                backup.replace(artifact)
            except OSError as restore_error:
                raise InstallError(f"cannot restore OpenCode artifact: {restore_error}") from error
        if not parent_existed:
            try:
                artifact.parent.rmdir()
            except OSError:
                pass
        if isinstance(error, InstallError):
            raise
        raise InstallError(f"cannot build OpenCode artifact: {error}") from error


def _require_opencode_source(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise InstallError(f"opencode source must not be a symlink: {path}")
    if not path.exists():
        raise InstallError(f"opencode source is missing: {label}: {path}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"opencode source cannot be resolved: {path}: {error}") from error
    return resolved


def _artifact_entries(artifact_root: Path, relative: str, label: str) -> tuple[Path, ...]:
    directory = artifact_root / relative
    if directory.is_symlink() or not directory.is_dir():
        raise InstallError(f"opencode artifact {label} directory is missing: {directory}")
    entries: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file() and not path.is_dir():
            raise InstallError(f"opencode artifact {label} entry is unsafe: {path}")
        entries.append(path)
    if not entries:
        raise InstallError(f"opencode artifact {label} inventory is empty: {directory}")
    return tuple(entries)


def _opencode_expected_links(
    repo_root: Path,
    config_dir: Path,
    artifact_root: Path,
) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    canonical_config = _canonical_opencode_config(config_dir)
    artifact_path = Path(artifact_root).expanduser()
    _assert_no_symlink_components(artifact_path, "opencode artifact")
    if artifact_path.is_symlink():
        raise InstallError(f"opencode artifact must not be a symlink: {artifact_path}")
    artifact_root = artifact_path.resolve(strict=True)
    if not artifact_root.is_dir():
        raise InstallError(f"opencode artifact is not a regular directory: {artifact_root}")
    links: list[ProfileLink] = []
    skills = _artifact_entries(artifact_root, "skills", "shared skills")
    for skill in skills:
        name = skill.name
        source = _require_opencode_source(skill, f"shared skill {name!r}")
        if not source.is_dir():
            raise InstallError(f"shared skill source is not a directory: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "skills" / name))
    commands = _artifact_entries(artifact_root, "commands", "commands")
    for path in commands:
        if path.suffix != ".md":
            raise InstallError(f"opencode command source must be Markdown: {path}")
        name = path.stem
        source = _require_opencode_source(path, f"command {name!r}")
        if not source.is_file():
            raise InstallError(f"command source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "commands" / f"{name}.md"))
    agents = _artifact_entries(artifact_root, "agents", "agents")
    for path in agents:
        if path.suffix != ".md":
            raise InstallError(f"opencode agent source must be Markdown: {path}")
        name = path.stem
        source = _require_opencode_source(path, f"agent {name!r}")
        if not source.is_file():
            raise InstallError(f"agent source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "agents" / f"{name}.md"))
    plugins = _artifact_entries(artifact_root, "plugins", "plugins")
    for path in plugins:
        name = path.name
        source = _require_opencode_source(path, f"plugin {name!r}")
        if not source.is_file():
            raise InstallError(f"plugin source is not a regular file: {source}")
        links.append(ProfileLink(source=source, destination=canonical_config / "plugins" / name))
    for link in links:
        _validate_opencode_destination(link.destination, canonical_config)
    return tuple(links)


def preflight_opencode_links(
    repo_root: Path,
    config_dir: Path,
    state_home: Path | None = None,
    *,
    artifact_root: Path | None = None,
    legacy_receipt: _Receipt | None = None,
) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    _validate_repository(canonical_root)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if artifact_root is None:
        # Preflight is read-only.  Even when a state home is supplied, build
        # into a disposable artifact rather than adopting or replacing state.
        temporary = tempfile.TemporaryDirectory(prefix="expskill-opencode-preflight-")
        artifact_root = Path(temporary.name) / "artifact"
        try:
            build_opencode_package(canonical_root, artifact_root)
        except (OpencodeBuildError, OSError) as error:
            temporary.cleanup()
            raise InstallError(f"cannot build OpenCode artifact: {error}") from error
    try:
        links = _opencode_expected_links(canonical_root, config_dir, artifact_root)
        if temporary is not None:
            # The temporary artifact is validation-only.  Remap every source
            # to the fixed receipt-owned path before conflict checks and before
            # returning links to callers, so dry-run output is stable.
            future_artifact = _fixed_opencode_artifact(
                _default_state_home() if state_home is None else state_home
            )
            remapped: list[ProfileLink] = []
            for link in links:
                relative = link.source.relative_to(artifact_root)
                remapped.append(
                    ProfileLink(source=future_artifact / relative, destination=link.destination)
                )
            links = tuple(remapped)
    finally:
        if temporary is not None:
            temporary.cleanup()
    for link in links:
        if not _lexists(link.destination):
            continue
        if not _same_owned_link(link.destination, link.source):
            if legacy_receipt is not None and legacy_receipt.artifact_root is None:
                legacy = next(
                    (
                        item
                        for item in legacy_receipt.links
                        if item.destination == link.destination
                    ),
                    None,
                )
                if legacy is not None and _same_recorded_link(
                    link.destination, legacy.source
                ):
                    continue
            raise InstallError(f"refusing conflicting opencode destination: {link.destination}")
    return links


def install_opencode(
    repo_root: Path,
    config_dir: Path,
    state_home: Path,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    # Semantic repository validation is intentionally before artifact/state
    # creation, link preflight, or any receipt mutation.
    _validate_repository(canonical_root)
    # Validate the lexical config path before materializing receipt-owned
    # state; resolving a symlinked ancestor here would redirect every output.
    _canonical_opencode_config(config_dir)
    receipt_path_value = _opencode_receipt_path(state_home)
    receipt_directory = receipt_path_value.parent
    receipt_directory_existed = receipt_directory.exists()
    canonical_state_home = receipt_directory.parent
    state_home_existed = canonical_state_home.exists()
    receipt: _Receipt | None = None
    if _lexists(receipt_path_value):
        receipt = _read_opencode_receipt(
            receipt_path_value, canonical_root, config_dir, state_home
        )
        if receipt is None:
            raise InstallError(f"receipt disappeared while reading: {receipt_path_value}")
        _validate_receipt_artifact(receipt, state_home)
    try:
        artifact_root, artifact_created, artifact_backup = _ensure_opencode_artifact(
            canonical_root, state_home
        )
    except Exception:
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        raise
    try:
        links = preflight_opencode_links(
            canonical_root,
            config_dir,
            artifact_root=artifact_root,
            legacy_receipt=receipt,
        )
    except Exception:
        if artifact_created:
            _remove_opencode_artifact(artifact_root)
        elif artifact_backup is not None:
            _restore_opencode_artifact(artifact_root, artifact_backup)
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        raise
    created_links: list[ProfileLink] = []
    migrated_links: list[ProfileLink] = []
    removed_retired: tuple[ProfileLink, ...] = ()
    try:
        if receipt is not None and receipt.artifact_root is None:
            for old in receipt.links:
                if not _lexists(old.destination):
                    continue
                if not _same_recorded_link(old.destination, old.source):
                    raise InstallError(
                        f"refusing to migrate retargeted legacy link: {old.destination}"
                    )
                old.destination.unlink()
                migrated_links.append(old)
        _create_links(links, created_links)
        if receipt is not None:
            _retained, removed_retired = _prune_opencode_retired_links(
                receipt, links, config_dir
            )
        merged_receipt = _Receipt(
            repository_root=canonical_root,
            links=tuple(links),
            marketplace_added=False,
            plugin_installed=True,
            artifact_root=artifact_root,
        )
        _write_receipt(receipt_path_value, merged_receipt)
    except Exception as error:
        for failure in _rollback_links(created_links):
            error = InstallError(f"{error}; residual state or rollback failures: {failure}")
        for failure in _restore_opencode_links(removed_retired, config_dir):
            error = InstallError(f"{error}; retired-link rollback: {failure}")
        for failure in _restore_opencode_links(migrated_links, config_dir):
            error = InstallError(f"{error}; legacy-link rollback: {failure}")
        if isinstance(error, InstallError):
            if artifact_created:
                try:
                    _remove_opencode_artifact(artifact_root)
                except InstallError as cleanup_error:
                    error = InstallError(f"{error}; {cleanup_error}")
            elif artifact_backup is not None:
                try:
                    _restore_opencode_artifact(artifact_root, artifact_backup)
                except InstallError as cleanup_error:
                    error = InstallError(f"{error}; {cleanup_error}")
            _remove_new_opencode_state(
                receipt_directory,
                receipt_directory_existed,
                canonical_state_home,
                state_home_existed,
            )
            raise error
        if artifact_created:
            try:
                _remove_opencode_artifact(artifact_root)
            except InstallError as cleanup_error:
                error = InstallError(f"{error}; {cleanup_error}")
        elif artifact_backup is not None:
            try:
                _restore_opencode_artifact(artifact_root, artifact_backup)
            except InstallError as cleanup_error:
                error = InstallError(f"{error}; {cleanup_error}")
        _remove_new_opencode_state(
            receipt_directory,
            receipt_directory_existed,
            canonical_state_home,
            state_home_existed,
        )
        raise InstallError(str(error)) from error
    if artifact_backup is not None:
        # Commit is complete once links and receipt point at the new artifact.
        # Garbage collection is post-commit and safely retried on a later run.
        try:
            _remove_opencode_artifact(artifact_backup)
        except (InstallError, OSError):
            pass
    return InstallResult(
        links=links,
        created_links=tuple(created_links),
        removed_links=removed_retired,
        marketplace_added=False,
        plugin_installed=True,
    )


def _opencode_receipt_links_without_artifact(
    receipt_path: Path,
    config_dir: Path,
    state_home: Path,
    repository_root: Path | None = None,
) -> tuple[ProfileLink, ...]:
    """Reconstruct a constrained receipt allow-list when state was removed."""

    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("links"), list):
        raise InstallError(f"receipt links are malformed: {receipt_path}")
    artifact = _fixed_opencode_artifact(state_home)
    artifact_value = payload.get("artifact_root")
    if artifact_value is not None:
        if not isinstance(artifact_value, str):
            raise InstallError(f"receipt artifact root is malformed: {receipt_path}")
        recorded_artifact = Path(artifact_value).expanduser()
        if not recorded_artifact.is_absolute() or _has_dot_components(recorded_artifact):
            raise InstallError(f"receipt artifact root must be an absolute path: {receipt_path}")
        _fixed_opencode_artifact(state_home, recorded_artifact)
    canonical_config = _canonical_opencode_config(config_dir)
    if repository_root is not None:
        try:
            allowed_skills = set(_skill_inventory(repository_root))
        except Exception as error:
            raise InstallError(f"canonical skill inventory cannot be read: {error}") from error
    else:
        allowed_skills = set(LEGACY_OPENCODE_SKILLS)
    allowed_agents = set(_OPENCODE_AGENT_NAMES)
    allowed_plugins = set(LEGACY_OPENCODE_PLUGINS)
    expected: list[ProfileLink] = []
    seen_destinations: set[Path] = set()
    for entry in payload["links"]:
        if not isinstance(entry, dict):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source_value = entry.get("source")
        destination_value = entry.get("destination")
        if not isinstance(source_value, str) or not isinstance(destination_value, str):
            raise InstallError(f"receipt links are malformed: {receipt_path}")
        source = Path(source_value).expanduser()
        destination = Path(destination_value).expanduser()
        if not source.is_absolute() or not destination.is_absolute():
            raise InstallError(f"receipt links must be absolute paths: {receipt_path}")
        source = _lexical_absolute(source)
        destination = _lexical_absolute(destination)
        if not _path_is_within(source, artifact):
            raise InstallError(f"receipt artifact source is outside owned state: {receipt_path}")
        if not _path_is_within(destination, canonical_config):
            raise InstallError(f"receipt destination is outside OpenCode config: {receipt_path}")
        try:
            source_relative = source.relative_to(artifact)
            destination_relative = destination.relative_to(canonical_config)
        except ValueError as error:
            raise InstallError(f"receipt link is outside fixed OpenCode layouts: {receipt_path}") from error
        if len(source_relative.parts) != 2 or len(destination_relative.parts) != 2:
            raise InstallError(f"receipt link has an invalid OpenCode layout: {receipt_path}")
        source_group, source_name = source_relative.parts
        destination_group, destination_name = destination_relative.parts
        if source_group == "skills":
            valid = source_name in allowed_skills and destination_group == "skills" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_dir()):
                valid = False
        elif source_group == "commands":
            valid = source_name.endswith(".md") and source_name[:-3] in allowed_skills and destination_group == "commands" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        elif source_group == "agents":
            valid = source_name.endswith(".md") and source_name[:-3] in allowed_agents and destination_group == "agents" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        elif source_group == "plugins":
            valid = source_name in allowed_plugins and destination_group == "plugins" and destination_name == source_name
            if valid and _lexists(source) and (source.is_symlink() or not source.is_file()):
                valid = False
        else:
            valid = False
        if not valid:
            raise InstallError(f"receipt link is outside fixed OpenCode layouts: {receipt_path}")
        if destination in seen_destinations:
            raise InstallError(f"receipt link is duplicated: {receipt_path}")
        seen_destinations.add(destination)
        _validate_opencode_destination(destination, canonical_config)
        expected.append(ProfileLink(source=source, destination=destination))
    return tuple(expected)


def _read_opencode_receipt(
    receipt_path: Path,
    repository_root: Path,
    config_dir: Path,
    state_home: Path,
) -> _Receipt | None:
    """Read a current artifact receipt or a strictly constrained legacy one."""

    if not _lexists(receipt_path):
        return None
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallError(f"receipt is malformed: {receipt_path}: {error}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"receipt is malformed: {receipt_path}")
    if "artifact_root" in payload:
        expected = _opencode_receipt_links_without_artifact(
            receipt_path, config_dir, state_home, repository_root
        )
    else:
        expected = _legacy_opencode_expected_links(repository_root, config_dir)
    return _read_receipt(receipt_path, repository_root, expected)


def _prune_opencode_retired_links(
    receipt: _Receipt,
    current_links: Sequence[ProfileLink],
    config_dir: Path,
) -> tuple[tuple[ProfileLink, ...], tuple[ProfileLink, ...]]:
    """Drop receipt links no longer emitted by the rebuilt artifact."""

    current_destinations = {link.destination for link in current_links}
    retained: list[ProfileLink] = []
    removed: list[ProfileLink] = []
    for link in receipt.links:
        if link.destination in current_destinations:
            retained.append(link)
            continue
        if not _lexists(link.destination) or not _same_recorded_link(
            link.destination, link.source
        ):
            # Missing and retargeted destinations are not ours to remove.
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            for restored in _restore_opencode_links(removed, config_dir):
                error = InstallError(f"{error}; retired-link rollback: {restored}")
            raise InstallError(
                f"cannot remove retired OpenCode link: {link.destination}: {error}"
            ) from error
        removed.append(link)
    return tuple(retained), tuple(removed)


def _restore_opencode_links(links: Sequence[ProfileLink], config_dir: Path) -> list[str]:
    failures: list[str] = []
    for link in reversed(tuple(links)):
        if _lexists(link.destination):
            continue
        try:
            _validate_opencode_destination(link.destination, config_dir)
            link.destination.parent.mkdir(parents=True, exist_ok=True)
            link.destination.symlink_to(link.source)
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
        except InstallError as error:
            failures.append(str(error))
    return failures


def uninstall_opencode(
    repo_root: Path,
    config_dir: Path,
    state_home: Path,
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    receipt_path_value = _opencode_receipt_path(state_home)
    if not _lexists(receipt_path_value):
        return InstallResult()
    receipt = _read_opencode_receipt(
        receipt_path_value, canonical_root, config_dir, state_home
    )
    if receipt is None:
        return InstallResult()
    links = receipt.links
    _validate_receipt_artifact(receipt, state_home)
    current = receipt
    removed: list[ProfileLink] = []
    failures: list[str] = []
    for link in receipt.links:
        if not _lexists(link.destination):
            current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
            continue
        if not _same_recorded_link(link.destination, link.source):
            current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
            continue
        try:
            if link.destination.is_dir() and not link.destination.is_symlink():
                failures.append(f"link {link.destination} is a real directory")
                continue
            link.destination.unlink()
        except OSError as error:
            failures.append(f"link {link.destination}: {error}")
            continue
        removed.append(link)
        current = _persist_receipt(receipt_path_value, current, links=tuple(item for item in current.links if item != link))
    if failures:
        raise InstallError("owned opencode link cleanup failed: " + "; ".join(failures))
    if current.links:
        raise InstallError("owned opencode link cleanup did not converge")
    if current.artifact_root is not None:
        artifact = _fixed_opencode_artifact(state_home, current.artifact_root)
        _remove_opencode_artifact(artifact)
    if receipt_path_value.is_symlink() or not receipt_path_value.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path_value}")
    try:
        receipt_path_value.unlink()
    except OSError as error:
        raise InstallError(f"cannot remove receipt: {receipt_path_value}: {error}") from error
    return InstallResult(
        links=links,
        removed_links=tuple(removed),
        marketplace_added=False,
        plugin_installed=True,
    )


def _print_opencode_dry_run(
    repo_root: Path, config_dir: Path, state_home: Path | None = None
) -> None:
    receipt_state_home = _default_state_home() if state_home is None else state_home
    # A dry run must not adopt or rebuild receipt-owned state.  Build the
    # planned artifact in the preflight temporary directory instead.
    links = preflight_opencode_links(repo_root, config_dir, receipt_state_home)
    for link in links:
        print(f"link {link.destination} -> {link.source}")
    print(f"opencode {OPENCODE_PACKAGE_NAME} receipt {_opencode_receipt_path(receipt_state_home)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the expskill marketplace and profiles.")
    parser.add_argument("--target", choices=("codex", "opencode"), default="codex")
    parser.add_argument(
        "--agents-only",
        action="store_true",
        help="codex target only: link agent profiles without touching plugin CLI state",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    arguments = parser.parse_args(argv)
    repository_root = Path(__file__).absolute().parents[1]
    state_home = _default_state_home()
    try:
        if arguments.target == "opencode":
            config_dir = _default_opencode_config_dir()
            if arguments.dry_run:
                _print_opencode_dry_run(repository_root, config_dir, state_home)
            elif arguments.uninstall:
                uninstall_opencode(repository_root, config_dir, state_home)
            else:
                install_opencode(repository_root, config_dir, state_home)
            return 0
        codex_home = _default_codex_home()
        if arguments.agents_only and arguments.target != "codex":
            print("install error: --agents-only applies to the codex target only", file=sys.stderr)
            return 1
        if arguments.dry_run:
            _print_dry_run(repository_root, codex_home, arguments.agents_only)
        elif arguments.uninstall:
            uninstall(
                repository_root, codex_home, state_home, _subprocess_runner, arguments.agents_only
            )
        else:
            install(
                repository_root, codex_home, state_home, _subprocess_runner, arguments.agents_only
            )
    except InstallError as error:
        print(f"install error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
