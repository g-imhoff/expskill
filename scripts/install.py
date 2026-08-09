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


MARKETPLACE_NAME = "codex-dev-flow"
PLUGIN_SELECTOR = "codex-dev-flow@codex-dev-flow"
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


def _profile_sources(repository_root: Path) -> tuple[Path, ...]:
    agents_root = repository_root / "plugins" / "codex-dev-flow" / "assets" / "agents"
    if not agents_root.is_dir() or agents_root.is_symlink():
        raise InstallError(f"agent source directory is missing: {agents_root}")
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


def _same_owned_link(destination: Path, source: Path) -> bool:
    if not destination.is_symlink():
        return False
    try:
        return destination.resolve(strict=False) == source.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def preflight_links(repo_root: Path, codex_home: Path) -> tuple[ProfileLink, ...]:
    canonical_root = _canonical_repository_root(repo_root)
    sources = _profile_sources(canonical_root)
    canonical_codex_home = Path(codex_home).expanduser().resolve(strict=False)
    agents_directory = canonical_codex_home / "agents"
    _validate_agent_directory(agents_directory)
    links = tuple(
        ProfileLink(source=source, destination=agents_directory / source.name)
        for source in sources
    )
    for link in links:
        if not _lexists(link.destination):
            continue
        if not _same_owned_link(link.destination, link.source):
            raise InstallError(f"refusing conflicting agent destination: {link.destination}")
    return links


def _receipt_path(state_home: Path) -> Path:
    canonical_state_home = Path(state_home).expanduser().resolve(strict=False)
    return canonical_state_home / RECEIPT_DIRECTORY / RECEIPT_FILENAME


def _receipt_links(value: object, receipt_path: Path, repository_root: Path) -> tuple[ProfileLink, ...]:
    if not isinstance(value, list):
        raise InstallError(f"receipt links are malformed: {receipt_path}")
    links: list[ProfileLink] = []
    destinations: set[Path] = set()
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
        try:
            canonical_source = source.resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise InstallError(f"receipt source cannot be resolved: {source}") from error
        try:
            canonical_source.relative_to(repository_root)
        except ValueError as error:
            raise InstallError(f"receipt source is outside the repository: {source}") from error
        if destination in destinations:
            raise InstallError(f"receipt destination is duplicated: {destination}")
        destinations.add(destination)
        links.append(ProfileLink(source=canonical_source, destination=destination))
    return tuple(links)


def _read_receipt(receipt_path: Path, repository_root: Path) -> _Receipt | None:
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
    links = _receipt_links(payload.get("links"), receipt_path, repository_root)
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
        raise InstallError(f"cannot write receipt: {receipt_path}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


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


def _checked_json(
    run: Runner | Callable[[Sequence[str]], object], command: list[str]
) -> dict[str, Any]:
    result = _invoke_runner(run, command)
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        suffix = f": {details}" if details else ""
        raise InstallError(
            f"command failed with exit code {result.returncode}: {' '.join(command)}{suffix}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise InstallError(f"command returned invalid JSON: {' '.join(command)}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise InstallError(f"command returned non-object JSON: {' '.join(command)}")
    return payload


def _marketplace_is_new(payload: Mapping[str, Any]) -> bool:
    marker = payload.get("alreadyAdded")
    if isinstance(marker, bool):
        return not marker
    marker = payload.get("already_added")
    if isinstance(marker, bool):
        return not marker
    raise InstallError("marketplace add JSON did not report alreadyAdded")


def _plugin_is_new(payload: Mapping[str, Any], receipt_exists: bool) -> bool:
    marker = payload.get("alreadyInstalled")
    if isinstance(marker, bool):
        return not marker
    marker = payload.get("already_installed")
    if isinstance(marker, bool):
        return not marker
    installed = payload.get("installed")
    if isinstance(installed, bool):
        return installed and not receipt_exists
    return False


def _create_links(links: tuple[ProfileLink, ...]) -> tuple[ProfileLink, ...]:
    created: list[ProfileLink] = []
    if links:
        try:
            links[0].destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise InstallError(f"cannot create agent destination directory: {links[0].destination.parent}: {error}") from error
    try:
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
    except Exception:
        _rollback_links(created)
        raise
    return tuple(created)


def _rollback_links(links: Sequence[ProfileLink]) -> None:
    for link in reversed(tuple(links)):
        if not _same_owned_link(link.destination, link.source):
            continue
        try:
            link.destination.unlink()
        except OSError:
            continue


def _remove_external_state(
    run: Runner | Callable[[Sequence[str]], object], plugin: bool, marketplace: bool
) -> None:
    if plugin:
        _checked_json(
            run,
            ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"],
        )
    if marketplace:
        _checked_json(
            run,
            ["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"],
        )


def _cleanup_after_install_failure(
    run: Runner | Callable[[Sequence[str]], object],
    created_links: Sequence[ProfileLink],
    plugin_new: bool,
    marketplace_new: bool,
) -> None:
    _rollback_links(created_links)
    try:
        _remove_external_state(run, plugin_new, marketplace_new)
    except InstallError:
        pass


def install(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root)
    links = preflight_links(canonical_root, codex_home)
    receipt_path = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path, canonical_root)
    state_directory_existed = _lexists(receipt_path.parent)
    created_links: tuple[ProfileLink, ...] = ()
    marketplace_new = False
    plugin_new = False
    try:
        created_links = _create_links(links)
        marketplace_payload = _checked_json(
            run,
            [
                "codex",
                "plugin",
                "marketplace",
                "add",
                str(canonical_root),
                "--json",
            ],
        )
        marketplace_new = _marketplace_is_new(marketplace_payload)
        plugin_payload = _checked_json(
            run,
            ["codex", "plugin", "add", PLUGIN_SELECTOR, "--json"],
        )
        plugin_new = _plugin_is_new(plugin_payload, receipt is not None)
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
        _write_receipt(receipt_path, merged_receipt)
    except Exception as error:
        _cleanup_after_install_failure(run, created_links, plugin_new, marketplace_new)
        if not state_directory_existed and _lexists(receipt_path.parent):
            try:
                receipt_path.parent.rmdir()
            except OSError:
                pass
        if isinstance(error, InstallError):
            raise
        raise InstallError(str(error)) from error
    return InstallResult(
        links=links,
        created_links=created_links,
        marketplace_added=marketplace_new,
        plugin_installed=plugin_new,
    )


def _marketplace_matches(repository_root: Path, payload: Mapping[str, Any]) -> bool:
    marketplaces = payload.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallError("marketplace list JSON did not contain marketplaces")
    for marketplace in marketplaces:
        if not isinstance(marketplace, dict) or marketplace.get("name") != MARKETPLACE_NAME:
            continue
        sources: list[object] = [marketplace.get("root")]
        marketplace_source = marketplace.get("marketplaceSource")
        if isinstance(marketplace_source, dict):
            sources.append(marketplace_source.get("source"))
        for source in sources:
            if not isinstance(source, str):
                continue
            try:
                if Path(source).expanduser().resolve(strict=False) == repository_root:
                    return True
            except (OSError, RuntimeError):
                continue
    return False


def _remove_owned_links(links: Sequence[ProfileLink]) -> tuple[ProfileLink, ...]:
    removed: list[ProfileLink] = []
    for link in links:
        if not _same_owned_link(link.destination, link.source):
            continue
        try:
            link.destination.unlink()
        except OSError as error:
            raise InstallError(f"cannot remove owned agent link: {link.destination}: {error}") from error
        removed.append(link)
    return tuple(removed)


def uninstall(
    repo_root: Path,
    codex_home: Path,
    state_home: Path,
    run: Runner | Callable[[Sequence[str]], object],
) -> InstallResult:
    canonical_root = _canonical_repository_root(repo_root, require_directory=False)
    receipt_path = _receipt_path(state_home)
    receipt = _read_receipt(receipt_path, canonical_root)
    if receipt is None:
        return InstallResult()
    marketplace_matches = False
    if receipt.marketplace_added:
        marketplace_payload = _checked_json(
            run,
            ["codex", "plugin", "marketplace", "list", "--json"],
        )
        marketplace_matches = _marketplace_matches(canonical_root, marketplace_payload)
    if receipt.plugin_installed:
        _checked_json(
            run,
            ["codex", "plugin", "remove", PLUGIN_SELECTOR, "--json"],
        )
    if marketplace_matches:
        _checked_json(
            run,
            ["codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME, "--json"],
        )
    removed_links = _remove_owned_links(receipt.links)
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise InstallError(f"receipt path is not a regular file: {receipt_path}")
    try:
        receipt_path.unlink()
    except OSError as error:
        raise InstallError(f"cannot remove receipt: {receipt_path}: {error}") from error
    return InstallResult(
        links=receipt.links,
        removed_links=removed_links,
        marketplace_added=marketplace_matches,
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
    print(f"codex plugin marketplace add {links[0].source.parents[4]} --json")
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
