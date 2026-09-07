"""Pinned CLI acquisition with fail-closed archive and cache verification.

This module is deliberately test-owned.  The installer and plugin do not need
to know how the integration suite obtains its external CLI executables.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import platform as host_platform
import secrets
import shutil
import stat
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


MANIFEST_PATH = Path(__file__).with_name("cli_release_manifest.json")
CLI_MODE_ENV = "EXPSKILL_CLI_MODE"
REQUIRED_MODE = "required"
OPTIONAL_MODE = "optional"
_VALID_MODES = frozenset({REQUIRED_MODE, OPTIONAL_MODE})
_CHUNK_SIZE = 1024 * 1024
_NETWORK_TIMEOUT = 300
_INTEGRITY_FILENAME = ".integrity.json"
_READY_FILENAME = ".ready"
_NETWORK_ERRNOS = frozenset(
    getattr(errno, name)
    for name in (
        "ECONNABORTED",
        "ECONNREFUSED",
        "ECONNRESET",
        "EHOSTDOWN",
        "EHOSTUNREACH",
        "ENETDOWN",
        "ENETUNREACH",
        "ENETRESET",
        "ETIMEDOUT",
    )
    if hasattr(errno, name)
)


class CliVerificationError(RuntimeError):
    """Raised when a CLI cannot be proven safe to use."""


class CliAcquisitionUnavailable(RuntimeError):
    """Downloader contract for failures opening or reading the remote response.

    Injected downloaders may raise this exception only for a remote acquisition
    failure.  Local destination, cache, hashing, and publication errors must
    propagate as their original exception and are always hard failures.
    """


@dataclass(frozen=True)
class CliAsset:
    kind: str
    version: str
    archive: str
    binary_name: str
    sha256: str
    url: str


def _load_manifest() -> dict[str, object]:
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CliVerificationError(f"CLI release manifest is unreadable: {MANIFEST_PATH}: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise CliVerificationError(f"CLI release manifest has unsupported schema: {MANIFEST_PATH}")
    if not isinstance(payload.get("provenance"), dict) or not payload["provenance"]:
        raise CliVerificationError(f"CLI release manifest has no provenance: {MANIFEST_PATH}")
    if not isinstance(payload.get("clis"), dict):
        raise CliVerificationError(f"CLI release manifest has no CLI entries: {MANIFEST_PATH}")
    return payload


CLI_RELEASE_MANIFEST = _load_manifest()


def platform_key(system: str | None = None, machine: str | None = None) -> tuple[str, str]:
    """Return the manifest platform key components for a host."""

    normalized_system = (host_platform.system() if system is None else system).lower()
    normalized_machine = (host_platform.machine() if machine is None else machine).lower()
    if normalized_system in {"mac", "macos", "osx"}:
        normalized_system = "darwin"
    elif normalized_system == "gnu/linux":
        normalized_system = "linux"
    if normalized_machine in {"x86_64", "amd64"}:
        normalized_machine = "x86_64"
    elif normalized_machine in {"aarch64", "arm64"}:
        normalized_machine = "arm64" if normalized_system == "darwin" else "aarch64"
    return normalized_system, normalized_machine


def _mode(value: str | None, environ: Mapping[str, str]) -> str:
    selected = value if value is not None else environ.get(CLI_MODE_ENV, REQUIRED_MODE)
    selected = selected.strip().lower()
    if selected not in _VALID_MODES:
        choices = ", ".join(sorted(_VALID_MODES))
        raise CliVerificationError(f"{CLI_MODE_ENV} must be one of {choices}, got {selected!r}")
    return selected


def _failure(
    selected_mode: str,
    message: str,
    *,
    cause: BaseException | None = None,
    skippable: bool = False,
) -> None:
    if selected_mode == OPTIONAL_MODE and skippable:
        error = unittest.SkipTest(f"optional CLI verification unavailable: {message}")
    else:
        error = CliVerificationError(message)
    if cause is not None:
        raise error from cause
    raise error


def _is_acquisition_unavailable(error: BaseException) -> bool:
    """Return whether an acquisition failure can be skipped in optional mode."""

    if isinstance(error, CliAcquisitionUnavailable):
        cause = error.__cause__
        return cause is None or _is_acquisition_unavailable(cause)
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if reason is None or isinstance(reason, str):
            return True
        return _is_acquisition_unavailable(reason)
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    return isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS


def _is_regular_file(path: Path) -> bool:
    return not path.is_symlink() and path.is_file()


def _is_executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _asset_for(kind: str, key: tuple[str, str], selected_mode: str) -> CliAsset:
    clis = CLI_RELEASE_MANIFEST.get("clis")
    if not isinstance(clis, dict):
        _failure(selected_mode, "CLI release manifest has no CLI map")
    entry = clis.get(kind)
    if not isinstance(entry, dict):
        _failure(selected_mode, f"CLI release manifest has no {kind!r} entry")
    platforms = entry.get("platforms")
    platform_name = f"{key[0]}-{key[1]}"
    if not isinstance(platforms, dict) or not platforms:
        _failure(selected_mode, f"malformed {kind} platform manifest")
    if platform_name not in platforms:
        _failure(
            selected_mode,
            f"no pinned {kind} asset supports platform {platform_name}",
            skippable=True,
        )
    raw_asset = platforms[platform_name]
    if not isinstance(raw_asset, dict):
        _failure(selected_mode, f"malformed {kind} asset manifest entry for {platform_name}")
    version = entry.get("version")
    archive = raw_asset.get("archive")
    binary_name = raw_asset.get("binary_name")
    expected = raw_asset.get("sha256")
    url = raw_asset.get("url")
    if not all(isinstance(value, str) and value for value in (version, archive, binary_name, expected, url)):
        _failure(selected_mode, f"malformed {kind} asset manifest entry for {platform_name}")
    if (
        Path(archive).is_absolute()
        or len(Path(archive).parts) != 1
        or Path(binary_name).is_absolute()
        or len(Path(binary_name).parts) != 1
        or not url.startswith("https://")
    ):
        _failure(selected_mode, f"unsafe {kind} asset manifest entry for {platform_name}")
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        _failure(selected_mode, f"malformed SHA-256 digest for {kind} {platform_name}")
    return CliAsset(
        kind=kind,
        version=version,
        archive=archive,
        binary_name=binary_name,
        sha256=expected,
        url=url,
    )


def _default_downloader(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "expskill-cli-integrity-test"})
    try:
        response = urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT)
    except Exception as error:
        raise CliAcquisitionUnavailable(f"could not open CLI response: {url}") from error
    with response:
        try:
            stream = destination.open("wb")
        except Exception as error:
            raise CliVerificationError(f"could not open downloaded CLI archive: {destination}") from error
        try:
            with stream:
                while True:
                    try:
                        chunk = response.read(_CHUNK_SIZE)
                    except Exception as error:
                        raise CliAcquisitionUnavailable(f"could not read CLI response: {url}") from error
                    if not chunk:
                        break
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        except CliAcquisitionUnavailable:
            raise
        except Exception as error:
            raise CliVerificationError(f"could not write downloaded CLI archive: {destination}") from error


def _ensure_cache_root(cache_root: Path, selected_mode: str) -> Path:
    cache_root = Path(cache_root).expanduser()
    if cache_root.is_symlink() or (cache_root.exists() and not cache_root.is_dir()):
        _failure(selected_mode, f"CLI cache root is not a directory: {cache_root}")
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        _failure(selected_mode, f"cannot create CLI cache root: {cache_root}: {error}", cause=error)
    return cache_root


def _download_archive(
    asset: CliAsset,
    cache_root: Path,
    selected_mode: str,
    downloader: Callable[[str, Path], None],
) -> Path:
    archive = cache_root / asset.archive
    if os.path.lexists(archive):
        if not _is_regular_file(archive):
            _failure(selected_mode, f"cached archive is not a regular file: {archive}")
        try:
            actual = _sha256(archive)
        except OSError as error:
            _failure(selected_mode, f"cached archive cannot be hashed: {archive}", cause=error)
        if actual != asset.sha256:
            _failure(
                selected_mode,
                f"cached {asset.kind} archive hash mismatch for {archive.name}: "
                f"expected {asset.sha256}, got {actual}",
            )
        return archive

    temporary: Path | None = None
    try:
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{asset.archive}.", suffix=".part", dir=cache_root
            )
            os.close(descriptor)
        except OSError as error:
            _failure(
                selected_mode,
                f"cannot create temporary CLI archive in {cache_root}: {error}",
                cause=error,
            )
        temporary = Path(temporary_name)
        # The downloader owns only this disposable path; the named cache file
        # is changed after both download completion and digest verification.
        try:
            downloader(asset.url, temporary)
        except CliVerificationError:
            raise
        except CliAcquisitionUnavailable as error:
            _failure(
                selected_mode,
                f"CLI download failed for {asset.url}: {error}",
                cause=error,
                skippable=_is_acquisition_unavailable(error),
            )
        except Exception as error:
            _failure(
                selected_mode,
                f"CLI download failed for {asset.url}: {error}",
                cause=error,
            )
        if not _is_regular_file(temporary):
            _failure(
                selected_mode,
                f"CLI download did not produce a regular archive: {asset.url}",
            )
        try:
            actual = _sha256(temporary)
        except OSError as error:
            _failure(selected_mode, f"downloaded CLI archive cannot be hashed: {temporary}", cause=error)
        if actual != asset.sha256:
            _failure(
                selected_mode,
                f"downloaded {asset.kind} archive hash mismatch for {asset.archive}: "
                f"expected {asset.sha256}, got {actual}",
            )
        try:
            os.replace(temporary, archive)
        except OSError as error:
            _failure(
                selected_mode,
                f"cannot publish downloaded CLI archive {archive}: {error}",
                cause=error,
            )
        temporary = None
        return archive
    finally:
        if temporary is not None:
            try:
                if temporary.is_dir() and not temporary.is_symlink():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # The original download failure remains the useful error.
                pass
    raise AssertionError("unreachable")


def _safe_member_name(name: str, expected: str) -> bool:
    path = Path(name)
    return (
        name == expected
        and not path.is_absolute()
        and len(path.parts) == 1
        and path.parts[0] not in {"", ".", ".."}
    )


def _archive_member_sha256(archive: Path, asset: CliAsset, selected_mode: str) -> str:
    try:
        archive_digest = _sha256(archive)
    except OSError as error:
        _failure(selected_mode, f"verified archive cannot be hashed: {archive}", cause=error)
    if archive_digest != asset.sha256:
        _failure(selected_mode, f"refusing to inspect archive whose hash is not pinned: {archive}")

    digest = hashlib.sha256()
    size = 0
    try:
        if asset.archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if len(entries) != 1 or not _safe_member_name(entries[0].filename, asset.binary_name):
                    raise ValueError(
                        f"expected one top-level member named {asset.binary_name!r}, "
                        f"found {[entry.filename for entry in entries]!r}"
                    )
                entry = entries[0]
                mode = (entry.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError("archive member is a symbolic link")
                if entry.is_dir():
                    raise ValueError("archive member is a directory")
                with bundle.open(entry, "r") as source:
                    while True:
                        chunk = source.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                entries = bundle.getmembers()
                if len(entries) != 1 or not _safe_member_name(entries[0].name, asset.binary_name):
                    raise ValueError(
                        f"expected one top-level member named {asset.binary_name!r}, "
                        f"found {[entry.name for entry in entries]!r}"
                    )
                member = entries[0]
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("archive member is not a regular file")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("archive member could not be read")
                with source:
                    while True:
                        chunk = source.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                        size += len(chunk)
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as error:
        _failure(selected_mode, f"unexpected {asset.kind} archive layout in {archive.name}: {error}", cause=error)
    if size == 0:
        _failure(selected_mode, f"archived {asset.kind} binary is missing or empty: {archive}")
    return digest.hexdigest()


def _extract_verified_archive(archive: Path, asset: CliAsset, destination: Path, selected_mode: str) -> Path:
    try:
        archive_digest = _sha256(archive)
    except OSError as error:
        _failure(selected_mode, f"verified archive cannot be hashed: {archive}", cause=error)
    if archive_digest != asset.sha256:
        _failure(selected_mode, f"refusing to extract archive whose hash is not pinned: {archive}")
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / asset.binary_name
    try:
        if asset.archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as bundle:
                entries = bundle.infolist()
                if len(entries) != 1 or not _safe_member_name(entries[0].filename, asset.binary_name):
                    raise ValueError(
                        f"expected one top-level member named {asset.binary_name!r}, "
                        f"found {[entry.filename for entry in entries]!r}"
                    )
                mode = (entries[0].external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise ValueError("archive member is a symbolic link")
                if entries[0].is_dir():
                    raise ValueError("archive member is a directory")
                with bundle.open(entries[0], "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=_CHUNK_SIZE)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                entries = bundle.getmembers()
                if len(entries) != 1 or not _safe_member_name(entries[0].name, asset.binary_name):
                    raise ValueError(
                        f"expected one top-level member named {asset.binary_name!r}, "
                        f"found {[entry.name for entry in entries]!r}"
                    )
                member = entries[0]
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("archive member is not a regular file")
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError("archive member could not be read")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=_CHUNK_SIZE)
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as error:
        _failure(selected_mode, f"unexpected {asset.kind} archive layout in {archive.name}: {error}", cause=error)
    if not _is_regular_file(target) or target.stat().st_size == 0:
        _failure(selected_mode, f"extracted {asset.kind} binary is missing or empty: {target}")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def _read_json(path: Path) -> dict[str, object] | None:
    if not _is_regular_file(path):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _recover_cache(cache_dir: Path) -> None:
    """Restore a cache directory if a process died during the directory swap."""

    if os.path.lexists(cache_dir):
        return
    backups = sorted(cache_dir.parent.glob(f".{cache_dir.name}.backup-*"))
    for backup in backups:
        if backup.is_dir() and not backup.is_symlink():
            try:
                os.replace(backup, cache_dir)
            except OSError as error:
                raise CliVerificationError(
                    f"could not recover cached CLI directory {cache_dir}: {error}"
                ) from error
            return


def _cache_is_valid(
    cache_dir: Path,
    asset: CliAsset,
    archive: Path | None,
    selected_mode: str,
) -> Path | None:
    if not os.path.lexists(cache_dir):
        return None
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        _failure(selected_mode, f"CLI cache directory is not a directory: {cache_dir}")
    binary = cache_dir / "bin"
    marker = cache_dir / _READY_FILENAME
    metadata_path = cache_dir / _INTEGRITY_FILENAME
    metadata_present = os.path.lexists(metadata_path)
    metadata = _read_json(metadata_path)

    if metadata_present and metadata is None:
        _failure(selected_mode, f"cached {asset.kind} integrity receipt is malformed: {metadata_path}")

    # A marker alone is deliberately not trusted.  Once an integrity receipt
    # exists, however, any mismatch is a hard failure rather than an automatic
    # repair that could hide tampering or execute an unexpected file.
    if metadata is not None:
        if not _is_regular_file(binary):
            _failure(selected_mode, f"cached {asset.kind} executable is missing or not regular: {binary}")
        if not _is_executable(binary):
            _failure(selected_mode, f"cached {asset.kind} executable is not executable: {binary}")
        if (
            metadata.get("schema") != 1
            or metadata.get("kind") != asset.kind
            or metadata.get("version") != asset.version
            or metadata.get("archive") != asset.archive
            or metadata.get("archive_sha256") != asset.sha256
            or metadata.get("binary_name") != asset.binary_name
            or metadata.get("url") != asset.url
            or not isinstance(metadata.get("binary_sha256"), str)
            or len(metadata["binary_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in metadata["binary_sha256"])
        ):
            _failure(selected_mode, f"cached {asset.kind} integrity receipt is inconsistent: {metadata_path}")
        try:
            receipt_actual = _sha256(binary)
        except OSError as error:
            _failure(selected_mode, f"cached {asset.kind} executable cannot be hashed: {binary}", cause=error)
        if receipt_actual != metadata["binary_sha256"]:
            _failure(
                selected_mode,
                f"cached {asset.kind} executable hash disagrees with its integrity receipt: {binary}",
            )
        if not _is_regular_file(marker):
            _failure(selected_mode, f"cached {asset.kind} ready marker is missing: {marker}")
        try:
            marker_value = marker.read_text(encoding="utf-8").strip()
        except OSError as error:
            _failure(selected_mode, f"cached {asset.kind} ready marker cannot be read: {marker}", cause=error)
        if marker_value != asset.version:
            _failure(
                selected_mode,
                f"cached {asset.kind} ready marker is for {marker_value!r}, expected {asset.version!r}",
            )
        # A local receipt is not enough to authorize reuse.  The archive must
        # be acquired and its pinned member digest checked before comparing
        # the cached executable bytes.
        if archive is None:
            return None
        expected_binary_sha256 = _archive_member_sha256(archive, asset, selected_mode)
        if metadata["binary_sha256"] != expected_binary_sha256:
            _failure(
                selected_mode,
                f"cached {asset.kind} receipt does not match the pinned archive member: {metadata_path}",
            )
        try:
            actual = _sha256(binary)
        except OSError as error:
            _failure(selected_mode, f"cached {asset.kind} executable cannot be hashed: {binary}", cause=error)
        if actual != expected_binary_sha256:
            _failure(
                selected_mode,
                f"cached {asset.kind} executable hash mismatch for {binary}: "
                f"expected {expected_binary_sha256}, got {actual}",
            )
        return binary

    # A stale marker or binary without a receipt is rebuilt only from an
    # archive whose official digest has already been checked.
    return None


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _install_verified_cache(
    archive: Path,
    asset: CliAsset,
    cache_root: Path,
    cache_dir: Path,
    selected_mode: str,
) -> Path:
    stage: Path | None = None
    backup: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{cache_dir.name}.stage-", dir=cache_root))
        extracted = _extract_verified_archive(archive, asset, stage / "payload", selected_mode)
        binary_sha256 = _sha256(extracted)
        metadata = {
            "archive": asset.archive,
            "archive_sha256": asset.sha256,
            "binary_name": asset.binary_name,
            "binary_sha256": binary_sha256,
            "kind": asset.kind,
            "schema": 1,
            "url": asset.url,
            "version": asset.version,
        }
        staged_cache = stage / "cache"
        staged_cache.mkdir()
        os.replace(extracted, staged_cache / "bin")
        _write_json(staged_cache / _INTEGRITY_FILENAME, metadata)
        (staged_cache / _READY_FILENAME).write_text(asset.version + "\n", encoding="utf-8")
        with (staged_cache / _READY_FILENAME).open("rb") as stream:
            os.fsync(stream.fileno())

        if os.path.lexists(cache_dir):
            if cache_dir.is_symlink() or not cache_dir.is_dir():
                _failure(selected_mode, f"CLI cache directory is not a directory: {cache_dir}")
            backup = cache_dir.parent / f".{cache_dir.name}.backup-{secrets.token_hex(8)}"
            os.replace(cache_dir, backup)
        os.replace(staged_cache, cache_dir)
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        return cache_dir / "bin"
    except CliVerificationError:
        raise
    except Exception as error:
        if backup is not None and not os.path.lexists(cache_dir):
            try:
                os.replace(backup, cache_dir)
                backup = None
            except OSError:
                pass
        _failure(
            selected_mode,
            f"could not commit verified CLI cache for {asset.kind}: {error}",
            cause=error,
        )
    finally:
        if backup is not None:
            # Keep a recoverable backup if restoring it failed.  The next call
            # can recover it, and no unverified marker is written.
            pass
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
    raise AssertionError("unreachable")


def _resolve_override_path(kind: str, value: str, environ: Mapping[str, str]) -> Path:
    supplied = Path(value).expanduser()
    if not supplied.is_absolute() and len(supplied.parts) == 1 and value == supplied.name:
        selected = shutil.which(str(supplied), path=environ.get("PATH"))
        if selected is None:
            raise CliVerificationError(
                f"explicit {kind} CLI override was not found on PATH: {value!r}"
            )
        supplied = Path(selected)
    if not supplied.is_absolute():
        supplied = Path.cwd() / supplied
    return supplied.absolute()


def _verified_override(kind: str, path: Path, environ: Mapping[str, str]) -> Path:
    digest_name = f"EXPSKILL_TEST_{kind.upper()}_BIN_SHA256"
    expected = environ.get(digest_name, "").strip().lower()
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise CliVerificationError(
            f"{digest_name} must contain the independently verified SHA-256 digest "
            "for the explicitly supplied executable"
        )
    if not _is_regular_file(path):
        raise CliVerificationError(f"explicit {kind} CLI override is not a regular file: {path}")
    if not _is_executable(path):
        raise CliVerificationError(f"explicit {kind} CLI override is not executable: {path}")
    actual = _sha256(path)
    if actual != expected:
        raise CliVerificationError(
            f"explicit {kind} CLI override hash mismatch: expected {expected}, got {actual}"
        )
    return path


def ensure_binary(
    kind: str,
    cache_root: Path,
    *,
    platform_key: tuple[str, str] | None = None,
    mode: str | None = None,
    environ: Mapping[str, str] | None = None,
    downloader: Callable[[str, Path], None] | None = None,
) -> Path:
    """Return a verified pinned CLI executable, or explicitly skip optional work.

    The required mode is fail-closed: no missing binary, unsupported platform,
    download error, hash mismatch, or archive-layout problem becomes a skip.
    Optional mode is intended for an explicitly requested local/offline test and
    may skip only acquisition failures.  An explicit executable override always
    requires its companion SHA-256 environment variable.
    """

    if kind not in {"codex", "opencode"}:
        raise CliVerificationError(f"unsupported CLI kind: {kind!r}")
    env = os.environ if environ is None else environ
    selected_mode = _mode(mode, env)
    override_name = f"EXPSKILL_TEST_{kind.upper()}_BIN"
    override = env.get(override_name)
    if override:
        return _verified_override(kind, _resolve_override_path(kind, override, env), env)

    selected_platform = platform_key if platform_key is not None else globals()["platform_key"]()
    asset = _asset_for(kind, selected_platform, selected_mode)
    root = _ensure_cache_root(Path(cache_root), selected_mode)
    cache_dir = root / f"{kind}-{asset.version}"
    _recover_cache(cache_dir)
    _cache_is_valid(cache_dir, asset, None, selected_mode)
    archive = _download_archive(asset, root, selected_mode, downloader or _default_downloader)
    cached = _cache_is_valid(cache_dir, asset, archive, selected_mode)
    if cached is not None:
        return cached
    return _install_verified_cache(archive, asset, root, cache_dir, selected_mode)


__all__ = [
    "CLI_MODE_ENV",
    "CLI_RELEASE_MANIFEST",
    "CliAcquisitionUnavailable",
    "CliAsset",
    "CliVerificationError",
    "OPTIONAL_MODE",
    "REQUIRED_MODE",
    "ensure_binary",
    "platform_key",
]
