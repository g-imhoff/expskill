from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

try:
    from scripts.validate import validate_repository
except ModuleNotFoundError:
    from validate import validate_repository


MARKETPLACE_NAME = "codex-dev-flow"
PLUGIN_NAME = "codex-dev-flow"
PLUGIN_SELECTOR = "codex-dev-flow@codex-dev-flow"
PLUGIN_VERSION = "0.1.0"
PROFILE_NAMES = (
    "devflow-explorer",
    "devflow-implementer",
    "devflow-reviewer",
    "devflow-test-engineer",
    "devflow-verifier",
)
RECEIPT_DIRECTORY = "codex-dev-flow"
RECEIPT_FILENAME = "install.json"


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


@dataclass(frozen=True)
class _CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _canonical_repository_root(repo_root: Path, require_directory: bool = True) -> Path:
    candidate = Path(repo_root).expanduser()
    try:
        canonical = candidate.resolve(strict=require_directory)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"repository root cannot be resolved: {candidate}: {error}") from error
    if require_directory and not canonical.is_dir():
        raise InstallError(f"repository root is not a directory: {canonical}")
    return canonical


def _validate_repository(repository_root: Path) -> None:
    errors = validate_repository(repository_root)
    if errors:
        raise InstallError("repository validation failed: " + "; ".join(errors))


def _assert_no_symlink_components(root: Path, relative: Sequence[str]) -> None:
    current = root
    for component in relative:
        current = current / component
        if current.is_symlink():
            raise InstallError(f"repository profile path contains a symlink: {current}")


def _profile_sources(repository_root: Path) -> tuple[Path, ...]:
    _assert_no_symlink_components(
        repository_root,
        ("plugins", PLUGIN_NAME, "assets", "agents"),
    )
    agents_root = repository_root / "plugins" / PLUGIN_NAME / "assets" / "agents"
    if not agents_root.is_dir():
        raise InstallError(f"agent source directory is missing: {agents_root}")
    try:
        resolved_agents_root = agents_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise InstallError(f"agent source directory cannot be resolved: {agents_root}: {error}") from error
    if resolved_agents_root != agents_root:
        raise InstallError(f"agent source directory resolves outside the repository: {agents_root}")
    discovered = tuple(sorted(agents_root.glob("devflow-*.toml"), key=lambda path: path.name))
    expected_names = {f"{name}.toml" for name in PROFILE_NAMES}
    discovered_names = {path.name for path in discovered}
    if discovered_names != expected_names or len(discovered) != len(expected_names):
        found = ", ".join(sorted(discovered_names)) or "none"
        expected = ", ".join(sorted(expected_names))
        raise InstallError(
            f"agent sources must be exactly the five validated profiles; found {found}; expected {expected}"
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
    source_directory = canonical_root / "plugins" / PLUGIN_NAME / "assets" / "agents"
    return tuple(
        ProfileLink(
            source=_lexical_absolute(source_directory / f"{name}.toml"),
            destination=agents_directory / f"{name}.toml",
        )
        for name in PROFILE_NAMES
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
    links = _receipt_links(payload.get("links"), receipt_path, expected_links)
    return _Receipt(
        repository_root=recorded_root,
        links=links,
        marketplace_added=marketplace_added,
        plugin_installed=plugin_installed,
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


def _validate_plugin_add(payload: Mapping[str, Any]) -> None:
    if payload.get("pluginId") != PLUGIN_SELECTOR:
        raise InstallError("plugin add JSON identified the wrong plugin")
    if payload.get("name") != PLUGIN_NAME:
        raise InstallError("plugin add JSON identified the wrong plugin name")
    if payload.get("marketplaceName") != MARKETPLACE_NAME:
        raise InstallError("plugin add JSON identified the wrong marketplace")
    if payload.get("version") != PLUGIN_VERSION:
        raise InstallError("plugin add JSON identified the wrong version")
    if not isinstance(payload.get("installedPath"), str) or not payload["installedPath"]:
        raise InstallError("plugin add JSON did not report an installed path")


def _create_links(links: Sequence[ProfileLink], created: list[ProfileLink]) -> None:
    if links:
        try:
            links[0].destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallError(
                f"cannot create agent destination directory: {links[0].destination.parent}: {error}"
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
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = preflight_links(canonical_root, codex_home)
    receipt_path_value = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path_value, canonical_root, links)
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
    try:
        _create_links(links, created_links)
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
        _validate_plugin_add(plugin_add_json)
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
    if receipt.plugin_installed:
        plugin_payload = _run_json(run, ["codex", "plugin", "list", "--json"])
        plugin_state = _plugin_state(plugin_payload, canonical_root)
    if receipt.marketplace_added:
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


def _print_dry_run(repo_root: Path, codex_home: Path) -> None:
    links = preflight_links(repo_root, codex_home)
    for link in links:
        print(f"link {link.destination} -> {link.source}")
    repository = links[0].source.parent.parent.parent.parent.parent
    print(f"codex plugin marketplace add {repository} --json")
    print(f"codex plugin add {PLUGIN_SELECTOR} --json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the codex-dev-flow marketplace and profiles.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    arguments = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    codex_home = _default_codex_home()
    state_home = _default_state_home()
    try:
        if arguments.dry_run:
            _print_dry_run(repository_root, codex_home)
        elif arguments.uninstall:
            uninstall(repository_root, codex_home, state_home, _subprocess_runner)
        else:
            install(repository_root, codex_home, state_home, _subprocess_runner)
    except InstallError as error:
        print(f"install error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
