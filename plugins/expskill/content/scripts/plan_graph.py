"""Private, route-neutral transactions for durable executable Plan Graphs.

The installed helper is resolved relative to the plugin package, not a source
checkout.  Its public API is intentionally small: ``initialize_workflow``,
``discover_workflow``, ``load_workflow``, ``apply_updates``,
``recover_workflow``, ``pause_workflow``, ``resume_workflow``,
``discard_workflow``, ``create_workflow_branch``, and
``complete_for_human_review``.  The command-line entry point exposes the safe
read/lifecycle subset and accepts ``--state-home`` for isolated operation.
"""
from __future__ import annotations

import argparse
import copy
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


MAX_GRAPH_BYTES = 1024 * 1024
MAX_RECORDS = 10_000
_SCHEMA = "plan-graph.v1"
_TERMINAL_SCHEMA = "plan-human-review-handoff.v1"
_OPERATION_RECEIPT_SCHEMA = "plan-graph-operation.v1"
_DESIGN_JOIN_SCHEMA = "plan-design-join.v1"
_PROVENANCE_ROLES = {"implement", "review", "verify", "integrate", "provider-policy"}
_IMMUTABLE = {"schema_version", "workflow_id", "graph_revision", "identity", "baseline"}
_TOP_LEVEL = {
    "schema_version",
    "workflow_id",
    "graph_revision",
    "identity",
    "baseline",
    "outcomes",
    "evidence",
    "decisions",
    "work",
    "proof",
    "git",
    "projections",
    "invalidations",
    "unresolved",
    "lifecycle",
    "audit",
    "design_join",
}
_PROTECTED_BRANCHES = {"main", "master", "develop", "development", "trunk"}
_HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")


def _empty_design_join() -> dict[str, Any]:
    return {
        "required": False,
        "record_version": 1,
        "receipt": None,
        "fresh": True,
        "operation_receipt": None,
    }


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class PlanGraphError(RuntimeError):
    """A deterministic contract, state, or safety failure."""


class RevisionConflict(PlanGraphError):
    """The caller's graph revision cannot be safely reapplied."""


class _UnsafeState(PlanGraphError):
    pass


class _CorruptGraph(PlanGraphError):
    pass


class _MissingState(PlanGraphError):
    pass


def _canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlanGraphError("value is not JSON-compatible") from error
    return hashlib.sha256(payload).hexdigest()


def _validate_provenance_receipt(
    receipt: object, *, expected_id: str | None = None
) -> dict[str, Any]:
    value = _mapping(receipt, "provenance receipt")
    fields = {
        "receipt_id",
        "workflow_id",
        "graph_revision",
        "role",
        "session_id",
        "raw_evidence_digest",
        "source",
        "issued_at",
        "digest",
    }
    if set(value) != fields:
        raise PlanGraphError("provenance receipt schema is incomplete")
    receipt_id = _identifier(value.get("receipt_id"), "provenance receipt id")
    if expected_id is not None and receipt_id != expected_id:
        raise PlanGraphError("provenance receipt identity mismatch")
    if not isinstance(value.get("workflow_id"), str) or not re.fullmatch(
        r"[0-9a-f]{32}", value["workflow_id"]
    ):
        raise PlanGraphError("invalid provenance workflow identity")
    _integer(value.get("graph_revision"), "provenance graph revision", minimum=1)
    if value.get("role") not in _PROVENANCE_ROLES:
        raise PlanGraphError("invalid provenance role")
    _identifier(value.get("session_id"), "provenance session id")
    if not isinstance(value.get("raw_evidence_digest"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", value["raw_evidence_digest"]
    ):
        raise PlanGraphError("invalid raw-evidence digest")
    _text(value.get("source"), "provenance source", maximum=2048)
    _text(value.get("issued_at"), "provenance issued timestamp", maximum=256)
    digest = value.get("digest")
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    if digest != _canonical_digest(unsigned):
        raise PlanGraphError("provenance receipt digest mismatch")
    return value


def issue_provenance_receipt(*, workflow_id: str, graph_revision: int, role: str,
                             session_id: str, raw_evidence_digest: str, source: str) -> dict[str, str | int]:
    """Trusted-coordinator boundary: issue a bounded provenance descriptor.

    This helper does not authenticate callers; consumers must persist and
    verify the returned descriptor in their coordinator-owned store.
    """
    if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
        raise PlanGraphError("invalid provenance workflow identity")
    if role not in _PROVENANCE_ROLES:
        raise PlanGraphError("invalid provenance role")
    _identifier(session_id, "provenance session id")
    if not isinstance(raw_evidence_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_evidence_digest):
        raise PlanGraphError("invalid raw-evidence digest")
    _text(source, "provenance source", maximum=2048)
    _integer(graph_revision, "provenance graph revision", minimum=1)
    value = {"receipt_id": secrets.token_hex(16), "workflow_id": workflow_id,
            "graph_revision": graph_revision, "role": role, "session_id": session_id,
            "raw_evidence_digest": raw_evidence_digest, "source": source, "issued_at": _now()}
    value["digest"] = _canonical_digest(value)
    return value


def register_provenance_receipt(receipt: object, state_home: Path | None = None) -> dict[str, Any]:
    value = _validate_provenance_receipt(receipt)
    home_path = _absolute_state_path(state_home)
    lock = _thread_lock(f"{home_path}\0provenance\0{value['receipt_id']}")
    with lock:
        home_fd = _open_directory_path(home_path, create=True)
        try:
            prov_fd = _open_private_child(home_fd, "provenance", create=True)
            try:
                _create_json_entry_exclusive(
                    prov_fd, f"{value['receipt_id']}.json", value
                )
                _revalidate_directory(home_fd, "provenance", prov_fd)
            finally:
                os.close(prov_fd)
        finally:
            os.close(home_fd)
    return copy.deepcopy(value)


def load_provenance_receipt(receipt_id: str, state_home: Path | None = None) -> dict[str, Any]:
    """Load one coordinator-issued receipt without scanning the store."""
    if not isinstance(receipt_id, str) or not _ID.fullmatch(receipt_id):
        raise PlanGraphError("invalid provenance receipt id")
    home_fd = _open_directory_path(_absolute_state_path(state_home), create=False)
    try:
        prov_fd = _open_private_child(home_fd, "provenance", create=False)
        try:
            value = _load_provenance_from_fd(prov_fd, receipt_id)
            _revalidate_directory(home_fd, "provenance", prov_fd)
            return copy.deepcopy(value)
        finally:
            os.close(prov_fd)
    finally:
        os.close(home_fd)


@dataclass(frozen=True)
class Receipt:
    workflow_id: str
    revision: int
    path: Path
    previous_path: Path
    state: str
    request_id: str | None = None
    head: str | None = None
    applied_paths: tuple[tuple[str, ...], ...] = ()
    reconciled: bool = False
    previous_revision: int | None = None
    current_revision: int | None = None
    generation_paths: tuple[Path, ...] = ()
    terminal_path: Path | None = None


@dataclass(frozen=True)
class BranchReceipt:
    branch: str
    commit: str


@dataclass(frozen=True)
class _RepoContext:
    repository: Path
    git_common_dir: Path
    branch: str
    head: str
    object_hex_length: int


@dataclass
class _Transaction:
    context: _RepoContext
    home_path: Path
    root_path: Path
    key: str
    home_fd: int
    root_fd: int
    lock_fd: int
    provenance_fd: int
    work_fd: int | None

    @property
    def work_path(self) -> Path:
        return self.root_path / self.key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _require_posix() -> None:
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
    if os.name != "posix" or any(not hasattr(os, item) for item in required):
        raise PlanGraphError("private Plan Graph state requires POSIX no-follow operations")
    for operation in (
        os.open,
        os.mkdir,
        os.stat,
        os.unlink,
        os.rename,
        os.rmdir,
        os.link,
    ):
        if operation not in os.supports_dir_fd:
            raise PlanGraphError("required descriptor-relative filesystem operation unavailable")


def _run_git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        raise PlanGraphError(process.stderr.strip() or "git command failed")
    return process.stdout.strip()


def _repository(repo: Path) -> Path:
    try:
        candidate = Path(repo).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PlanGraphError("repository is not usable") from error
    if not candidate.is_dir():
        raise PlanGraphError("repository is not a directory")
    try:
        return Path(_run_git(candidate, "rev-parse", "--path-format=absolute", "--show-toplevel")).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PlanGraphError("repository identity is not usable") from error


def _check_branch_name(repo: Path, branch: object, *, allow_protected: bool = False) -> str:
    if not isinstance(branch, str) or not branch or len(branch) > 244 or "\x00" in branch:
        raise PlanGraphError("invalid branch name")
    process = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        raise PlanGraphError("invalid branch name")
    leaf = branch.rsplit("/", 1)[-1].lower()
    if not allow_protected and (branch.lower() in _PROTECTED_BRANCHES or leaf in _PROTECTED_BRANCHES):
        raise PlanGraphError("protected branch is not an eligible workflow target")
    return branch


def _repo_context(repo: Path, branch: str, *, require_current: bool = True) -> _RepoContext:
    repository = _repository(repo)
    branch = _check_branch_name(repository, branch)
    try:
        common = Path(
            _run_git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        ).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise PlanGraphError("Git common-directory identity is not usable") from error
    if require_current:
        current = _run_git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
        if current != branch:
            raise PlanGraphError("requested workflow branch is not the current branch")
    object_format = _run_git(repository, "rev-parse", "--show-object-format")
    if object_format == "sha1":
        length = 40
    elif object_format == "sha256":
        length = 64
    else:
        raise PlanGraphError("unsupported Git object format")
    head = _run_git(repository, "rev-parse", "--verify", "HEAD")
    if not re.fullmatch(rf"[0-9a-f]{{{length}}}", head):
        raise PlanGraphError("Git returned an invalid full commit identity")
    return _RepoContext(repository, common, branch, head, length)


def _is_full_commit(context: _RepoContext, value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        rf"[0-9a-f]{{{context.object_hex_length}}}", value
    ):
        return False
    process = subprocess.run(
        ["git", "cat-file", "-e", f"{value}^{{commit}}"],
        cwd=context.repository,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    return process.returncode == 0


def _commit_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    process = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode == 0:
        return True
    if process.returncode == 1:
        return False
    raise PlanGraphError(process.stderr.strip() or "cannot validate Design candidate ancestry")


def _identity_key(context: _RepoContext) -> str:
    identity = f"{context.git_common_dir}\0{context.branch}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def default_state_home() -> Path:
    """Return the helper-owned private state root for fresh sessions."""
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured) / "expskill"
    return Path.home() / ".local" / "state" / "expskill"


def _absolute_state_path(path: Path | None) -> Path:
    candidate = Path(path) if path is not None else default_state_home()
    if ".." in candidate.parts:
        raise PlanGraphError("state path traversal is forbidden")
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.normpath(os.fspath(candidate)))


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_directory_stat(value: os.stat_result, label: str, *, private: bool) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise _UnsafeState(f"{label} is not a directory")
    # Public/root-owned ancestors such as /tmp are legitimate.  They are still
    # traversed descriptor-relative with O_NOFOLLOW; only helper-owned private
    # components have an ownership and exact-mode contract.
    if private:
        if value.st_uid != os.geteuid():
            raise _UnsafeState(f"{label} is not owned by the current user")
        if stat.S_IMODE(value.st_mode) != 0o700:
            raise _UnsafeState(f"{label} must have mode 0700")


def _validate_regular_stat(value: os.stat_result, label: str) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise _UnsafeState(f"{label} is not a regular file")
    if value.st_uid != os.geteuid() or stat.S_IMODE(value.st_mode) != 0o600:
        raise _UnsafeState(f"{label} must be owned and mode 0600")
    if value.st_nlink != 1:
        raise _UnsafeState(f"{label} must not be hard-linked")


def _open_directory_path(path: Path, *, create: bool) -> int:
    _require_posix()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        parts = path.parts[1:] if path.is_absolute() else path.parts
        if not parts:
            raise _UnsafeState("state root cannot be the filesystem root")
        for index, component in enumerate(parts):
            created = False
            try:
                child = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise _MissingState("state root does not exist")
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                try:
                    child = os.open(component, flags, dir_fd=fd)
                except OSError as error:
                    raise _UnsafeState("state path component is unsafe") from error
            except OSError as error:
                raise _UnsafeState("state path contains a symlink or non-directory") from error
            child_stat = os.fstat(child)
            lexical_stat = os.stat(component, dir_fd=fd, follow_symlinks=False)
            if not _same_inode(child_stat, lexical_stat):
                os.close(child)
                raise _UnsafeState("state path component was substituted")
            _validate_directory_stat(
                child_stat,
                "state root" if index == len(parts) - 1 else "state ancestor",
                private=index == len(parts) - 1,
            )
            if created and stat.S_IMODE(child_stat.st_mode) != 0o700:
                os.fchmod(child, 0o700)
                child_stat = os.fstat(child)
                _validate_directory_stat(child_stat, "created state directory", private=True)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_private_child(parent_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    created = False
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise _MissingState(f"missing private directory: {name}")
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise _UnsafeState(f"unsafe private directory: {name}") from error
    except OSError as error:
        raise _UnsafeState(f"unsafe private directory: {name}") from error
    try:
        descriptor_stat = os.fstat(fd)
        lexical_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_inode(descriptor_stat, lexical_stat):
            raise _UnsafeState(f"private directory was substituted: {name}")
        if created and stat.S_IMODE(descriptor_stat.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
            descriptor_stat = os.fstat(fd)
        _validate_directory_stat(descriptor_stat, name, private=True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _revalidate_directory(parent_fd: int, name: str, fd: int) -> None:
    try:
        lexical = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise _UnsafeState(f"private directory disappeared: {name}") from error
    descriptor = os.fstat(fd)
    _validate_directory_stat(descriptor, name, private=True)
    if not _same_inode(lexical, descriptor):
        raise _UnsafeState(f"private directory was substituted: {name}")


def _open_lock(root_fd: int, key: str) -> int:
    name = f"{key}.lock"
    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    created = False
    try:
        fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd)
        created = True
    except FileExistsError:
        try:
            fd = os.open(name, flags, dir_fd=root_fd)
        except OSError as error:
            raise _UnsafeState("stable identity lock is unsafe") from error
    except OSError as error:
        raise _UnsafeState("stable identity lock is unsafe") from error
    try:
        descriptor = os.fstat(fd)
        lexical = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if created and stat.S_IMODE(descriptor.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
            descriptor = os.fstat(fd)
        _validate_regular_stat(descriptor, "stable identity lock")
        if not _same_inode(descriptor, lexical):
            raise _UnsafeState("stable identity lock was substituted")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _thread_lock(identity: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(identity, threading.RLock())


def _entry_stat(directory_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise _MissingState(f"missing state entry: {name}") from error
    except OSError as error:
        raise _UnsafeState(f"unsafe state entry: {name}") from error


def _read_bytes_entry(directory_fd: int, name: str) -> bytes:
    lexical = _entry_stat(directory_fd, name)
    _validate_regular_stat(lexical, name)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory_fd)
    except OSError as error:
        raise _UnsafeState(f"unsafe graph generation: {name}") from error
    try:
        descriptor = os.fstat(fd)
        _validate_regular_stat(descriptor, name)
        if not _same_inode(lexical, descriptor):
            raise _UnsafeState(f"graph generation was substituted: {name}")
        chunks: list[bytes] = []
        remaining = MAX_GRAPH_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_GRAPH_BYTES:
            raise _CorruptGraph("graph is oversized")
        after = _entry_stat(directory_fd, name)
        if not _same_inode(descriptor, after):
            raise _UnsafeState(f"graph generation changed while reading: {name}")
        return payload
    finally:
        os.close(fd)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode_json(payload: bytes, label: str) -> dict[str, Any]:
    if b"!!" in payload:
        raise _CorruptGraph(f"{label} is not strict JSON-compatible YAML")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise _CorruptGraph(f"{label} is not strict JSON-compatible YAML") from error
    if not isinstance(value, dict):
        raise _CorruptGraph(f"{label} must contain an object")
    return value


def _read_json_entry(directory_fd: int, name: str) -> dict[str, Any]:
    return _decode_json(_read_bytes_entry(directory_fd, name), name)


def _atomic_write_entry(directory_fd: int, name: str, value: dict[str, Any]) -> None:
    try:
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlanGraphError("graph is not JSON-compatible") from error
    if len(payload) > MAX_GRAPH_BYTES:
        raise PlanGraphError("graph is oversized")
    temporary = f".tmp-{os.getpid()}-{secrets.token_hex(8)}"
    fd: int | None = None
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        descriptor = os.fstat(fd)
        if stat.S_IMODE(descriptor.st_mode) != 0o600:
            os.fchmod(fd, 0o600)
        _validate_regular_stat(os.fstat(fd), "temporary generation")
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        _validate_regular_stat(_entry_stat(directory_fd, name), name)
        os.fsync(directory_fd)
    except OSError as error:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise PlanGraphError("atomic graph write failed") from error


def _create_json_entry_exclusive(
    directory_fd: int, name: str, value: dict[str, Any]
) -> None:
    """Atomically publish one immutable record without an overwrite window."""
    try:
        payload = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PlanGraphError("record is not JSON-compatible") from error
    if len(payload) > MAX_GRAPH_BYTES:
        raise PlanGraphError("record is oversized")
    temporary = f".provenance-{os.getpid()}-{secrets.token_hex(8)}"
    fd: int | None = None
    temporary_created = False
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        temporary_created = True
        _validate_regular_stat(os.fstat(fd), "temporary provenance record")
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
        os.close(fd)
        fd = None
        # Hard-link publication is atomic and refuses an existing target.  The
        # fully synchronized temporary is removed immediately afterward, so a
        # successful registration settles at one link without overwriting any
        # same-user injected object.
        os.link(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
        os.unlink(temporary, dir_fd=directory_fd)
        temporary_created = False
        _validate_regular_stat(_entry_stat(directory_fd, name), name)
        os.fsync(directory_fd)
    except FileExistsError as error:
        raise PlanGraphError("provenance receipt already exists") from error
    except OSError as error:
        if fd is not None:
            os.close(fd)
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                pass
        raise PlanGraphError("atomic provenance registration failed") from error
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                pass


def _load_provenance_from_fd(directory_fd: int, receipt_id: str) -> dict[str, Any]:
    if not isinstance(receipt_id, str) or not _ID.fullmatch(receipt_id):
        raise PlanGraphError("invalid provenance receipt id")
    return _validate_provenance_receipt(
        _read_json_entry(directory_fd, f"{receipt_id}.json"),
        expected_id=receipt_id,
    )


def _validate_root_entry(root_fd: int, name: str, requested_key: str, *, allow_requested_corrupt: bool) -> None:
    if name == "terminal":
        terminal_fd = _open_private_child(root_fd, name, create=False)
        os.close(terminal_fd)
        return
    if name.endswith(".lock") and _HEX_KEY.fullmatch(name[:-5]):
        _validate_regular_stat(_entry_stat(root_fd, name), name)
        return
    if name.endswith(".deleting") and _HEX_KEY.fullmatch(name[:-9]):
        tomb_key = name[:-9]
        tomb_fd = _open_private_child(root_fd, name, create=False)
        os.close(tomb_fd)
        if tomb_key == requested_key:
            raise _UnsafeState("requested workflow has an incomplete deletion tomb")
        return
    if not _HEX_KEY.fullmatch(name):
        raise _UnsafeState("unknown private-state root entry")
    work_fd = _open_private_child(root_fd, name, create=False)
    try:
        entries = set(os.listdir(work_fd))
        if name == requested_key and not entries <= {"current.yaml", "previous.yaml"}:
            raise _UnsafeState("workflow contains unknown state entries")
        if name == requested_key and "previous.yaml" in entries:
            _validate_regular_stat(_entry_stat(work_fd, "previous.yaml"), "previous.yaml")
        if "current.yaml" not in entries:
            return
        try:
            current = _read_json_entry(work_fd, "current.yaml")
        except _CorruptGraph:
            if name == requested_key and allow_requested_corrupt:
                return
            raise
        identity = current.get("identity")
        if not isinstance(identity, dict):
            raise _CorruptGraph("stored graph has malformed identity")
        common = identity.get("git_common_dir")
        branch = identity.get("target_branch")
        if not isinstance(common, str) or not isinstance(branch, str):
            raise _CorruptGraph("stored graph has malformed identity")
        expected = hashlib.sha256(f"{common}\0{branch}".encode("utf-8")).hexdigest()
        if expected != name:
            raise _UnsafeState("workflow is stored under a substituted identity")
    finally:
        os.close(work_fd)


def _scan_root(root_fd: int, requested_key: str, *, allow_requested_corrupt: bool) -> None:
    entries = os.listdir(root_fd)
    if len(entries) > MAX_RECORDS:
        raise _UnsafeState("private-state root contains too many entries")
    for name in entries:
        _validate_root_entry(root_fd, name, requested_key, allow_requested_corrupt=allow_requested_corrupt)


@contextmanager
def _transaction(
    repo: Path,
    branch: str,
    state_home: Path | None,
    *,
    require_workflow: bool,
    allow_corrupt_current: bool = False,
) -> Iterator[_Transaction]:
    context = _repo_context(Path(repo), branch)
    home_path = _absolute_state_path(state_home)
    home_fd = _open_directory_path(home_path, create=True)
    root_fd = -1
    lock_fd = -1
    provenance_fd = -1
    work_fd: int | None = None
    transaction: _Transaction | None = None
    key = _identity_key(context)
    lock = _thread_lock(f"{home_path}\0{key}")
    lock.acquire()
    try:
        actual_home = Path(os.readlink(f"/proc/self/fd/{home_fd}"))
        for forbidden in (context.repository, context.git_common_dir):
            if actual_home == forbidden or actual_home.is_relative_to(forbidden):
                raise _UnsafeState("private state must be outside the repository")
        root_fd = _open_private_child(home_fd, "plan-graphs", create=True)
        provenance_fd = _open_private_child(home_fd, "provenance", create=True)
        root_path = actual_home / "plan-graphs"
        lock_fd = _open_lock(root_fd, key)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _revalidate_directory(home_fd, "plan-graphs", root_fd)
        _scan_root(root_fd, key, allow_requested_corrupt=allow_corrupt_current)
        try:
            work_fd = _open_private_child(root_fd, key, create=False)
        except _MissingState:
            if require_workflow:
                raise PlanGraphError("workflow not found")
        transaction = _Transaction(
            context,
            actual_home,
            root_path,
            key,
            home_fd,
            root_fd,
            lock_fd,
            provenance_fd,
            work_fd,
        )
        yield transaction
    finally:
        closing_work_fd = transaction.work_fd if transaction is not None else work_fd
        if closing_work_fd is not None:
            os.close(closing_work_fd)
        if lock_fd >= 0:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
        if root_fd >= 0:
            os.close(root_fd)
        if provenance_fd >= 0:
            os.close(provenance_fd)
        os.close(home_fd)
        lock.release()


def _transaction_provenance_resolver(transaction: _Transaction) -> Any:
    """Return an exact-ID resolver bound to this locked descriptor transaction."""

    def resolve(receipt_id: str) -> dict[str, Any]:
        _revalidate_directory(
            transaction.home_fd, "provenance", transaction.provenance_fd
        )
        value = _load_provenance_from_fd(transaction.provenance_fd, receipt_id)
        _revalidate_directory(
            transaction.home_fd, "provenance", transaction.provenance_fd
        )
        return value

    return resolve


def _mapping(value: object, label: str, *, allow_empty: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PlanGraphError(f"{label} must be an object")
    if len(value) > MAX_RECORDS or (not allow_empty and not value):
        raise PlanGraphError(f"{label} has an invalid record count")
    return value


def _sequence(value: object, label: str, *, allow_empty: bool = True) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_RECORDS or (not allow_empty and not value):
        raise PlanGraphError(f"{label} must be a bounded list")
    return value


def _text(value: object, label: str, *, maximum: int = 16_384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise PlanGraphError(f"{label} must be nonempty bounded text")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise PlanGraphError(f"{label} is not a valid identifier")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlanGraphError(f"{label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise PlanGraphError(f"{label} must be a boolean")
    return value


def _id_list(value: object, label: str, *, allow_empty: bool = True) -> list[str]:
    result = _sequence(value, label, allow_empty=allow_empty)
    for item in result:
        _identifier(item, label)
    if len(result) != len(set(result)):
        raise PlanGraphError(f"{label} contains duplicate identifiers")
    return result


def _text_list(value: object, label: str, *, allow_empty: bool = True) -> list[str]:
    result = _sequence(value, label, allow_empty=allow_empty)
    for item in result:
        _text(item, label)
    return result


def _only(record: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(record) - allowed
    if unknown:
        raise PlanGraphError(f"{label} contains unsupported fields")


def _semantic_audit_floor(graph: dict[str, Any]) -> tuple[bool, bool, bool, str]:
    """Derive the non-downgradable audit floor from typed plan semantics."""
    decisions = graph.get("decisions") if isinstance(graph.get("decisions"), dict) else {}
    work = graph.get("work") if isinstance(graph.get("work"), dict) else {}
    git = graph.get("git") if isinstance(graph.get("git"), dict) else {}
    lanes = git.get("lanes") if isinstance(git.get("lanes"), dict) else {}
    joins = git.get("joins") if isinstance(git.get("joins"), dict) else {}
    design_join = graph.get("design_join") if isinstance(graph.get("design_join"), dict) else {}
    evidence = graph.get("evidence") if isinstance(graph.get("evidence"), dict) else {}

    # These are meaning-bearing signals: a real alternatives decision or an
    # external mechanism boundary broadens the plan.  Counts alone never do.
    breadth = any(
        isinstance(record, dict)
        and record.get("material") is True
        and isinstance(record.get("alternatives"), list)
        and len(record["alternatives"]) >= 2
        for record in decisions.values()
    ) or any(
        isinstance(record, dict) and record.get("kind") == "external"
        for record in evidence.values()
    )
    # Explicit parallel ownership and an integration join create adversarially
    # relevant coordination consequences independent of plan size.
    complexity = design_join.get("required") is True or bool(joins) or bool(lanes) or any(
        isinstance(record, dict)
        and record.get("concurrency") in {"parallel-safe", "parallel-candidate"}
        for record in work.values()
    )
    supplied = graph.get("audit") if isinstance(graph.get("audit"), dict) else {}
    # Consequence classifications are typed human/coordinator assertions.  They
    # may escalate the structural floor but validation never lets them lower it.
    high_consequence = supplied.get("high_consequence") is True
    reasons: list[str] = []
    if breadth:
        reasons.append("material alternatives or external mechanism evidence")
    if complexity:
        reasons.append("parallel ownership or integration-join consequences")
    if high_consequence:
        reasons.append("typed high-consequence signal")
    return breadth, complexity, high_consequence, "; ".join(reasons) or "no audit-forcing semantic signal"


def _audit_classification(breadth: bool, complexity: bool, consequence: bool) -> str:
    if consequence:
        return "high-consequence"
    if complexity:
        return "complex"
    if breadth:
        return "broad"
    return "tiny"


def _normalize_graph(graph: dict[str, Any], context: _RepoContext, workflow_id: str) -> dict[str, Any]:
    value = copy.deepcopy(graph)
    timestamp = _now()
    value["schema_version"] = _SCHEMA
    value["workflow_id"] = workflow_id
    value["graph_revision"] = 1
    value["identity"] = {
        "repository": str(context.repository),
        "git_common_dir": str(context.git_common_dir),
        "target_branch": context.branch,
        "created_at": timestamp,
    }
    value["baseline"] = {
        "repository_revision": context.head,
        "dirty_state_fingerprint": _dirty_fingerprint(context.repository),
        "observed_at": timestamp,
        "reproducible": True,
    }
    evidence = value.get("evidence")
    if isinstance(evidence, dict):
        for record in evidence.values():
            if isinstance(record, dict):
                record.setdefault("limitations", [])
                record.setdefault("observed_at", timestamp)
                record.setdefault("record_version", 1)
                record.setdefault("operation_receipt", None)
                if record.get("kind") == "repository":
                    record.setdefault("revision", context.head)
    decisions = value.get("decisions")
    if isinstance(decisions, dict):
        for record in decisions.values():
            if isinstance(record, dict):
                record.setdefault("consequences", [])
                record.setdefault("invalidates", [])
                record.setdefault("operation_receipt", None)
    proof = value.get("proof")
    if isinstance(proof, dict):
        for record in proof.values():
            if isinstance(record, dict):
                record.setdefault("fresh", True)
                record.setdefault("execution_required", bool(record.get("evidence")))
                record.setdefault("record_version", 1)
                record.setdefault("operation_receipt", None)
    projections = value.get("projections")
    if isinstance(projections, dict):
        for record in projections.values():
            if isinstance(record, dict):
                record.setdefault("decision_versions", {})
                record.setdefault("operation_receipt", None)
    lifecycle = value.get("lifecycle")
    if lifecycle is None:
        lifecycle = {}
        value["lifecycle"] = lifecycle
    if isinstance(lifecycle, dict):
        lifecycle.setdefault("state", None)
        lifecycle["created_at"] = timestamp
        lifecycle["updated_at"] = timestamp
        lifecycle["derived_state"] = "not-ready"
    supplied_audit = value.get("audit") if isinstance(value.get("audit"), dict) else {}
    floor_breadth, floor_complexity, floor_consequence, floor_reason = _semantic_audit_floor(value)
    breadth = floor_breadth or supplied_audit.get("breadth") is True
    complexity = floor_complexity or supplied_audit.get("complexity") is True
    consequence = floor_consequence or supplied_audit.get("high_consequence") is True
    required = breadth or complexity or consequence
    value["audit"] = {
        "classification": _audit_classification(breadth, complexity, consequence),
        "breadth": breadth,
        "complexity": complexity,
        "high_consequence": consequence,
        "reason": floor_reason,
        "required": required,
        "graph_revision": 1,
        "record_version": 1,
        "evidence": list(value.get("evidence", {}).keys()) if required and isinstance(value.get("evidence"), dict) else [],
        "independent": False,
        "constraints": ["no research", "no redesign", "no edit", "no question", "no approve"],
        "findings": [],
        "resolutions": [],
        "fresh": not required,
        "operation_receipt": None,
    }
    value.setdefault("design_join", _empty_design_join())
    return value


def _dirty_fingerprint(repo: Path) -> str:
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repo,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        raise PlanGraphError("cannot fingerprint repository dirty state")
    return hashlib.sha256(process.stdout).hexdigest()


def _validate_receipt(
    receipt: object,
    graph: dict[str, Any],
    context: _RepoContext | None,
    proof_id: str,
) -> None:
    value = _mapping(receipt, "proof evidence receipt")
    _only(
        value,
        {"workflow_id", "graph_revision", "node", "branch", "commit", "check", "result"},
        "proof evidence receipt",
    )
    if value.get("workflow_id") != graph["workflow_id"]:
        raise PlanGraphError("evidence receipt workflow mismatch")
    receipt_revision = _integer(
        value.get("graph_revision"), "evidence receipt graph revision", minimum=1
    )
    if receipt_revision >= graph["graph_revision"]:
        raise PlanGraphError("evidence receipt revision mismatch")
    node = _identifier(value.get("node"), "evidence receipt node")
    required = graph["proof"][proof_id]["required_by"]
    if node not in graph["work"] or node not in required:
        raise PlanGraphError("evidence receipt node is not required by this proof")
    if value.get("branch") != graph["identity"]["target_branch"]:
        raise PlanGraphError("evidence receipt branch mismatch")
    check = _text(value.get("check"), "evidence receipt command/check")
    if check != check.strip():
        raise PlanGraphError("evidence receipt command/check is not exact")
    commit = value.get("commit")
    if context is not None and not _is_full_commit(context, commit):
        raise PlanGraphError("evidence receipt commit is invalid")
    if context is None and (
        not isinstance(commit, str)
        or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit)
    ):
        raise PlanGraphError("evidence receipt commit is not a full commit identity")
    result = _mapping(value.get("result"), "evidence receipt result")
    _only(result, {"status", "exit_code"}, "evidence receipt result")
    if result.get("status") != "pass" or _integer(
        result.get("exit_code"), "evidence receipt exit code"
    ) != 0:
        raise PlanGraphError("evidence receipt does not prove a passing execution")


def _record_payload_digest(record: dict[str, Any]) -> str:
    return _canonical_digest(
        {key: item for key, item in record.items() if key != "operation_receipt"}
    )


def _validate_operation_receipt(
    receipt: object,
    *,
    graph: dict[str, Any],
    target: tuple[str, ...],
    record: dict[str, Any],
    record_version: int,
    allowed_operations: set[str],
    exact_prior_revision: int | None = None,
) -> dict[str, Any] | None:
    if receipt is None:
        return None
    value = _mapping(receipt, "typed operation receipt")
    fields = {
        "schema_version",
        "receipt_id",
        "operation",
        "workflow_id",
        "prior_graph_revision",
        "target",
        "record_version",
        "value_digest",
        "issued_at",
        "digest",
    }
    if set(value) != fields or value.get("schema_version") != _OPERATION_RECEIPT_SCHEMA:
        raise PlanGraphError("typed operation receipt schema is incomplete")
    _identifier(value.get("receipt_id"), "typed operation receipt id")
    if value.get("operation") not in allowed_operations:
        raise PlanGraphError("typed operation receipt has the wrong operation")
    if value.get("workflow_id") != graph["workflow_id"]:
        raise PlanGraphError("typed operation receipt workflow mismatch")
    prior = _integer(
        value.get("prior_graph_revision"),
        "typed operation prior graph revision",
        minimum=1,
    )
    if exact_prior_revision is not None and prior != exact_prior_revision:
        raise PlanGraphError("typed operation receipt is stale")
    if prior >= graph["graph_revision"] and exact_prior_revision is None:
        raise PlanGraphError("typed operation receipt revision is not prior")
    if value.get("target") != list(target):
        raise PlanGraphError("typed operation receipt target mismatch")
    if _integer(
        value.get("record_version"), "typed operation record version", minimum=1
    ) != record_version:
        raise PlanGraphError("typed operation receipt record version mismatch")
    expected_value_digest = _record_payload_digest(record)
    if value.get("value_digest") != expected_value_digest:
        raise PlanGraphError("typed operation receipt value mismatch")
    _text(value.get("issued_at"), "typed operation receipt timestamp", maximum=256)
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != _canonical_digest(unsigned):
        raise PlanGraphError("typed operation receipt digest mismatch")
    return value


def issue_operation_receipt(
    *,
    operation: str,
    workflow_id: str,
    prior_graph_revision: int,
    target: list[str],
    record_version: int,
    value: dict[str, Any],
) -> dict[str, Any]:
    """Build the closed receipt a coordinator attaches to one typed mutation."""
    if operation not in {
        "refresh-evidence",
        "reconfirm-decision",
        "refresh-proof",
        "refresh-proof-plan",
        "regenerate-projection",
        "reconfirm-projection",
        "refresh-audit",
        "resolve-finding",
        "record-design-join",
    }:
        raise PlanGraphError("unsupported typed operation receipt")
    if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
        raise PlanGraphError("invalid workflow identity")
    _integer(prior_graph_revision, "prior graph revision", minimum=1)
    _integer(record_version, "record version", minimum=1)
    if not isinstance(target, list) or not target or any(
        not isinstance(component, str) or not component for component in target
    ):
        raise PlanGraphError("invalid typed operation target")
    record = copy.deepcopy(_mapping(value, "typed operation value"))
    if operation == "refresh-proof" and record.get("evidence"):
        record["execution_required"] = True
    receipt: dict[str, Any] = {
        "schema_version": _OPERATION_RECEIPT_SCHEMA,
        "receipt_id": secrets.token_hex(16),
        "operation": operation,
        "workflow_id": workflow_id,
        "prior_graph_revision": prior_graph_revision,
        "target": list(target),
        "record_version": record_version,
        "value_digest": _record_payload_digest(record),
        "issued_at": _now(),
    }
    receipt["digest"] = _canonical_digest(receipt)
    return receipt


def issue_design_join_receipt(
    *,
    workflow_id: str,
    plan_revision: int,
    baseline: str,
    design_workflow_id: str,
    design_revision: int,
    design_branch: str,
    candidate_commit: str,
    brief_digest: str,
    approval_digest: str,
    manifest_digest: str,
    approved: bool,
    design_delivery_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Build the bounded Design result that Plan may join into its graph."""
    if not re.fullmatch(r"[0-9a-f]{32}", str(workflow_id)) or not re.fullmatch(r"[0-9a-f]{32}", str(design_workflow_id)):
        raise PlanGraphError("invalid Design join workflow identity")
    _integer(plan_revision, "Design join plan revision", minimum=1)
    _integer(design_revision, "Design workflow revision", minimum=1)
    _text(design_branch, "Design branch", maximum=244)
    for label, value in (("baseline", baseline), ("candidate commit", candidate_commit)):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
            raise PlanGraphError(f"invalid Design join {label}")
    for label, value in (("brief digest", brief_digest), ("approval digest", approval_digest), ("manifest digest", manifest_digest)):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise PlanGraphError(f"invalid Design join {label}")
    if approved is not True:
        raise PlanGraphError("Design join requires confirmed approval")
    delivery = _validate_design_delivery_receipt(design_delivery_receipt)
    delivery_identity = delivery["identity"]
    expected_bindings = {
        "workflow_id": design_workflow_id,
        "revision": design_revision,
        "baseline": baseline,
        "branch": design_branch,
        "candidate_commit": candidate_commit,
        "brief_digest": brief_digest,
        "approval_digest": approval_digest,
        "manifest_digest": manifest_digest,
    }
    observed_bindings = {
        "workflow_id": delivery["workflow_id"],
        "revision": delivery["revision"],
        "baseline": delivery_identity["baseline"],
        "branch": delivery_identity["branch"],
        "candidate_commit": delivery["candidate_commit"],
        "brief_digest": delivery["brief_digest"],
        "approval_digest": delivery["approval_digest"],
        "manifest_digest": delivery["manifest_digest"],
    }
    if observed_bindings != expected_bindings or delivery_identity["head"] != candidate_commit:
        raise PlanGraphError("Design delivery receipt binding mismatch")
    receipt: dict[str, Any] = {
        "schema_version": _DESIGN_JOIN_SCHEMA,
        "receipt_id": secrets.token_hex(16),
        "workflow_id": workflow_id,
        "plan_revision": plan_revision,
        "baseline": baseline,
        "design_workflow_id": design_workflow_id,
        "design_revision": design_revision,
        "design_branch": design_branch,
        "candidate_commit": candidate_commit,
        "brief_digest": brief_digest,
        "approval_digest": approval_digest,
        "manifest_digest": manifest_digest,
        "approved": True,
        "design_delivery_receipt": copy.deepcopy(delivery),
        "issued_at": _now(),
    }
    receipt["digest"] = _canonical_digest(receipt)
    return receipt


def _validate_design_delivery_receipt(value: object) -> dict[str, Any]:
    receipt = _mapping(value, "Design delivery receipt")
    fields = {
        "schema_version", "operation", "workflow_id", "revision", "lifecycle",
        "identity", "state_digest", "candidate_digest", "candidate_inventory_digest",
        "review_evidence_digest", "manifest_digest", "evidence_digest",
        "approval_digest", "dependency_digest", "brief_digest", "candidate_commit",
    }
    if set(receipt) != fields or receipt.get("schema_version") != 1:
        raise PlanGraphError("Design delivery receipt schema is incomplete")
    if receipt.get("operation") != "deliver" or receipt.get("lifecycle") != "delivered":
        raise PlanGraphError("Design delivery receipt is not delivered")
    if not re.fullmatch(r"[0-9a-f]{32}", str(receipt.get("workflow_id", ""))):
        raise PlanGraphError("invalid Design delivery workflow identity")
    _integer(receipt.get("revision"), "Design delivery revision", minimum=1)
    identity = _mapping(receipt.get("identity"), "Design delivery identity")
    identity_fields = {
        "repository", "branch", "worktree", "baseline", "head",
        "dirty_fingerprint", "ui_contract_digest",
    }
    if set(identity) != identity_fields:
        raise PlanGraphError("Design delivery identity is incomplete")
    for field in ("repository", "branch", "worktree"):
        _text(identity.get(field), f"Design delivery identity {field}")
    for field in ("baseline", "head", "candidate_commit"):
        candidate = receipt.get(field) if field == "candidate_commit" else identity.get(field)
        if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate):
            raise PlanGraphError(f"invalid Design delivery {field}")
    for field in (
        "dirty_fingerprint", "ui_contract_digest", "state_digest", "candidate_digest",
        "candidate_inventory_digest", "review_evidence_digest", "manifest_digest",
        "evidence_digest", "approval_digest", "dependency_digest", "brief_digest",
    ):
        candidate = identity.get(field) if field in identity else receipt.get(field)
        if not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate):
            raise PlanGraphError(f"invalid Design delivery {field}")
    if identity["head"] != receipt["candidate_commit"]:
        raise PlanGraphError("Design delivery candidate identity mismatch")
    return receipt


def _design_join_record(graph: dict[str, Any]) -> dict[str, Any]:
    value = graph.get("design_join")
    if value is None:
        return _empty_design_join()
    return _mapping(value, "Design join")


def _validate_design_join(graph: dict[str, Any], context: _RepoContext | None) -> None:
    record = _design_join_record(graph)
    if set(record) != {"required", "record_version", "receipt", "fresh", "operation_receipt"}:
        raise PlanGraphError("Design join fields are incomplete")
    required = _boolean(record.get("required"), "Design join requirement")
    fresh = _boolean(record.get("fresh"), "Design join freshness")
    record_version = _integer(record.get("record_version"), "Design join record version", minimum=1)
    receipt = record.get("receipt")
    operation = record.get("operation_receipt")
    if not required:
        if receipt is not None or not fresh or operation is not None:
            raise PlanGraphError("disabled Design join contains active state")
        return
    if receipt is None:
        if fresh or operation is not None:
            raise PlanGraphError("unresolved Design join claims freshness")
        return
    value = _mapping(receipt, "Design join receipt")
    fields = {
        "schema_version", "receipt_id", "workflow_id", "plan_revision", "baseline",
        "design_workflow_id", "design_revision", "design_branch", "candidate_commit",
        "brief_digest", "approval_digest", "manifest_digest", "approved",
        "design_delivery_receipt", "issued_at", "digest",
    }
    if set(value) != fields or value.get("schema_version") != _DESIGN_JOIN_SCHEMA:
        raise PlanGraphError("Design join receipt schema is incomplete")
    _identifier(value.get("receipt_id"), "Design join receipt id")
    if value.get("workflow_id") != graph["workflow_id"]:
        raise PlanGraphError("Design join workflow mismatch")
    plan_revision = _integer(value.get("plan_revision"), "Design join plan revision", minimum=1)
    design_revision = _integer(value.get("design_revision"), "Design workflow revision", minimum=1)
    if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("design_workflow_id", ""))):
        raise PlanGraphError("invalid Design workflow identity")
    if value.get("baseline") != graph["baseline"]["repository_revision"]:
        raise PlanGraphError("Design join baseline mismatch")
    branch = _text(value.get("design_branch"), "Design branch", maximum=244)
    if branch == graph["identity"]["target_branch"]:
        raise PlanGraphError("Design join must come from an isolated branch")
    if context is not None:
        _check_branch_name(context.repository, branch)
    for field in ("brief_digest", "approval_digest", "manifest_digest"):
        if not isinstance(value.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise PlanGraphError(f"invalid Design join {field}")
    if value.get("approved") is not True:
        raise PlanGraphError("Design join is not approved")
    delivery = _validate_design_delivery_receipt(value.get("design_delivery_receipt"))
    delivery_identity = delivery["identity"]
    if (
        delivery["workflow_id"] != value["design_workflow_id"]
        or delivery["revision"] != design_revision
        or delivery_identity["baseline"] != value["baseline"]
        or delivery_identity["branch"] != branch
        or delivery_identity["head"] != value["candidate_commit"]
        or delivery["candidate_commit"] != value["candidate_commit"]
        or delivery["brief_digest"] != value["brief_digest"]
        or delivery["approval_digest"] != value["approval_digest"]
        or delivery["manifest_digest"] != value["manifest_digest"]
    ):
        raise PlanGraphError("Design delivery receipt binding mismatch")
    _text(value.get("issued_at"), "Design join timestamp", maximum=256)
    unsigned = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != _canonical_digest(unsigned):
        raise PlanGraphError("Design join receipt digest mismatch")
    candidate = value.get("candidate_commit")
    if context is not None:
        if not _is_full_commit(context, candidate):
            raise PlanGraphError("Design candidate commit is unavailable")
        parents = _run_git(context.repository, "rev-list", "--parents", "-n", "1", candidate).split()
        if len(parents) != 2 or parents[1] != value["baseline"]:
            raise PlanGraphError("Design candidate is not one commit above baseline")
        if not _commit_is_ancestor(context.repository, candidate, context.head):
            branch_tip = _run_git(
                context.repository,
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"refs/heads/{branch}^{{commit}}",
            )
            if branch_tip != candidate:
                raise PlanGraphError("Design candidate is not the isolated branch tip")
    elif not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", candidate):
        raise PlanGraphError("invalid Design candidate commit")
    validated_operation = _validate_operation_receipt(
        operation,
        graph=graph,
        target=("design_join",),
        record=record,
        record_version=record_version,
        allowed_operations={"record-design-join"},
    )
    if validated_operation is None or validated_operation["prior_graph_revision"] != plan_revision:
        raise PlanGraphError("Design join operation binding mismatch")
    if not fresh:
        raise PlanGraphError("Design join receipt is stale")


def _validate_graph_inner(
    graph: object,
    context: _RepoContext | None,
    branch: str | None,
    *,
    validate_objects: bool,
    provenance_resolver: Any = None,
) -> None:
    value = _mapping(graph, "Plan Graph")
    if set(value) not in (_TOP_LEVEL, _TOP_LEVEL - {"design_join"}):
        raise PlanGraphError("Plan Graph has missing or unsupported top-level families")
    if value.get("schema_version") != _SCHEMA:
        raise PlanGraphError("unsupported Plan Graph schema")
    workflow_id = value.get("workflow_id")
    if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
        raise PlanGraphError("invalid workflow identity")
    _integer(value.get("graph_revision"), "graph revision", minimum=1)

    identity = _mapping(value.get("identity"), "identity")
    if set(identity) != {"repository", "git_common_dir", "target_branch", "created_at"}:
        raise PlanGraphError("identity fields are incomplete")
    for field in ("repository", "git_common_dir", "target_branch", "created_at"):
        _text(identity.get(field), f"identity {field}")
    if context is not None:
        if identity["repository"] != str(context.repository):
            raise PlanGraphError("repository identity mismatch")
        if identity["git_common_dir"] != str(context.git_common_dir):
            raise PlanGraphError("Git common-directory identity mismatch")
        if identity["target_branch"] != context.branch:
            raise PlanGraphError("target branch identity mismatch")
    if branch is not None and identity["target_branch"] != branch:
        raise PlanGraphError("target branch identity mismatch")

    baseline = _mapping(value.get("baseline"), "baseline")
    if set(baseline) != {
        "repository_revision",
        "dirty_state_fingerprint",
        "observed_at",
        "reproducible",
    }:
        raise PlanGraphError("baseline fields are incomplete")
    _text(baseline.get("repository_revision"), "baseline revision")
    if not isinstance(baseline.get("dirty_state_fingerprint"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", baseline["dirty_state_fingerprint"]
    ):
        raise PlanGraphError("invalid dirty-state fingerprint")
    _text(baseline.get("observed_at"), "baseline timestamp")
    _boolean(baseline.get("reproducible"), "baseline reproducibility")
    if context is not None and validate_objects and not _is_full_commit(
        context, baseline["repository_revision"]
    ):
        raise PlanGraphError("baseline is not a full repository commit")

    _validate_design_join(value, context if validate_objects else None)

    outcomes = _mapping(value.get("outcomes"), "outcomes", allow_empty=False)
    for outcome_id, raw in outcomes.items():
        _identifier(outcome_id, "outcome id")
        record = _mapping(raw, f"outcome {outcome_id}")
        _only(record, {"kind", "result"}, f"outcome {outcome_id}")
        if record.get("kind") not in {"outcome", "constraint", "non-goal"}:
            raise PlanGraphError("invalid outcome kind")
        _text(record.get("result"), "outcome result")

    evidence = _mapping(value.get("evidence"), "evidence", allow_empty=False)
    decision_ids = set(_mapping(value.get("decisions"), "decisions"))
    work_ids = set(_mapping(value.get("work"), "work", allow_empty=False))
    for evidence_id, raw in evidence.items():
        _identifier(evidence_id, "evidence id")
        record = _mapping(raw, f"evidence {evidence_id}")
        _only(
            record,
            {"kind", "fact", "source", "revision", "version", "limitations", "fresh", "supports", "observed_at",
             "record_version", "operation_receipt"},
            f"evidence {evidence_id}",
        )
        if record.get("kind") not in {"repository", "external"}:
            raise PlanGraphError("invalid evidence kind")
        _text(record.get("fact"), "evidence fact")
        _text(record.get("source"), "evidence source")
        _boolean(record.get("fresh"), "evidence freshness")
        record_version = _integer(
            record.get("record_version"), "evidence record version", minimum=1
        )
        _text_list(record.get("limitations"), "evidence limitations")
        _text(record.get("observed_at"), "evidence timestamp")
        if record["kind"] == "repository":
            _text(record.get("revision"), "repository evidence revision")
        elif "version" in record:
            _text(record.get("version"), "external evidence version")
        elif "revision" in record:
            _text(record.get("revision"), "external evidence revision")
        else:
            raise PlanGraphError("external evidence lacks a version or revision")
        supports = _id_list(record.get("supports"), "evidence supports")
        if not set(supports) <= decision_ids | work_ids:
            raise PlanGraphError("evidence supports an unknown record")
        _validate_operation_receipt(
            record.get("operation_receipt"),
            graph=value,
            target=("evidence", evidence_id),
            record=record,
            record_version=record_version,
            allowed_operations={"refresh-evidence"},
        )

    decisions = _mapping(value.get("decisions"), "decisions")
    for decision_id, raw in decisions.items():
        _identifier(decision_id, "decision id")
        record = _mapping(raw, f"decision {decision_id}")
        _only(
            record,
            {
                "question",
                "choice",
                "alternatives",
                "based_on",
                "material",
                "version",
                "confirmed_version",
                "stale",
                "invalidates",
                "consequences",
                "operation_receipt",
            },
            f"decision {decision_id}",
        )
        _text(record.get("question"), "decision question")
        _text(record.get("choice"), "decision choice")
        _boolean(record.get("material"), "decision materiality")
        version = _integer(record.get("version"), "decision version", minimum=1)
        confirmed = record.get("confirmed_version")
        if confirmed is not None:
            _integer(confirmed, "confirmed decision version", minimum=1)
            if confirmed > version:
                raise PlanGraphError("decision confirmation is from the future")
        _boolean(record.get("stale"), "decision staleness")
        based_on = _id_list(record.get("based_on"), "decision evidence", allow_empty=False)
        if not set(based_on) <= set(evidence):
            raise PlanGraphError("decision references unknown evidence")
        _text_list(record.get("consequences"), "decision consequences")
        _id_list(record.get("invalidates"), "decision invalidation targets")
        alternatives = _sequence(record.get("alternatives"), "decision alternatives")
        for alternative in alternatives:
            alternative_record = _mapping(alternative, "decision alternative")
            _only(alternative_record, {"id", "status", "reason"}, "decision alternative")
            _identifier(alternative_record.get("id"), "alternative id")
            if alternative_record.get("status") not in {"selected", "rejected", "conditional"}:
                raise PlanGraphError("invalid alternative status")
            _text(alternative_record.get("reason"), "alternative reason")
        _validate_operation_receipt(
            record.get("operation_receipt"),
            graph=value,
            target=("decisions", decision_id),
            record=record,
            record_version=version,
            allowed_operations={"reconfirm-decision"},
        )

    proof = _mapping(value.get("proof"), "proof", allow_empty=False)
    proof_ids = set(proof)
    for work_id, raw in _mapping(value.get("work"), "work", allow_empty=False).items():
        _identifier(work_id, "work id")
        record = _mapping(raw, f"work {work_id}")
        _only(
            record,
            {
                "kind",
                "result",
                "covers",
                "requires",
                "based_on",
                "decisions",
                "proof",
                "repository_boundary",
                "owner",
                "concurrency",
                "parallel_basis",
            },
            f"work {work_id}",
        )
        if record.get("kind") not in {"slice", "task", "join"}:
            raise PlanGraphError("invalid work kind")
        _text(record.get("result"), "work result")
        covers = _id_list(record.get("covers"), "work outcome coverage", allow_empty=False)
        if not set(covers) <= set(outcomes):
            raise PlanGraphError("work references unknown outcome")
        requires = _id_list(record.get("requires"), "work prerequisites")
        if not set(requires) <= work_ids:
            raise PlanGraphError("work references unknown prerequisite")
        based_on = _id_list(record.get("based_on"), "work evidence", allow_empty=False)
        if not set(based_on) <= set(evidence):
            raise PlanGraphError("work references unknown evidence")
        linked_decisions = _id_list(record.get("decisions"), "work decisions")
        if not set(linked_decisions) <= set(decisions):
            raise PlanGraphError("work references unknown decision")
        linked_proof = _id_list(record.get("proof"), "work proof", allow_empty=False)
        if not set(linked_proof) <= proof_ids:
            raise PlanGraphError("work references unknown proof")
        _text_list(record.get("repository_boundary"), "repository boundary", allow_empty=False)
        _text(record.get("owner"), "work owner", maximum=256)
        concurrency = record.get("concurrency")
        if concurrency not in {"serial", "parallel-safe", "parallel-candidate"}:
            raise PlanGraphError("invalid concurrency classification")
        if concurrency == "parallel-safe":
            basis = _mapping(record.get("parallel_basis"), "parallel basis")
            if set(basis) != {
                "stable_inputs",
                "ownership_disjoint",
                "shared_state_ordering",
                "independent_proof",
                "joins_at",
            }:
                raise PlanGraphError("parallel basis is incomplete")
            if not _boolean(basis["stable_inputs"], "stable inputs"):
                raise PlanGraphError("parallel inputs are not stable")
            if not _boolean(basis["ownership_disjoint"], "ownership disjointness"):
                raise PlanGraphError("parallel ownership is not disjoint")
            if _boolean(basis["shared_state_ordering"], "shared-state ordering"):
                raise PlanGraphError("parallel work has hidden shared-state ordering")
            if not _boolean(basis["independent_proof"], "independent proof"):
                raise PlanGraphError("parallel work lacks independent proof")
            _identifier(basis["joins_at"], "parallel join")
        elif "parallel_basis" in record:
            raise PlanGraphError("non-parallel-safe work must not claim a parallel basis")

    # Complete graph dependency cycle check.
    work = value["work"]
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(work_id: str) -> None:
        if work_id in visiting:
            raise PlanGraphError("work dependency cycle")
        if work_id in visited:
            return
        visiting.add(work_id)
        for prerequisite in work[work_id]["requires"]:
            visit(prerequisite)
        visiting.remove(work_id)
        visited.add(work_id)

    for work_id in work:
        visit(work_id)

    for proof_id, raw in proof.items():
        _identifier(proof_id, "proof id")
        record = _mapping(raw, f"proof {proof_id}")
        _only(
            record,
            {"claim", "covers", "required_by", "planned_method", "evidence", "fresh", "execution_required",
             "record_version", "operation_receipt"},
            f"proof {proof_id}",
        )
        _text(record.get("claim"), "proof claim")
        covers = _id_list(record.get("covers"), "proof outcome coverage", allow_empty=False)
        if not set(covers) <= set(outcomes):
            raise PlanGraphError("proof references unknown outcome")
        required_by = _id_list(record.get("required_by"), "proof work", allow_empty=False)
        if not set(required_by) <= work_ids:
            raise PlanGraphError("proof references unknown work")
        method = _mapping(record.get("planned_method"), "planned proof method")
        if set(method) != {"surface", "positive", "negative"}:
            raise PlanGraphError("planned proof method is incomplete")
        for field in ("surface", "positive", "negative"):
            _text(method.get(field), f"planned proof {field}")
        _boolean(record.get("fresh"), "proof freshness")
        proof_version = _integer(
            record.get("record_version"), "proof record version", minimum=1
        )
        receipts = _sequence(record.get("evidence"), "proof evidence")
        execution_required = _boolean(record.get("execution_required", bool(receipts)), "proof execution requirement")
        if receipts and not execution_required:
            raise PlanGraphError("executed proof requires execution evidence")
        for receipt in receipts:
            _validate_receipt(receipt, value, context, proof_id)
        for work_id in required_by:
            if proof_id not in work[work_id]["proof"]:
                raise PlanGraphError("proof/work reference is not bidirectional")
        for work_id, work_record in work.items():
            if proof_id in work_record["proof"] and work_id not in required_by:
                raise PlanGraphError("work/proof reference is not bidirectional")
        _validate_operation_receipt(
            record.get("operation_receipt"),
            graph=value,
            target=("proof", proof_id),
            record=record,
            record_version=proof_version,
            allowed_operations={"refresh-proof", "refresh-proof-plan"},
        )

    git = _mapping(value.get("git"), "Git topology")
    if set(git) != {"target", "lanes", "joins", "delivery"}:
        raise PlanGraphError("Git topology fields are incomplete")
    target = _mapping(git.get("target"), "Git target")
    if set(target) != {"branch", "protected", "reproducible", "dirty_dependency"}:
        raise PlanGraphError("Git target fields are incomplete")
    _text(target.get("branch"), "Git target branch")
    if target["branch"] != identity["target_branch"]:
        raise PlanGraphError("Git target branch mismatch")
    if _boolean(target.get("protected"), "Git target protection"):
        raise PlanGraphError("protected target branch is forbidden")
    _boolean(target.get("reproducible"), "Git target reproducibility")
    _boolean(target.get("dirty_dependency"), "Git target dirty dependency")

    lanes = _mapping(git.get("lanes"), "Git lanes")
    joins = _mapping(git.get("joins"), "Git joins")
    lane_work: dict[str, str] = {}
    for lane_id, raw in lanes.items():
        _identifier(lane_id, "lane id")
        lane = _mapping(raw, f"lane {lane_id}")
        _only(lane, {"work", "base", "owner", "integrates_at"}, f"lane {lane_id}")
        linked_work = _id_list(lane.get("work"), "lane work", allow_empty=False)
        if not set(linked_work) <= work_ids:
            raise PlanGraphError("lane references unknown work")
        for work_id in linked_work:
            if work_id in lane_work:
                raise PlanGraphError("work is assigned to multiple lanes")
            lane_work[work_id] = lane_id
            if work[work_id]["owner"] != lane.get("owner"):
                raise PlanGraphError("lane/work ownership mismatch")
        _text(lane.get("owner"), "lane owner")
        join_id = _identifier(lane.get("integrates_at"), "lane join")
        if join_id not in joins:
            raise PlanGraphError("lane references unknown join")
        if context is not None and validate_objects and not _is_full_commit(context, lane.get("base")):
            raise PlanGraphError("lane base is not a full repository commit")
        elif context is None:
            _text(lane.get("base"), "lane base")

    for join_id, raw in joins.items():
        _identifier(join_id, "join id")
        join = _mapping(raw, f"join {join_id}")
        _only(join, {"lanes", "proof"}, f"join {join_id}")
        if join_id not in work or work[join_id]["kind"] != "join":
            raise PlanGraphError("Git join lacks a join work node")
        join_lanes = _id_list(join.get("lanes"), "join lanes", allow_empty=False)
        expected_lanes = {key for key, lane in lanes.items() if lane["integrates_at"] == join_id}
        if set(join_lanes) != expected_lanes:
            raise PlanGraphError("join lane set is incomplete")
        join_proof = _id_list(join.get("proof"), "join proof", allow_empty=False)
        if set(join_proof) != set(work[join_id]["proof"]):
            raise PlanGraphError("join proof is not bidirectional")
        required_lane_work = {work_id for lane_id in join_lanes for work_id in lanes[lane_id]["work"]}
        if not required_lane_work <= set(work[join_id]["requires"]):
            raise PlanGraphError("join does not depend on every lane")

    for work_id, record in work.items():
        if record["concurrency"] == "parallel-safe":
            join_id = record["parallel_basis"]["joins_at"]
            if work_id not in lane_work or join_id not in joins:
                raise PlanGraphError("parallel-safe work lacks a lane and join")
            if lanes[lane_work[work_id]]["integrates_at"] != join_id:
                raise PlanGraphError("parallel-safe work lane joins elsewhere")

    _validate_delivery(value, context, provenance_resolver)

    projections = _mapping(value.get("projections"), "projections")
    projection_targets = set(outcomes) | set(decisions) | work_ids | proof_ids
    for projection_id, raw in projections.items():
        _identifier(projection_id, "projection id")
        projection = _mapping(raw, f"projection {projection_id}")
        _only(
            projection,
            {"covers", "version", "decision_versions", "presented", "confirmed", "stale",
             "operation_receipt"},
            f"projection {projection_id}",
        )
        covers = _id_list(projection.get("covers"), "projection coverage", allow_empty=False)
        if not set(covers) <= projection_targets:
            raise PlanGraphError("projection references unknown records")
        projection_version = _integer(projection.get("version"), "projection version", minimum=1)
        versions = _mapping(projection.get("decision_versions"), "projection decision versions")
        for decision_id, version in versions.items():
            if decision_id not in decisions:
                raise PlanGraphError("projection references unknown decision version")
            _integer(version, "projected decision version", minimum=1)
        _boolean(projection.get("presented"), "projection presentation")
        _boolean(projection.get("confirmed"), "projection confirmation")
        _boolean(projection.get("stale"), "projection staleness")
        _validate_operation_receipt(
            projection.get("operation_receipt"),
            graph=value,
            target=("projections", projection_id),
            record=projection,
            record_version=projection_version,
            allowed_operations={"regenerate-projection", "reconfirm-projection"},
        )

    all_records = set(evidence) | set(decisions) | work_ids | proof_ids | set(projections)
    for decision_id, decision in decisions.items():
        if not set(decision["invalidates"]) <= all_records:
            raise PlanGraphError(
                f"decision {decision_id} invalidates an unknown record"
            )
    invalidations = _sequence(value.get("invalidations"), "invalidations")
    for raw in invalidations:
        record = _mapping(raw, "invalidation")
        _only(record, {"source", "targets"}, "invalidation")
        source = _identifier(record.get("source"), "invalidation source")
        targets = _id_list(record.get("targets"), "invalidation targets", allow_empty=False)
        if source not in all_records or not set(targets) <= all_records:
            raise PlanGraphError("invalidation references unknown records")

    unresolved = _sequence(value.get("unresolved"), "unresolved")
    for raw in unresolved:
        record = _mapping(raw, "unresolved item")
        _only(record, {"id", "kind", "material", "question", "reason"}, "unresolved item")
        if "id" in record:
            _identifier(record["id"], "unresolved id")
        if "kind" in record:
            _text(record["kind"], "unresolved kind")
        if "material" in record:
            _boolean(record["material"], "unresolved materiality")
        if "question" in record:
            _text(record["question"], "unresolved question")
        if "reason" in record:
            _text(record["reason"], "unresolved reason")

    audit = _mapping(value.get("audit"), "audit")
    _only(audit, {"classification", "breadth", "complexity", "high_consequence", "reason", "required", "graph_revision",
                  "record_version", "evidence", "independent", "constraints", "findings", "resolutions", "fresh",
                  "operation_receipt"}, "audit")
    if audit.get("classification") not in {"tiny", "broad", "complex", "high-consequence"}:
        raise PlanGraphError("invalid audit classification")
    breadth = _boolean(audit.get("breadth"), "audit breadth")
    complexity = _boolean(audit.get("complexity"), "audit complexity")
    consequence = _boolean(audit.get("high_consequence"), "audit consequence")
    floor_breadth, floor_complexity, floor_consequence, _ = _semantic_audit_floor(value)
    if (floor_breadth and not breadth) or (floor_complexity and not complexity) or (
        floor_consequence and not consequence
    ):
        raise PlanGraphError("audit classification is below the semantic floor")
    expected_required = breadth or complexity or consequence
    if audit["classification"] != _audit_classification(
        breadth, complexity, consequence
    ):
        raise PlanGraphError("audit classification contradicts semantic indicators")
    _text(audit.get("reason"), "audit reason")
    required = _boolean(audit.get("required"), "audit required")
    if required != expected_required:
        raise PlanGraphError("audit requirement does not match plan classification")
    _integer(audit.get("graph_revision"), "audit graph revision", minimum=1)
    audit_version = _integer(audit.get("record_version"), "audit record version", minimum=1)
    audit_evidence = _id_list(audit.get("evidence"), "audit evidence")
    if not set(audit_evidence) <= set(evidence):
        raise PlanGraphError("audit references unknown evidence")
    independent = _boolean(audit.get("independent"), "audit independence")
    _text_list(audit.get("constraints"), "audit constraints", allow_empty=False)
    findings = _sequence(audit.get("findings"), "audit findings")
    finding_ids: set[str] = set()
    for finding in findings:
        item = _mapping(finding, "audit finding")
        _only(item, {"id", "severity", "evidence", "disposition"}, "audit finding")
        fid = _identifier(item.get("id"), "audit finding id")
        if fid in finding_ids:
            raise PlanGraphError("duplicate audit finding")
        finding_ids.add(fid)
        if item.get("severity") not in {"low", "medium", "high", "critical"}:
            raise PlanGraphError("invalid audit finding severity")
        finding_evidence = _id_list(item.get("evidence"), "audit finding evidence", allow_empty=False)
        if not set(finding_evidence) <= set(evidence):
            raise PlanGraphError("audit finding references unknown evidence")
        if item.get("disposition") not in {"open", "resolved"}:
            raise PlanGraphError("invalid audit finding disposition")
    resolutions = _sequence(audit.get("resolutions"), "audit resolutions")
    resolved_ids: set[str] = set()
    for resolution in resolutions:
        item = _mapping(resolution, "audit resolution")
        _only(item, {"finding_id", "disposition", "evidence"}, "audit resolution")
        fid = _identifier(item.get("finding_id"), "audit resolution finding")
        if fid not in finding_ids or fid in resolved_ids:
            raise PlanGraphError("audit resolution references an unknown or duplicate finding")
        resolved_ids.add(fid)
        if item.get("disposition") != "resolved":
            raise PlanGraphError("audit resolution is not resolved")
        resolution_evidence = _id_list(item.get("evidence"), "audit resolution evidence", allow_empty=False)
        if not set(resolution_evidence) <= set(evidence):
            raise PlanGraphError("audit resolution references unknown evidence")
    _boolean(audit.get("fresh"), "audit freshness")
    if not required and audit["classification"] == "tiny" and audit["findings"]:
        raise PlanGraphError("tiny audit cannot contain findings")
    resolved_findings = {
        item["id"] for item in findings if item.get("disposition") == "resolved"
    }
    open_findings = finding_ids - resolved_findings
    if resolved_ids != resolved_findings or resolved_ids & open_findings:
        raise PlanGraphError("audit finding resolutions do not match dispositions")
    operation_receipt = _validate_operation_receipt(
        audit.get("operation_receipt"),
        graph=value,
        target=("audit",),
        record=audit,
        record_version=audit_version,
        allowed_operations={"refresh-audit", "resolve-finding"},
    )
    if required and audit["fresh"]:
        if not independent or operation_receipt is None:
            raise PlanGraphError("current required audit lacks independent receipt")
        if audit["graph_revision"] != operation_receipt["prior_graph_revision"]:
            raise PlanGraphError("required audit revision does not match its receipt")

    lifecycle = _mapping(value.get("lifecycle"), "lifecycle")
    _only(lifecycle, {"state", "derived_state", "created_at", "updated_at"}, "lifecycle")
    if lifecycle.get("state") not in {None, "paused"}:
        raise PlanGraphError("invalid editable lifecycle state")
    if lifecycle.get("derived_state") not in {
        "not-ready",
        "awaiting-user",
        "ready",
        "stale",
        "paused",
    }:
        raise PlanGraphError("invalid derived lifecycle state")
    _text(lifecycle.get("created_at"), "lifecycle creation timestamp")
    _text(lifecycle.get("updated_at"), "lifecycle update timestamp")


def _validate_graph(
    graph: object,
    context: _RepoContext | None,
    branch: str | None,
    *,
    validate_objects: bool = True,
    provenance_resolver: Any = None,
) -> None:
    try:
        _validate_graph_inner(graph, context, branch, validate_objects=validate_objects, provenance_resolver=provenance_resolver)
    except PlanGraphError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise PlanGraphError("malformed Plan Graph") from error


def _derive_validated(graph: dict[str, Any]) -> str:
    if graph["lifecycle"]["state"] == "paused":
        return "paused"
    if graph["unresolved"]:
        return "awaiting-user"
    if not graph["baseline"]["reproducible"] or not graph["git"]["target"]["reproducible"]:
        return "not-ready"
    if graph["git"]["target"]["dirty_dependency"]:
        return "not-ready"
    design_join = _design_join_record(graph)
    if design_join["required"] and (not design_join["fresh"] or design_join["receipt"] is None):
        return "not-ready"
    audit = graph["audit"]
    if audit["required"] and (not audit["fresh"] or any(item.get("disposition") == "open" for item in audit["findings"])):
        return "stale"
    if any(not evidence["fresh"] for evidence in graph["evidence"].values()):
        return "stale"
    if any(not proof["fresh"] for proof in graph["proof"].values()):
        return "stale"
    for decision in graph["decisions"].values():
        if decision["stale"]:
            return "stale"
        if decision["material"] and decision["confirmed_version"] != decision["version"]:
            return "awaiting-user"

    outcomes = set(graph["outcomes"])
    work_coverage = {item for work in graph["work"].values() for item in work["covers"]}
    proof_coverage = {item for proof in graph["proof"].values() for item in proof["covers"]}
    if not outcomes <= work_coverage or not outcomes <= proof_coverage:
        return "not-ready"
    if not graph["projections"]:
        return "not-ready"

    covered_decisions: set[str] = set()
    for projection in graph["projections"].values():
        if projection["stale"]:
            return "stale"
        if not projection["presented"] or not projection["confirmed"]:
            return "awaiting-user"
        covered_decisions.update(item for item in projection["covers"] if item in graph["decisions"])
        for decision_id, version in projection["decision_versions"].items():
            if graph["decisions"][decision_id]["version"] != version:
                return "stale"
    for decision_id, decision in graph["decisions"].items():
        if decision["material"]:
            if decision_id not in covered_decisions:
                return "not-ready"
            if not any(
                projection["decision_versions"].get(decision_id) == decision["version"]
                for projection in graph["projections"].values()
                if decision_id in projection["covers"]
            ):
                return "stale"
    return "ready"


def derive_plan_state(graph: object) -> str:
    """Derive readiness from validated relationships; editable status is ignored."""
    _validate_graph(graph, None, None, validate_objects=False)
    return _derive_validated(graph)  # type: ignore[arg-type]


def _finalize_graph(
    graph: dict[str, Any], context: _RepoContext, provenance_resolver: Any = None
) -> None:
    lifecycle = _mapping(graph.get("lifecycle"), "lifecycle")
    lifecycle["updated_at"] = _now()
    lifecycle["derived_state"] = "not-ready"
    _validate_graph(
        graph, context, context.branch, provenance_resolver=provenance_resolver
    )
    lifecycle["derived_state"] = _derive_validated(graph)
    _validate_graph(
        graph, context, context.branch, provenance_resolver=provenance_resolver
    )


def _read_current(transaction: _Transaction) -> dict[str, Any]:
    if transaction.work_fd is None:
        raise PlanGraphError("workflow not found")
    _revalidate_directory(transaction.root_fd, transaction.key, transaction.work_fd)
    entries = set(os.listdir(transaction.work_fd))
    if "current.yaml" not in entries or not entries <= {"current.yaml", "previous.yaml"}:
        raise _UnsafeState("workflow generations are missing or ambiguous")
    if "previous.yaml" in entries:
        _validate_regular_stat(_entry_stat(transaction.work_fd, "previous.yaml"), "previous.yaml")
    graph = _read_json_entry(transaction.work_fd, "current.yaml")
    graph.setdefault("design_join", _empty_design_join())
    _validate_graph(
        graph,
        transaction.context,
        transaction.context.branch,
        provenance_resolver=_transaction_provenance_resolver(transaction),
    )
    if graph["lifecycle"]["derived_state"] != _derive_validated(graph):
        raise _CorruptGraph("stored derived lifecycle state is inconsistent")
    return graph


def _read_previous(transaction: _Transaction) -> dict[str, Any]:
    if transaction.work_fd is None:
        raise PlanGraphError("workflow not found")
    graph = _read_json_entry(transaction.work_fd, "previous.yaml")
    graph.setdefault("design_join", _empty_design_join())
    _validate_graph(
        graph,
        transaction.context,
        transaction.context.branch,
        provenance_resolver=_transaction_provenance_resolver(transaction),
    )
    if graph["lifecycle"]["derived_state"] != _derive_validated(graph):
        raise _CorruptGraph("previous derived lifecycle state is inconsistent")
    return graph


def _receipt(
    graph: dict[str, Any],
    transaction: _Transaction,
    *,
    previous_revision: int | None,
    applied_paths: tuple[tuple[str, ...], ...] = (),
    reconciled: bool = False,
) -> Receipt:
    current = transaction.work_path / "current.yaml"
    previous = transaction.work_path / "previous.yaml"
    delivery = graph["git"]["delivery"]
    request = delivery.get("request") if isinstance(delivery, dict) else None
    request_id = request.get("id") if isinstance(request, dict) else None
    head = delivery.get("exact_head") if isinstance(delivery, dict) else None
    return Receipt(
        graph["workflow_id"],
        graph["graph_revision"],
        current,
        previous,
        _derive_validated(graph),
        request_id,
        head,
        applied_paths,
        reconciled,
        previous_revision,
        graph["graph_revision"],
        (current, previous),
    )


def _rotate(transaction: _Transaction, old: dict[str, Any], new: dict[str, Any]) -> None:
    if transaction.work_fd is None:
        raise PlanGraphError("workflow not found")
    entries = set(os.listdir(transaction.work_fd))
    if not entries <= {"current.yaml", "previous.yaml"} or "current.yaml" not in entries:
        raise _UnsafeState("workflow generations are ambiguous")
    if "previous.yaml" in entries:
        _read_previous(transaction)
    _atomic_write_entry(transaction.work_fd, "previous.yaml", old)
    _atomic_write_entry(transaction.work_fd, "current.yaml", new)
    _revalidate_directory(transaction.root_fd, transaction.key, transaction.work_fd)
    if set(os.listdir(transaction.work_fd)) != {"current.yaml", "previous.yaml"}:
        raise _UnsafeState("generation rotation left unexpected entries")


def initialize_workflow(
    repo: Path,
    branch: str,
    graph: dict[str, Any],
    state_home: Path | None = None,
) -> Receipt:
    if not isinstance(graph, dict):
        raise PlanGraphError("Plan Graph template must be an object")
    if graph.get("schema_version") not in {None, _SCHEMA}:
        raise PlanGraphError("unsupported Plan Graph schema")
    with _transaction(repo, branch, state_home, require_workflow=False) as transaction:
        if transaction.work_fd is not None:
            raise PlanGraphError("workflow already exists")
        workflow_id = secrets.token_hex(16)
        candidate = _normalize_graph(graph, transaction.context, workflow_id)
        _finalize_graph(
            candidate,
            transaction.context,
            _transaction_provenance_resolver(transaction),
        )
        try:
            transaction.work_fd = _open_private_child(transaction.root_fd, transaction.key, create=True)
            if os.listdir(transaction.work_fd):
                raise _UnsafeState("new workflow directory is not empty")
            _atomic_write_entry(transaction.work_fd, "current.yaml", candidate)
            _revalidate_directory(transaction.root_fd, transaction.key, transaction.work_fd)
        except BaseException:
            if transaction.work_fd is not None and not os.listdir(transaction.work_fd):
                try:
                    os.rmdir(transaction.key, dir_fd=transaction.root_fd)
                    os.fsync(transaction.root_fd)
                except OSError:
                    pass
            raise
        return _receipt(candidate, transaction, previous_revision=None)


def discover_workflow(
    repo: Path, branch: str, state_home: Path | None = None
) -> Receipt:
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        graph = _read_current(transaction)
        return _receipt(
            graph,
            transaction,
            previous_revision=graph["graph_revision"] - 1 if graph["graph_revision"] > 1 else None,
        )


def load_workflow(
    repo: Path, branch: str, state_home: Path | None = None
) -> dict[str, Any]:
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        return copy.deepcopy(_read_current(transaction))


def _validated_updates(updates: object) -> tuple[list[dict[str, Any]], tuple[tuple[str, ...], ...]]:
    rows = _sequence(updates, "updates", allow_empty=False)
    normalized: list[dict[str, Any]] = []
    paths: list[tuple[str, ...]] = []
    for row in rows:
        update = _mapping(row, "update")
        operation = update.get("op")
        typed = operation in {"refresh-evidence", "reconfirm-decision", "refresh-proof", "refresh-proof-plan",
                              "regenerate-projection", "reconfirm-projection", "refresh-audit", "resolve-finding", "record-design-join"}
        if operation != "set" and not typed:
            raise PlanGraphError("unsupported update operation")
        expected_fields = (
            {"op", "path", "value", "prior_graph_revision", "record_version", "receipt"}
            if typed
            else {"op", "path", "value"}
        )
        if set(update) != expected_fields:
            raise PlanGraphError("update schema is incomplete")
        raw_path = _sequence(update.get("path"), "update path", allow_empty=False)
        path: list[str] = []
        for component in raw_path:
            if (
                not isinstance(component, str)
                or not component
                or component in {".", ".."}
                or "/" in component
                or "\x00" in component
            ):
                raise PlanGraphError("unsafe update path")
            path.append(component)
        if path[0] in _IMMUTABLE or path[0] not in _TOP_LEVEL:
            raise PlanGraphError("immutable or unsupported update path")
        if operation == "set" and len(path) == 1 and path[0] in {"evidence", "decisions", "work", "proof", "git", "projections", "audit", "design_join"}:
            raise PlanGraphError("broad family replacement is forbidden")
        if operation == "set" and path[0] == "audit":
            raise PlanGraphError("audit updates require a typed operation")
        controlled_true = (
            (path[0] == "evidence" and path[-1] == "fresh" and update.get("value") is True)
            or (path[0] == "proof" and path[-1] == "fresh" and update.get("value") is True)
            or (path[0] == "decisions" and path[-1] in {"confirmed_version", "stale"})
            or (
                path[0] == "projections"
                and path[-1] in {"version", "decision_versions", "presented", "confirmed", "stale"}
                and update.get("value") is not False
                and update.get("value") is not None
            )
            or (path[0] == "design_join" and path[-1] == "fresh" and update.get("value") is True)
        )
        if operation == "set" and controlled_true:
            raise PlanGraphError("readiness-enabling fields require a typed operation")
        if typed:
            expected_family = {"refresh-evidence": "evidence", "reconfirm-decision": "decisions",
                "refresh-proof": "proof", "refresh-proof-plan": "proof", "regenerate-projection": "projections", "reconfirm-projection": "projections",
                "refresh-audit": "audit", "resolve-finding": "audit", "record-design-join": "design_join"}[operation]
            expected_length = 1 if expected_family in {"audit", "design_join"} else 2
            if path[0] != expected_family or len(path) != expected_length:
                raise PlanGraphError("typed update targets the wrong record family")
            _integer(
                update.get("prior_graph_revision"),
                "typed update prior graph revision",
                minimum=1,
            )
            _integer(
                update.get("record_version"), "typed update record version", minimum=1
            )
            _mapping(update.get("receipt"), "typed update receipt", allow_empty=False)
            _mapping(update.get("value"), "typed update value")
        current_path = tuple(path)
        if any(_paths_overlap(current_path, previous) for previous in paths):
            raise PlanGraphError("one mutation contains overlapping update paths")
        paths.append(current_path)
        normalized.append(
            {
                "op": "set",
                "path": path,
                "value": copy.deepcopy(update.get("value")),
                "_typed": operation if typed else None,
                "_prior_graph_revision": update.get("prior_graph_revision"),
                "_record_version": update.get("record_version"),
                "_receipt": copy.deepcopy(update.get("receipt")),
            }
        )
    return normalized, tuple(paths)


def _paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    shortest = min(len(left), len(right))
    return left[:shortest] == right[:shortest]


def _changed_paths(left: Any, right: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if type(left) is not type(right):
        return {prefix}
    if isinstance(left, dict):
        changed: set[tuple[str, ...]] = set()
        for key in set(left) | set(right):
            path = prefix + (key,)
            if key not in left or key not in right:
                changed.add(path)
            else:
                changed.update(_changed_paths(left[key], right[key], path))
        return changed
    if isinstance(left, list):
        return set() if left == right else {prefix}
    return set() if left == right else {prefix}


def _apply_to_graph(graph: dict[str, Any], updates: list[dict[str, Any]]) -> None:
    for update in updates:
        path = update["path"]
        target: Any = graph
        for component in path[:-1]:
            if not isinstance(target, dict) or component not in target:
                raise PlanGraphError("update path does not exist")
            target = target[component]
        if not isinstance(target, dict):
            raise PlanGraphError("update parent is not an object")
        value = copy.deepcopy(update["value"])
        if update.get("_typed"):
            value["operation_receipt"] = copy.deepcopy(update.get("_receipt"))
        target[path[-1]] = value


def _validate_typed_repairs(
    previous_graph: dict[str, Any],
    graph: dict[str, Any],
    updates: list[dict[str, Any]],
    prior_revision: int,
) -> None:
    for update in updates:
        operation = update.get("_typed")
        if not operation:
            continue
        path = update["path"]
        record = graph[path[0]][path[1]] if path[0] not in {"audit", "design_join"} else graph[path[0]]
        previous_record = (
            previous_graph[path[0]][path[1]]
            if path[0] not in {"audit", "design_join"}
            else previous_graph[path[0]]
        )
        if update.get("_prior_graph_revision") != prior_revision:
            raise PlanGraphError("typed update is stale")
        expected_version = update.get("_record_version")
        actual_version = (
            record.get("version")
            if operation in {"reconfirm-decision", "regenerate-projection", "reconfirm-projection"}
            else record.get("record_version")
        )
        previous_version = (
            previous_record.get("version")
            if operation
            in {"reconfirm-decision", "regenerate-projection", "reconfirm-projection"}
            else previous_record.get("record_version")
        )
        if expected_version != actual_version or expected_version != previous_version:
            raise PlanGraphError("typed update record version mismatch")
        allowed_changes = {
            "refresh-evidence": {
                "fact", "source", "revision", "version", "limitations", "fresh",
                "supports", "observed_at", "operation_receipt",
            },
            "reconfirm-decision": {"confirmed_version", "stale", "operation_receipt"},
            "refresh-proof": {"evidence", "fresh", "execution_required", "operation_receipt"},
            "refresh-proof-plan": {"fresh", "operation_receipt"},
            "regenerate-projection": {
                "decision_versions", "presented", "confirmed", "stale", "operation_receipt",
            },
            "reconfirm-projection": {"confirmed", "operation_receipt"},
            "refresh-audit": {
                "classification", "breadth", "complexity", "high_consequence", "reason",
                "required", "graph_revision", "evidence", "independent", "constraints",
                "findings", "resolutions", "fresh", "operation_receipt",
            },
            "resolve-finding": {
                "graph_revision", "evidence", "independent", "findings", "resolutions",
                "fresh", "operation_receipt",
            },
            "record-design-join": {"receipt", "fresh", "operation_receipt"},
        }[operation]
        changed_fields = {
            key
            for key in set(previous_record) | set(record)
            if previous_record.get(key) != record.get(key)
        }
        if not changed_fields <= allowed_changes:
            raise PlanGraphError("typed operation changed fields outside its authority")
        attached = record.get("operation_receipt")
        if attached != update.get("_receipt"):
            raise PlanGraphError("typed update receipt was not attached to its target")
        validated_receipt = _validate_operation_receipt(
            attached,
            graph=graph,
            target=tuple(path),
            record=record,
            record_version=expected_version,
            allowed_operations={operation},
            exact_prior_revision=prior_revision,
        )
        if validated_receipt is None:
            raise PlanGraphError("typed update receipt is required")
        if operation == "refresh-evidence":
            if (
                previous_record.get("fresh") is not False
                or record.get("fresh") is not True
                or not record.get("observed_at")
            ):
                raise PlanGraphError("evidence refresh requires fresh timestamped evidence")
        elif operation == "reconfirm-decision":
            if (
                previous_record.get("stale") is not True
                or record.get("confirmed_version") != record.get("version")
                or record.get("stale")
            ):
                raise PlanGraphError("decision reconfirmation is not current")
        elif operation == "refresh-proof-plan":
            if (
                previous_record.get("fresh") is not False
                or record.get("fresh") is not True
                or previous_record.get("execution_required", bool(previous_record.get("evidence")))
                or record.get("execution_required", bool(record.get("evidence")))
                or record.get("evidence")
                or graph["git"]["delivery"].get("state") != "planning"
            ):
                raise PlanGraphError("proof plan refresh requires an unexecuted planning obligation")
        elif operation == "refresh-proof":
            if (
                previous_record.get("fresh") is not False
                or record.get("fresh") is not True
                or not record.get("evidence")
            ):
                raise PlanGraphError("proof refresh requires exact execution evidence")
        elif operation == "regenerate-projection":
            if (
                previous_record.get("stale") is not True
                or
                record.get("stale")
                or not record.get("presented")
                or record.get("confirmed") is not False
            ):
                raise PlanGraphError(
                    "projection regeneration must await explicit confirmation"
                )
        elif operation == "reconfirm-projection":
            if (
                previous_record.get("confirmed") is not False
                or previous_record.get("stale")
                or record.get("stale")
                or not record.get("presented")
                or not record.get("confirmed")
            ):
                raise PlanGraphError("projection reconfirmation is not current")
        elif operation == "refresh-audit":
            if graph["audit"].get("required") and (
                not graph["audit"].get("fresh")
                or not graph["audit"].get("independent")
            ):
                raise PlanGraphError("audit refresh must be fresh and independent")
        elif operation == "resolve-finding":
            finding_ids = {
                item.get("id")
                for item in graph["audit"].get("findings", [])
                if isinstance(item, dict)
            }
            resolved = {
                item.get("finding_id")
                for item in graph["audit"].get("resolutions", [])
                if isinstance(item, dict)
            }
            if finding_ids != resolved or any(
                item.get("disposition") != "resolved"
                for item in graph["audit"].get("findings", [])
                if isinstance(item, dict)
            ):
                raise PlanGraphError("finding resolution is incomplete")
        elif operation == "record-design-join":
            if (
                previous_record.get("required") is not True
                or previous_record.get("fresh") is not False
                or previous_record.get("receipt") is not None
                or record.get("fresh") is not True
                or not isinstance(record.get("receipt"), dict)
            ):
                raise PlanGraphError("Design join recording is not current")


def _invalidate_semantic_dependents(
    graph: dict[str, Any],
    changed: set[tuple[str, ...]],
    *,
    preserve_evidence: set[str] | None = None,
) -> None:
    """Propagate material meaning changes through the affected plan subgraph."""
    semantic_roots = {
        path
        for path in changed
        if path
        and (
            path[0]
            in {
                "outcomes",
                "evidence",
                "decisions",
                "work",
                "proof",
                "projections",
                "baseline",
                "design_join",
            }
            or (
                path[0] == "git"
                and len(path) > 1
                and path[1] in {"target", "lanes", "joins"}
            )
        )
    }
    if not semantic_roots:
        return
    outcome_ids = {
        path[1]
        for path in semantic_roots
        if path[0] == "outcomes" and len(path) > 1
    }
    evidence_ids = {path[1] for path in semantic_roots if path[0] == "evidence" and len(path) > 1}
    decision_ids = {path[1] for path in semantic_roots if path[0] == "decisions" and len(path) > 1}
    work_ids = {path[1] for path in semantic_roots if path[0] == "work" and len(path) > 1}
    proof_ids = {path[1] for path in semantic_roots if path[0] == "proof" and len(path) > 1 and not (len(path) > 2 and path[2] == "evidence")}
    projection_ids = {
        path[1]
        for path in semantic_roots
        if path[0] == "projections"
        and len(path) > 1
        # Revoking confirmation alone is safe and should merely return the
        # current projection to awaiting-user.  Any change to its meaning or
        # presentation must instead stale and version the projection.
        and not (len(path) > 2 and path[2] == "confirmed")
    }
    if "git" in {path[0] for path in semantic_roots} or "baseline" in {path[0] for path in semantic_roots}:
        evidence_ids.update(graph["evidence"])
    decisions = graph["decisions"]
    work = graph["work"]
    proof = graph["proof"]
    changed_again = True
    while changed_again:
        changed_again = False
        affected = outcome_ids | evidence_ids | decision_ids | work_ids | proof_ids | projection_ids
        edges = [(record["source"], record["targets"]) for record in graph["invalidations"]]
        edges.extend((decision_id, record["invalidates"]) for decision_id, record in decisions.items())
        families = (
            (graph["evidence"], evidence_ids), (decisions, decision_ids),
            (work, work_ids), (proof, proof_ids), (graph["projections"], projection_ids),
        )
        for source, targets in edges:
            if source in affected:
                for target in targets:
                    for family, affected_ids in families:
                        if target in family and target not in affected_ids:
                            affected_ids.add(target)
                            changed_again = True
        for decision_id, record in decisions.items():
            if decision_id not in decision_ids and set(record["based_on"]) & evidence_ids:
                decision_ids.add(decision_id); changed_again = True
        for work_id, record in work.items():
            if work_id not in work_ids and (
                set(record["covers"]) & outcome_ids
                or set(record["based_on"]) & evidence_ids
                or set(record["decisions"]) & decision_ids
                or set(record["requires"]) & work_ids
            ):
                work_ids.add(work_id); changed_again = True
        for work_id in tuple(work_ids):
            for decision_id in work.get(work_id, {}).get("decisions", []):
                if decision_id not in decision_ids:
                    decision_ids.add(decision_id); changed_again = True
        for proof_id, record in proof.items():
            if proof_id not in proof_ids and (
                set(record["covers"]) & outcome_ids
                or set(record["required_by"]) & work_ids
            ):
                proof_ids.add(proof_id); changed_again = True
        affected = outcome_ids | evidence_ids | decision_ids | work_ids | proof_ids
        for projection_id, record in graph["projections"].items():
            if projection_id not in projection_ids and set(record["covers"]) & affected:
                projection_ids.add(projection_id)
                changed_again = True
    preserved_evidence = preserve_evidence or set()
    for evidence_id in evidence_ids:
        if evidence_id in graph["evidence"]:
            record = graph["evidence"][evidence_id]
            if evidence_id in preserved_evidence:
                continue
            if record["fresh"]:
                record["record_version"] += 1
            record["fresh"] = False
            record["operation_receipt"] = None
    for decision_id in decision_ids:
        if decision_id in decisions:
            record = decisions[decision_id]
            if not record["stale"]:
                record["version"] += 1
            record["stale"] = True
            record["operation_receipt"] = None
    for proof_id in proof_ids:
        if proof_id in proof:
            record = proof[proof_id]
            if record["fresh"]:
                record["record_version"] += 1
            record["fresh"] = False
            record["operation_receipt"] = None
    affected = outcome_ids | evidence_ids | decision_ids | work_ids | proof_ids
    for projection_id, projection in graph["projections"].items():
        if projection_id in projection_ids or set(projection["covers"]) & affected:
            if not projection["stale"]:
                projection["version"] += 1
            projection["stale"] = True
            projection["confirmed"] = False
            projection["operation_receipt"] = None
    if semantic_roots and not all(path[0] == "proof" and len(path) > 2 and path[2] == "evidence" for path in semantic_roots):
        if graph["audit"]["fresh"]:
            graph["audit"]["record_version"] += 1
        graph["audit"]["fresh"] = False
        graph["audit"]["independent"] = False
        graph["audit"]["operation_receipt"] = None
    meaning_changed = any(
        not (
            path[0] == "proof"
            and len(path) > 2
            and path[2] == "evidence"
        )
        and not (
            path[0] == "projections"
            and len(path) > 2
            and path[2] == "confirmed"
        )
        for path in semantic_roots
    )
    design_join = graph.get("design_join")
    if meaning_changed and isinstance(design_join, dict) and design_join.get("required"):
        if design_join.get("fresh"):
            design_join["record_version"] += 1
        design_join["receipt"] = None
        design_join["fresh"] = False
        design_join["operation_receipt"] = None


def _apply_updates_locked(
    transaction: _Transaction,
    workflow_id: str,
    expected_revision: int,
    updates: object,
    *,
    reconcile_disjoint: bool,
) -> Receipt:
    if not isinstance(workflow_id, str) or not re.fullmatch(r"[0-9a-f]{32}", workflow_id):
        raise PlanGraphError("invalid workflow identity")
    _integer(expected_revision, "expected graph revision", minimum=1)
    normalized, paths = _validated_updates(updates)
    current = _read_current(transaction)
    if current["workflow_id"] != workflow_id:
        raise PlanGraphError("workflow identity mismatch")
    actual_previous_revision = current["graph_revision"]
    reconciled = False
    if actual_previous_revision != expected_revision:
        if not reconcile_disjoint or actual_previous_revision != expected_revision + 1:
            raise RevisionConflict("graph revision conflict")
        try:
            base = _read_previous(transaction)
        except PlanGraphError as error:
            raise RevisionConflict("immediate prior generation is unavailable") from error
        if base["graph_revision"] != expected_revision or base["workflow_id"] != workflow_id:
            raise RevisionConflict("stale base is not the immediate prior generation")
        semantic_changes = {
            path
            for path in _changed_paths(base, current)
            if path and path[0] not in {"graph_revision", "lifecycle", "projections", "audit"}
        }
        if any(_paths_overlap(path, changed) for path in paths for changed in semantic_changes):
            raise RevisionConflict("stale update overlaps a semantic change")
        reconciled = True

    candidate = copy.deepcopy(current)
    _apply_to_graph(candidate, normalized)
    for proof_id, record in candidate["proof"].items():
        previous_record = current["proof"].get(proof_id, {})
        was_executed = previous_record.get("execution_required", bool(previous_record.get("evidence")))
        if was_executed and record.get("execution_required", True) is not True:
            raise PlanGraphError("proof execution requirement cannot be downgraded")
        if was_executed or record.get("evidence"):
            record["execution_required"] = True
    if current["audit"]["required"] and not candidate["audit"]["required"]:
        raise PlanGraphError("required audit cannot be downgraded")
    if current["design_join"]["required"] and not candidate["design_join"]["required"]:
        raise PlanGraphError("required Design join cannot be downgraded")
    for signal in ("breadth", "complexity", "high_consequence"):
        if current["audit"][signal] and not candidate["audit"][signal]:
            raise PlanGraphError("audit consequence signals cannot be downgraded")
    typed_families = [
        (tuple(update["path"]), update["_typed"])
        for update in normalized
        if update.get("_typed")
    ]
    evidence_meaning = {"fact", "source", "revision", "version", "limitations", "supports"}
    semantic_changes: set[tuple[str, ...]] = set()
    preserved_evidence: set[str] = set()
    for path in _changed_paths(current, candidate):
        if len(path) == 3 and path[0] == "proof" and path[2] == "execution_required":
            continue
        operation = next(
            (
                typed_operation
                for family, typed_operation in typed_families
                if path[:len(family)] == family
            ),
            None,
        )
        if operation is None:
            semantic_changes.add(path)
        elif operation == "refresh-evidence" and len(path) > 2 and path[2] in evidence_meaning:
            semantic_changes.add(path)
            preserved_evidence.add(path[1])
    _invalidate_semantic_dependents(
        candidate,
        semantic_changes,
        preserve_evidence=preserved_evidence,
    )
    # Structural semantics impose a floor even when a caller supplies false
    # booleans.  Escalation is retained; lowering is rejected below.
    floor_breadth, floor_complexity, floor_consequence, floor_reason = _semantic_audit_floor(
        candidate
    )
    audit = candidate["audit"]
    audit["breadth"] = audit["breadth"] or floor_breadth
    audit["complexity"] = audit["complexity"] or floor_complexity
    audit["high_consequence"] = audit["high_consequence"] or floor_consequence
    audit["required"] = (
        audit["breadth"] or audit["complexity"] or audit["high_consequence"]
    )
    audit["classification"] = _audit_classification(
        audit["breadth"], audit["complexity"], audit["high_consequence"]
    )
    typed_audit_refresh = any(
        update.get("_typed") in {"refresh-audit", "resolve-finding"}
        for update in normalized
    )
    if (
        audit["required"]
        and not current["audit"]["required"]
        and not typed_audit_refresh
    ):
        audit["fresh"] = False
        audit["independent"] = False
        audit["operation_receipt"] = None
        audit["reason"] = floor_reason
    _validate_typed_repairs(
        current, candidate, normalized, actual_previous_revision
    )
    # A newly attached execution receipt is produced against the exact graph
    # generation the worker consumed.  Older already-attached receipts remain
    # valid records across later disjoint graph revisions.
    for proof_id, proof_record in candidate["proof"].items():
        evidence_path = ("proof", proof_id, "evidence")
        if any(_paths_overlap(path, evidence_path) for path in paths):
            for evidence_receipt in proof_record.get("evidence", []):
                if (
                    not isinstance(evidence_receipt, dict)
                    or evidence_receipt.get("graph_revision") != actual_previous_revision
                    or evidence_receipt.get("commit") != transaction.context.head
                ):
                    raise PlanGraphError(
                        "new evidence receipt is not bound to the exact prior graph revision and commit"
                    )
    candidate["graph_revision"] = actual_previous_revision + 1
    _finalize_graph(
        candidate,
        transaction.context,
        _transaction_provenance_resolver(transaction),
    )
    _rotate(transaction, current, candidate)
    return _receipt(
        candidate,
        transaction,
        previous_revision=actual_previous_revision,
        applied_paths=paths,
        reconciled=reconciled,
    )


def apply_updates(
    repo: Path,
    branch: str,
    workflow_id: str,
    expected_revision: int,
    updates: list[dict[str, Any]],
    state_home: Path | None = None,
    reconcile_disjoint: bool = False,
) -> Receipt:
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        return _apply_updates_locked(
            transaction,
            workflow_id,
            expected_revision,
            updates,
            reconcile_disjoint=reconcile_disjoint,
        )


def recover_workflow(
    repo: Path,
    branch: str,
    workflow_id: str,
    state_home: Path | None = None,
) -> Receipt:
    with _transaction(
        repo,
        branch,
        state_home,
        require_workflow=True,
        allow_corrupt_current=True,
    ) as transaction:
        if transaction.work_fd is None:
            raise PlanGraphError("workflow not found")
        entries = set(os.listdir(transaction.work_fd))
        if not entries <= {"current.yaml", "previous.yaml"} or "previous.yaml" not in entries:
            raise PlanGraphError("no unambiguous previous generation is available")
        previous = _read_previous(transaction)
        if previous["workflow_id"] != workflow_id:
            raise PlanGraphError("workflow identity mismatch")
        if "current.yaml" not in entries:
            current = None
        else:
            try:
                current = _read_current(transaction)
            except _CorruptGraph:
                current = None
        if current is not None:
            raise PlanGraphError("current generation is valid; recovery is not applicable")
        recovered = copy.deepcopy(previous)
        recovered["graph_revision"] = previous["graph_revision"] + 2
        _finalize_graph(
            recovered,
            transaction.context,
            _transaction_provenance_resolver(transaction),
        )
        _atomic_write_entry(transaction.work_fd, "current.yaml", recovered)
        _revalidate_directory(transaction.root_fd, transaction.key, transaction.work_fd)
        return _receipt(
            recovered,
            transaction,
            previous_revision=previous["graph_revision"],
            applied_paths=(),
            reconciled=False,
        )


def pause_workflow(
    repo: Path,
    branch: str,
    workflow_id: str,
    revision: int,
    state_home: Path | None = None,
) -> Receipt:
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        return _apply_updates_locked(
            transaction,
            workflow_id,
            revision,
            [{"op": "set", "path": ["lifecycle", "state"], "value": "paused"}],
            reconcile_disjoint=False,
        )


def _changed_repository_paths(context: _RepoContext, baseline: str) -> set[str]:
    if not _is_full_commit(context, baseline):
        raise PlanGraphError("stored baseline is not a repository commit")
    process = subprocess.run(
        ["git", "diff", "--name-only", "-z", baseline, context.head, "--"],
        cwd=context.repository,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if process.returncode:
        raise PlanGraphError("cannot compare repository freshness")
    changed = {item.decode("utf-8", "surrogateescape") for item in process.stdout.split(b"\0") if item}
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=context.repository,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if status.returncode:
        raise PlanGraphError("cannot inspect repository freshness")
    for row in status.stdout.split(b"\0"):
        if len(row) >= 4:
            changed.add(row[3:].decode("utf-8", "surrogateescape"))
    return changed


def _path_related(source: str, changed: str) -> bool:
    source_path = source.strip("/")
    changed_path = changed.strip("/")
    return (
        source_path == changed_path
        or source_path.startswith(changed_path + "/")
        or changed_path.startswith(source_path + "/")
    )


def resume_workflow(
    repo: Path,
    branch: str,
    workflow_id: str,
    revision: int,
    state_home: Path | None = None,
) -> Receipt:
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        _integer(revision, "expected graph revision", minimum=1)
        current = _read_current(transaction)
        if current["workflow_id"] != workflow_id:
            raise PlanGraphError("workflow identity mismatch")
        if current["graph_revision"] != revision:
            raise RevisionConflict("graph revision conflict")
        if current["lifecycle"]["state"] != "paused":
            raise PlanGraphError("workflow is not paused")
        candidate = copy.deepcopy(current)
        candidate["lifecycle"]["state"] = None
        changed = _changed_repository_paths(
            transaction.context, candidate["baseline"]["repository_revision"]
        )
        stale_evidence = {
            evidence_id
            for evidence_id, evidence in candidate["evidence"].items()
            if evidence["kind"] == "repository"
            and any(_path_related(evidence["source"], path) for path in changed)
        }
        _invalidate_semantic_dependents(
            candidate,
            {("evidence", evidence_id, "revision") for evidence_id in stale_evidence},
        )
        candidate["graph_revision"] = current["graph_revision"] + 1
        _finalize_graph(
            candidate,
            transaction.context,
            _transaction_provenance_resolver(transaction),
        )
        _rotate(transaction, current, candidate)
        return _receipt(
            candidate,
            transaction,
            previous_revision=current["graph_revision"],
            applied_paths=(("lifecycle", "state"),),
        )


def _validate_workspace_for_delete(transaction: _Transaction) -> tuple[dict[str, Any], dict[str, Any] | None]:
    current = _read_current(transaction)
    entries = set(os.listdir(transaction.work_fd)) if transaction.work_fd is not None else set()
    if not entries <= {"current.yaml", "previous.yaml"}:
        raise _UnsafeState("workflow contains unknown state entries")
    previous = _read_previous(transaction) if "previous.yaml" in entries else None
    return current, previous


def _delete_workspace(transaction: _Transaction) -> None:
    if transaction.work_fd is None:
        raise PlanGraphError("workflow not found")
    current, previous = _validate_workspace_for_delete(transaction)
    tomb = f"{transaction.key}.deleting"
    try:
        os.stat(tomb, dir_fd=transaction.root_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise _UnsafeState("an ambiguous deletion tomb already exists")
    try:
        os.rename(
            transaction.key,
            tomb,
            src_dir_fd=transaction.root_fd,
            dst_dir_fd=transaction.root_fd,
        )
        os.fsync(transaction.root_fd)
    except OSError as error:
        # The rename syscall may have succeeded even when a wrapper reports a
        # later failure.  Restore only when the lexical tomb is still the exact
        # descriptor-owned workspace; never move a substituted name.
        try:
            active = os.stat(
                transaction.key,
                dir_fd=transaction.root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            try:
                tomb_stat = os.stat(
                    tomb, dir_fd=transaction.root_fd, follow_symlinks=False
                )
            except FileNotFoundError:
                pass
            else:
                if _same_inode(tomb_stat, os.fstat(transaction.work_fd)):
                    try:
                        os.rename(
                            tomb,
                            transaction.key,
                            src_dir_fd=transaction.root_fd,
                            dst_dir_fd=transaction.root_fd,
                        )
                        os.fsync(transaction.root_fd)
                    except OSError:
                        pass
        else:
            if not _same_inode(active, os.fstat(transaction.work_fd)):
                raise PlanGraphError(
                    "workflow deletion entry was substituted; foreign state preserved"
                ) from error
        raise PlanGraphError("cannot begin safe workflow deletion") from error
    tomb_fd: int | None = None
    try:
        # Keep the descriptor identity across the rename.  The name is in an
        # attacker-controlled namespace and must not be trusted for deletion.
        _revalidate_directory(transaction.root_fd, tomb, transaction.work_fd)
        tomb_fd = os.open(
            tomb,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=transaction.root_fd,
        )
        if not _same_inode(os.fstat(tomb_fd), os.fstat(transaction.work_fd)):
            raise _UnsafeState("deletion tomb identity changed")
        if previous is not None:
            _revalidate_directory(transaction.root_fd, tomb, transaction.work_fd)
            os.unlink("previous.yaml", dir_fd=tomb_fd)
        _revalidate_directory(transaction.root_fd, tomb, transaction.work_fd)
        os.unlink("current.yaml", dir_fd=tomb_fd)
        _revalidate_directory(transaction.root_fd, tomb, tomb_fd)
        os.fsync(transaction.work_fd)
        _revalidate_directory(transaction.root_fd, tomb, transaction.work_fd)
        os.rmdir(tomb, dir_fd=transaction.root_fd)
        os.fsync(transaction.root_fd)
    except (OSError, PlanGraphError) as error:
        # Restore an exact recoverable active graph before reporting deletion failure.
        try:
            entries = set(os.listdir(transaction.work_fd))
            if "current.yaml" not in entries:
                _atomic_write_entry(transaction.work_fd, "current.yaml", current)
            if previous is not None and "previous.yaml" not in entries:
                _atomic_write_entry(transaction.work_fd, "previous.yaml", previous)
            try:
                active_stat = os.stat(
                    transaction.key,
                    dir_fd=transaction.root_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                try:
                    tomb_stat = os.stat(
                        tomb,
                        dir_fd=transaction.root_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as missing_tomb:
                    raise PlanGraphError(
                        "workflow deletion failed after namespace removal"
                    ) from missing_tomb
                if not _same_inode(tomb_stat, os.fstat(transaction.work_fd)):
                    raise PlanGraphError(
                        "workflow deletion tomb was substituted; foreign state preserved"
                    )
                os.rename(
                    tomb,
                    transaction.key,
                    src_dir_fd=transaction.root_fd,
                    dst_dir_fd=transaction.root_fd,
                )
                os.fsync(transaction.root_fd)
            else:
                if not _same_inode(active_stat, os.fstat(transaction.work_fd)):
                    raise PlanGraphError(
                        "workflow active name was substituted; foreign state preserved"
                    )
        except OSError as restore_error:
            raise PlanGraphError("workflow deletion failed and recovery could not be restored") from restore_error
        raise PlanGraphError("workflow deletion failed; recoverable state was restored") from error
    finally:
        if tomb_fd is not None:
            os.close(tomb_fd)


def discard_workflow(
    repo: Path,
    branch: str,
    workflow_id: str,
    revision: int,
    explicit: bool,
    state_home: Path | None = None,
) -> None:
    if explicit is not True:
        raise PlanGraphError("discard requires explicit confirmation")
    with _transaction(repo, branch, state_home, require_workflow=True) as transaction:
        current = _read_current(transaction)
        if current["workflow_id"] != workflow_id or current["graph_revision"] != revision:
            raise PlanGraphError("workflow identity or revision mismatch")
        _delete_workspace(transaction)


def _validate_delivery(graph: dict[str, Any], context: _RepoContext | None, provenance_resolver: Any = None) -> None:
    git = _mapping(graph.get("git"), "Git topology")
    delivery = _mapping(git.get("delivery"), "delivery")
    state = delivery.get("state")
    if state == "planning":
        _only(delivery, {"state"}, "planning delivery")
        return
    if state != "draft":
        raise PlanGraphError("invalid delivery state")
    if provenance_resolver is None:
        raise PlanGraphError(
            "draft delivery requires transaction-owned provenance resolution"
        )
    _only(
        delivery,
        {
            "state",
            "request",
            "first_coherent_commit",
            "exact_head",
            "gates",
            "checks",
            "checks_policy",
            "lanes_clean",
            "handoff",
        },
        "draft delivery",
    )
    head = delivery.get("exact_head")
    first = delivery.get("first_coherent_commit")
    if context is not None:
        if not _is_full_commit(context, head) or not _is_full_commit(context, first):
            raise PlanGraphError("delivery commits are not full repository commits")
    else:
        _text(head, "delivery head")
        _text(first, "first coherent commit")
    request = _mapping(delivery.get("request"), "delivery request")
    if not {"id", "draft", "head"} <= set(request):
        raise PlanGraphError("delivery request binding is incomplete")
    _text(request.get("id"), "request id")
    if request.get("draft") is not True or request.get("head") != head:
        raise PlanGraphError("delivery request is not a draft at the exact head")
    gates = _mapping(delivery.get("gates"), "delivery gates", allow_empty=False)
    required_roles = {"implementation", "review", "verification", "target_proof"}
    if set(gates) != required_roles:
        raise PlanGraphError("delivery gates must contain exactly the required roles")
    role_names = {
        "implementation": "implement",
        "review": "review",
        "verification": "verify",
        "target_proof": "integrate",
    }
    executable_work = {work_id for work_id, item in graph["work"].items() if item["kind"] != "join"}
    all_proof = set(graph["proof"])
    covered_roles: dict[str, tuple[set[str], set[str]]] = {}
    receipt_ids: set[str] = set()
    for gate_name, gate in gates.items():
        record = _mapping(gate, "delivery gate")
        role_fields = {
            "implementation": {"implementation_evidence"},
            "review": {"disposition", "findings"},
            "verification": {"independent"},
            "target_proof": {"integrated_proof"} | ({"join"} if graph["git"].get("joins") else set()),
        }
        provenance_fields = {"provenance_id", "provenance_digest", "provenance_session",
                             "provenance_source", "raw_evidence_digest"}
        allowed = {"role", "receipt_id", "digest",
                   "workflow_id", "graph_revision", "branch", "commit", "work", "proof",
                   "commands", "results", "result"} | role_fields[gate_name] | provenance_fields
        _only(record, allowed, f"{gate_name} delivery gate")
        required_fields = {"role", "receipt_id", "digest",
                           "workflow_id", "graph_revision", "branch", "commit", "work", "proof",
                           "commands", "results", "result"} | role_fields[gate_name] | provenance_fields
        if set(record) != required_fields:
            raise PlanGraphError(f"{gate_name} receipt schema is incomplete")
        _text(record.get("receipt_id"), f"{gate_name} receipt id")
        provenance_id = _identifier(
            record.get("provenance_id"), f"{gate_name} provenance id"
        )
        provenance = provenance_resolver(provenance_id)
        if (
            provenance.get("workflow_id") != graph["workflow_id"]
            or provenance.get("graph_revision") != record.get("graph_revision")
            or provenance.get("role") != record["role"]
            or provenance.get("session_id") != record.get("provenance_session")
            or provenance.get("source") != record.get("provenance_source")
            or provenance.get("raw_evidence_digest") != record.get("raw_evidence_digest")
        ):
            raise PlanGraphError("delivery provenance binding mismatch")
        if record.get("provenance_digest") != provenance.get("digest"):
            raise PlanGraphError("delivery provenance digest mismatch")
        if record["receipt_id"] in receipt_ids:
            raise PlanGraphError("delivery receipt IDs must be unique across roles")
        receipt_ids.add(record["receipt_id"])
        digest = record.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PlanGraphError(f"{gate_name} receipt digest is invalid")
        unsigned = {key: value for key, value in record.items() if key != "digest"}
        expected_digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if digest != expected_digest:
            raise PlanGraphError(f"{gate_name} receipt digest mismatch")
        if record.get("workflow_id") != graph["workflow_id"]:
            raise PlanGraphError("delivery gate workflow mismatch")
        gate_revision = _integer(
            record.get("graph_revision"), "delivery gate graph revision", minimum=1
        )
        if gate_revision >= graph["graph_revision"]:
            raise PlanGraphError("delivery gate revision mismatch")
        if record.get("branch") != graph["identity"]["target_branch"]:
            raise PlanGraphError("delivery gate branch mismatch")
        if record.get("commit") != head:
            raise PlanGraphError("delivery gate head mismatch")
        if record.get("role") != role_names[gate_name]:
            raise PlanGraphError("delivery gate role is swapped or fabricated")
        if gate_name == "verification" and record.get("independent") is not True:
            raise PlanGraphError("verification receipt is not independent")
        if gate_name == "review":
            if record.get("disposition") not in {"approve", "request-changes"}:
                raise PlanGraphError("review disposition is invalid")
            _sequence(record.get("findings"), "review findings")
        if gate_name == "implementation":
            _text(record.get("implementation_evidence"), "implementation evidence")
        if gate_name == "target_proof":
            _text(record.get("integrated_proof"), "integrated target proof")
        work_ids = _id_list(record.get("work"), "delivery gate work", allow_empty=False)
        proof_ids = _id_list(record.get("proof"), "delivery gate proof", allow_empty=False)
        covered_roles[gate_name] = (set(work_ids), set(proof_ids))
        if not set(work_ids) <= set(graph["work"]):
            raise PlanGraphError("delivery gate references unknown work")
        if not set(proof_ids) <= set(graph["proof"]):
            raise PlanGraphError("delivery gate references unknown proof")
        if not any(set(graph["work"][item]["proof"]) & set(proof_ids) for item in work_ids):
            raise PlanGraphError("delivery gate proof is unrelated to its work")
        if gate_name == "target_proof":
            if graph["git"].get("joins"):
                join = _identifier(record.get("join"), "target proof join")
                if join not in graph["work"] or graph["work"][join].get("kind") != "join":
                    raise PlanGraphError("target proof must bind a graph join")
                if join not in work_ids or not any(join in graph["proof"][item].get("required_by", []) for item in proof_ids):
                    raise PlanGraphError("target proof join evidence is unrelated")
        elif "join" in record:
            raise PlanGraphError("only target proof may bind a join")
        commands = _text_list(record.get("commands"), "delivery gate commands", allow_empty=False)
        results = _sequence(record.get("results"), "delivery gate results", allow_empty=False)
        if len(commands) != len(results):
            raise PlanGraphError("delivery gate commands and results differ")
        for command, result in zip(commands, results):
            result_record = _mapping(result, "delivery gate result evidence")
            _only(result_record, {"command", "exit_code", "output"}, "delivery gate result evidence")
            if result_record.get("command") != command or result_record.get("exit_code") != 0:
                raise PlanGraphError("delivery gate command did not pass exactly")
            _text(result_record.get("output"), "delivery gate command output")
        result = _mapping(record.get("result"), "delivery gate result")
        if result.get("status") not in {"pass", "pending", "fail"}:
            raise PlanGraphError("invalid delivery gate result")
    provenance_records = [
        provenance_resolver(gate["provenance_id"]) for gate in gates.values()
    ]
    if len({item["receipt_id"] for item in provenance_records}) != len(required_roles):
        raise PlanGraphError("delivery provenance records must be distinct")
    if len({item["session_id"] for item in provenance_records}) != len(required_roles):
        raise PlanGraphError("delivery provenance sessions must be distinct")
    if len({item["raw_evidence_digest"] for item in provenance_records}) != len(required_roles):
        raise PlanGraphError("delivery raw-evidence records must be distinct")
    implementation_work, implementation_proof = covered_roles["implementation"]
    if implementation_work != executable_work or not all_proof >= implementation_proof:
        raise PlanGraphError("implementation receipt coverage is incomplete")
    review_work, review_proof = covered_roles["review"]
    if review_work != executable_work or review_proof != all_proof:
        raise PlanGraphError("review receipt coverage is incomplete")
    verify_work, verify_proof = covered_roles["verification"]
    if verify_work != executable_work or verify_proof != all_proof:
        raise PlanGraphError("verification receipt coverage is incomplete")
    target_work, target_proof = covered_roles["target_proof"]
    joins = {work_id for work_id, item in graph["work"].items() if item["kind"] == "join"}
    if joins:
        if target_work != joins or target_proof != {proof_id for proof_id, item in graph["proof"].items() if any(graph["work"][wid]["kind"] == "join" for wid in item["required_by"])}:
            raise PlanGraphError("target proof receipt join coverage is incomplete")
    elif "join" in gates["target_proof"]:
        raise PlanGraphError("target proof cannot bind a join in a graph without joins")
    policy = delivery.get("checks_policy")
    policy = _mapping(policy, "delivery check policy")
    _only(policy, {"provider", "source", "request_id", "workflow_id", "graph_revision",
                   "branch", "head", "observed_at", "authoritative", "discovered_names",
                   "provenance_id", "provenance_digest", "provenance_session",
                   "raw_evidence_digest"}, "delivery check policy")
    if set(policy) != {"provider", "source", "request_id", "workflow_id", "graph_revision",
                       "branch", "head", "observed_at", "authoritative", "discovered_names",
                       "provenance_id", "provenance_digest", "provenance_session",
                       "raw_evidence_digest"}:
        raise PlanGraphError("checks policy receipt is incomplete")
    _text(policy.get("provider"), "checks provider")
    if policy["provider"] not in {"local", "github-actions", "github"}:
        raise PlanGraphError("checks provider is not trusted")
    _text(policy.get("source"), "checks source")
    _text(policy.get("request_id"), "checks request id")
    if policy.get("request_id") != request["id"] or policy.get("workflow_id") != graph["workflow_id"]:
        raise PlanGraphError("checks policy identity mismatch")
    if policy.get("branch") != graph["identity"]["target_branch"] or policy.get("head") != head:
        raise PlanGraphError("checks policy branch or head mismatch")
    if _integer(policy.get("graph_revision"), "checks policy revision", minimum=1) != graph["graph_revision"] - 1:
        raise PlanGraphError("checks policy must bind the immediate prior revision")
    _text(policy.get("observed_at"), "checks observed timestamp")
    if policy.get("authoritative") is not True:
        raise PlanGraphError("checks policy is not authoritative")
    policy_provenance = provenance_resolver(
        _identifier(policy.get("provenance_id"), "check-policy provenance id")
    )
    if (
        policy_provenance.get("workflow_id") != graph["workflow_id"]
        or policy_provenance.get("graph_revision") != policy.get("graph_revision")
        or policy_provenance.get("role") != "provider-policy"
        or policy_provenance.get("source") != policy.get("source")
        or policy_provenance.get("session_id") != policy.get("provenance_session")
        or policy_provenance.get("raw_evidence_digest") != policy.get("raw_evidence_digest")
        or policy.get("provenance_digest") != policy_provenance.get("digest")
        or policy_provenance.get("receipt_id")
        in {item["receipt_id"] for item in provenance_records}
    ):
        raise PlanGraphError("checks policy provenance binding mismatch")
    names = _text_list(policy.get("discovered_names"), "discovered check names")
    checks = _sequence(delivery.get("checks"), "delivery checks", allow_empty=not names)
    seen_checks: set[str] = set()
    for check in checks:
        record = _mapping(check, "delivery check")
        _only(record, {"run_id", "url", "name", "head", "conclusion", "status", "result"}, "delivery check")
        if record.get("head") != head or record.get("status") not in {"pass", "pending", "fail"}:
            raise PlanGraphError("delivery check is not bound to the exact head")
        _text(record.get("run_id"), "delivery check run id")
        _text(record.get("url"), "delivery check URL")
        _text(record.get("conclusion"), "delivery check conclusion")
        _text(record.get("result"), "delivery check result")
        _text(record.get("name"), "delivery check name")
        if record["name"] in seen_checks:
            raise PlanGraphError("duplicate delivery check")
        seen_checks.add(record["name"])
    if seen_checks != set(names):
        raise PlanGraphError("required exact-head checks are incomplete")
    _boolean(delivery.get("lanes_clean"), "lane cleanup state")
    handoff = _mapping(delivery.get("handoff"), "human-review handoff")
    if handoff:
        _text(handoff.get("title"), "handoff title")
        _text(handoff.get("summary"), "handoff summary")


def _terminal_directory(transaction: _Transaction) -> tuple[int, Path]:
    fd = _open_private_child(transaction.root_fd, "terminal", create=True)
    return fd, transaction.root_path / "terminal"


def _terminal_provenance_summary(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "provenance_id": gate["provenance_id"],
            "provenance_digest": gate["provenance_digest"],
        }
        for name, gate in delivery["gates"].items()
    } | {
        "checks_policy": {
            "provenance_id": delivery["checks_policy"]["provenance_id"],
            "provenance_digest": delivery["checks_policy"]["provenance_digest"],
        }
    }


def _validate_terminal_payload(payload: object, transaction: _Transaction, workflow_id: str, branch: str) -> dict[str, Any]:
    value = _mapping(payload, "terminal handoff")
    _only(value, {"schema_version", "workflow_id", "previous_revision", "current_revision",
                  "repository", "git_common_dir", "branch", "head", "request_id",
                  "handoff", "provenance", "completed_at", "state"}, "terminal handoff")
    if value.get("schema_version") != _TERMINAL_SCHEMA or value.get("workflow_id") != workflow_id:
        raise PlanGraphError("terminal handoff identity mismatch")
    if value.get("repository") != str(transaction.context.repository) or value.get("git_common_dir") != str(transaction.context.git_common_dir):
        raise PlanGraphError("terminal handoff repository identity mismatch")
    if value.get("branch") != branch or value.get("head") != transaction.context.head:
        raise PlanGraphError("terminal handoff branch or head mismatch")
    previous = _integer(value.get("previous_revision"), "terminal previous revision", minimum=1)
    current = _integer(value.get("current_revision"), "terminal current revision", minimum=2)
    if current != previous + 1:
        raise PlanGraphError("terminal revision sequence is invalid")
    _text(value.get("request_id"), "terminal request id")
    _mapping(value.get("handoff"), "terminal handoff details", allow_empty=False)
    provenance = _mapping(
        value.get("provenance"), "terminal provenance", allow_empty=False
    )
    role_names = {
        "implementation": "implement",
        "review": "review",
        "verification": "verify",
        "target_proof": "integrate",
        "checks_policy": "provider-policy",
    }
    if set(provenance) != set(role_names):
        raise PlanGraphError("terminal provenance role set is incomplete")
    resolver = _transaction_provenance_resolver(transaction)
    seen_ids: set[str] = set()
    for name, expected_role in role_names.items():
        reference = _mapping(provenance[name], "terminal provenance reference")
        if set(reference) != {"provenance_id", "provenance_digest"}:
            raise PlanGraphError("terminal provenance reference is incomplete")
        provenance_id = _identifier(
            reference.get("provenance_id"), "terminal provenance id"
        )
        stored = resolver(provenance_id)
        if (
            stored.get("workflow_id") != workflow_id
            or stored.get("graph_revision") != previous - 1
            or stored.get("role") != expected_role
            or stored.get("digest") != reference.get("provenance_digest")
            or provenance_id in seen_ids
        ):
            raise PlanGraphError("terminal provenance binding mismatch")
        seen_ids.add(provenance_id)
    _text(value.get("completed_at"), "terminal completion timestamp")
    if value.get("state") not in {"preparing-human-review", "ready-for-human-review"}:
        raise PlanGraphError("terminal handoff state is invalid")
    return value


def complete_for_human_review(
    repo: Path,
    branch: str,
    workflow_id: str,
    revision: int,
    state_home: Path | None = None,
) -> Receipt:
    if not isinstance(workflow_id, str) or not re.fullmatch(
        r"[0-9a-f]{32}", workflow_id
    ):
        raise PlanGraphError("invalid workflow identity")
    _integer(revision, "expected graph revision", minimum=1)
    with _transaction(repo, branch, state_home, require_workflow=False) as transaction:
        terminal_name = f"{transaction.key}-{workflow_id}.yaml"
        if transaction.work_fd is None:
            terminal_fd, terminal_root = _terminal_directory(transaction)
            try:
                payload = _validate_terminal_payload(_read_json_entry(terminal_fd, terminal_name), transaction, workflow_id, branch)
                if payload["previous_revision"] != revision:
                    raise PlanGraphError("terminal retry revision mismatch")
                if payload.get("state") == "preparing-human-review":
                    # This retry is also the durability barrier for a prior
                    # post-rmdir parent-fsync failure.  Do not publish ready
                    # until the absent workflow namespace is synchronized.
                    os.fsync(transaction.root_fd)
                    payload["state"] = "ready-for-human-review"
                    _atomic_write_entry(terminal_fd, terminal_name, payload)
                elif payload.get("state") != "ready-for-human-review":
                    raise PlanGraphError("terminal handoff is not recoverable")
                return Receipt(workflow_id, payload["current_revision"], terminal_root / terminal_name,
                               transaction.work_path / "previous.yaml", "ready-for-human-review",
                               payload.get("request_id"), payload.get("head"), (), False,
                               payload["previous_revision"], payload["current_revision"], (),
                               terminal_root / terminal_name)
            finally:
                os.close(terminal_fd)
        current = _read_current(transaction)
        if current["workflow_id"] != workflow_id or current["graph_revision"] != revision:
            raise PlanGraphError("workflow identity or revision mismatch")
        _validate_delivery(
            current,
            transaction.context,
            _transaction_provenance_resolver(transaction),
        )
        delivery = current["git"]["delivery"]
        if delivery.get("state") != "draft":
            raise PlanGraphError("delivery is not a draft")
        required_gates = {"implementation", "review", "verification", "target_proof"}
        if not required_gates <= set(delivery["gates"]):
            raise PlanGraphError("required AI gates are incomplete")
        if any(
            gate.get("graph_revision") != current["graph_revision"] - 1
            for gate in delivery["gates"].values()
        ):
            raise PlanGraphError("an AI gate is stale for the current graph revision")
        if any(gate["result"].get("status") != "pass" for gate in delivery["gates"].values()):
            raise PlanGraphError("an AI gate is not passing")
        if any(check.get("status") != "pass" for check in delivery["checks"]):
            raise PlanGraphError("a required exact-head check is not passing")
        if delivery["lanes_clean"] is not True or not delivery["handoff"]:
            raise PlanGraphError("lane cleanup or human handoff is incomplete")
        if delivery["exact_head"] != transaction.context.head:
            raise PlanGraphError("repository HEAD changed after delivery gates")

        terminal_fd, terminal_root = _terminal_directory(transaction)
        terminal_path = terminal_root / terminal_name
        try:
            try:
                os.stat(terminal_name, dir_fd=terminal_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                existing = _validate_terminal_payload(_read_json_entry(terminal_fd, terminal_name), transaction, workflow_id, branch)
                if (
                    existing["previous_revision"] != current["graph_revision"]
                    or existing["request_id"] != delivery["request"]["id"]
                    or existing["provenance"]
                    != _terminal_provenance_summary(delivery)
                ):
                    raise PlanGraphError(
                        "preparing terminal handoff does not match the active graph"
                    )
                if existing.get("state") == "preparing-human-review":
                    # Continue the interrupted transaction with the exact
                    # durable payload, rather than creating a second receipt.
                    _delete_workspace(transaction)
                    existing["state"] = "ready-for-human-review"
                    _atomic_write_entry(terminal_fd, terminal_name, existing)
                    return Receipt(workflow_id, existing["current_revision"], terminal_path,
                                   transaction.work_path / "previous.yaml", "ready-for-human-review",
                                   existing.get("request_id"), existing.get("head"), (), False,
                                   existing["previous_revision"], existing["current_revision"], (), terminal_path)
                raise PlanGraphError("terminal handoff already exists")
            terminal_revision = current["graph_revision"] + 1
            payload = {
                "schema_version": _TERMINAL_SCHEMA,
                "workflow_id": workflow_id,
                "previous_revision": current["graph_revision"],
                "current_revision": terminal_revision,
                "repository": str(transaction.context.repository),
                "git_common_dir": str(transaction.context.git_common_dir),
                "branch": branch,
                "head": delivery["exact_head"],
                "request_id": delivery["request"]["id"],
                "handoff": copy.deepcopy(delivery["handoff"]),
                "provenance": _terminal_provenance_summary(delivery),
                "completed_at": _now(),
                "state": "preparing-human-review",
            }
            _atomic_write_entry(terminal_fd, terminal_name, payload)
            try:
                _delete_workspace(transaction)
            except PlanGraphError:
                # The preparing receipt is the recovery authority once
                # deletion has begun.  Never remove it after an indeterminate
                # filesystem failure: doing so can orphan both state and the
                # only durable record of the requested handoff.
                raise
            # Publish the success state only after the graph generations are
            # gone.  If this last durable replace fails, the preparing receipt
            # remains an honest, recoverable handoff instead of a false success.
            payload["state"] = "ready-for-human-review"
            _atomic_write_entry(terminal_fd, terminal_name, payload)
        finally:
            os.close(terminal_fd)

        current_path = transaction.work_path / "current.yaml"
        previous_path = transaction.work_path / "previous.yaml"
        return Receipt(
            workflow_id,
            terminal_revision,
            terminal_path,
            previous_path,
            "ready-for-human-review",
            delivery["request"]["id"],
            delivery["exact_head"],
            (),
            False,
            current["graph_revision"],
            terminal_revision,
            (current_path, previous_path),
            terminal_path,
        )


def create_workflow_branch(
    repo: Path, branch: str, baseline: str, confirmed: bool
) -> BranchReceipt:
    repository = _repository(Path(repo))
    branch = _check_branch_name(repository, branch)
    if confirmed is not True:
        raise PlanGraphError("workflow branch creation requires explicit confirmation")
    if "/" not in branch:
        raise PlanGraphError("workflow branch must use a non-protected namespace")
    # Object-format and baseline validation do not depend on the current symbolic
    # branch; entry from a protected or detached HEAD is precisely the supported
    # use case for this narrow mutation.
    current_context = _repo_context(repository, branch, require_current=False)
    if not _is_full_commit(current_context, baseline):
        raise PlanGraphError("baseline must be a full repository commit")
    existing = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repository,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if existing.returncode == 0:
        raise PlanGraphError("workflow branch already exists")
    if existing.returncode not in {0, 1}:
        raise PlanGraphError("cannot verify workflow branch uniqueness")
    process = subprocess.run(
        ["git", "switch", "--no-track", "-c", branch, baseline],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if process.returncode:
        raise PlanGraphError(process.stderr.strip() or "workflow branch creation failed")
    if _run_git(repository, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        raise PlanGraphError("workflow branch switch could not be verified")
    if _run_git(repository, "rev-parse", "--verify", "HEAD") != baseline:
        raise PlanGraphError("workflow branch baseline could not be verified")
    return BranchReceipt(branch, baseline)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operate a private Plan Graph from the installed plugin package"
    )
    parser.add_argument("command", choices=("initialize", "discover", "load", "apply", "recover", "state", "pause", "resume", "discard", "create-branch", "complete"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--state-home", type=Path, default=None)
    parser.add_argument("--workflow-id")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--baseline")
    parser.add_argument("--json")
    args = parser.parse_args(argv)
    context = None if args.command == "create-branch" else _repo_context(args.repo, args.branch)

    def graph_digest(graph: dict[str, Any] | None) -> str | None:
        return _canonical_digest(graph) if graph is not None else None

    def envelope(
        operation: str,
        *,
        workflow_id: str,
        previous_revision: int,
        current_revision: int,
        before_state: str,
        after_state: str,
        before_graph: dict[str, Any] | None,
        after_graph: dict[str, Any] | None,
        head_commit: str,
        baseline_commit: str | None = None,
    ) -> dict[str, object]:
        if context is None:
            raise PlanGraphError("graph operation lacks repository context")
        value: dict[str, object] = {
            "schema_version": "plan-graph-cli-receipt.v1",
            "operation": operation,
            "repository": str(context.repository),
            "branch": context.branch,
            "workflow_id": workflow_id,
            "previous_revision": previous_revision,
            "current_revision": current_revision,
            "before_state": before_state,
            "after_state": after_state,
            "derived_state": after_state,
            "before_graph_digest": graph_digest(before_graph),
            "after_graph_digest": graph_digest(after_graph),
            "head_commit": head_commit,
        }
        if baseline_commit is not None:
            value["baseline_commit"] = baseline_commit
        return value

    def emit(value: dict[str, object]) -> int:
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0
    def input_json() -> object:
        raw = args.json if args.json is not None else __import__("sys").stdin.read(MAX_GRAPH_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_GRAPH_BYTES:
            raise PlanGraphError("JSON input is oversized")
        try:
            return json.loads(raw, object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError) as error:
            raise PlanGraphError("CLI JSON input is not strict JSON") from error

    if args.command == "initialize":
        receipt = initialize_workflow(args.repo, args.branch, input_json(), args.state_home)  # type: ignore[arg-type]
        graph = load_workflow(args.repo, args.branch, args.state_home)
        return emit(envelope(
            args.command, workflow_id=receipt.workflow_id, previous_revision=0,
            current_revision=receipt.revision, before_state="absent",
            after_state=graph["lifecycle"]["derived_state"], before_graph=None,
            after_graph=graph, head_commit=context.head,
            baseline_commit=graph["baseline"]["repository_revision"],
        ))
    if args.command == "apply":
        if not args.workflow_id or args.revision is None:
            parser.error("apply requires --workflow-id and --revision")
        updates = input_json()
        if not isinstance(updates, list):
            raise PlanGraphError("apply JSON must be a list")
        before = load_workflow(args.repo, args.branch, args.state_home)
        receipt = apply_updates(args.repo, args.branch, args.workflow_id, args.revision, updates, args.state_home)  # type: ignore[arg-type]
        graph = load_workflow(args.repo, args.branch, args.state_home)
        return emit(envelope(
            args.command, workflow_id=receipt.workflow_id,
            previous_revision=receipt.previous_revision or 0,
            current_revision=receipt.revision,
            before_state=before["lifecycle"]["derived_state"],
            after_state=graph["lifecycle"]["derived_state"], before_graph=before,
            after_graph=graph, head_commit=context.head,
        ))
    if args.command == "recover":
        if not args.workflow_id: parser.error("recover requires --workflow-id")
        receipt = recover_workflow(args.repo, args.branch, args.workflow_id, args.state_home)
        graph = load_workflow(args.repo, args.branch, args.state_home)
        return emit(envelope(
            args.command, workflow_id=receipt.workflow_id,
            previous_revision=receipt.previous_revision or 0,
            current_revision=receipt.revision, before_state="corrupt",
            after_state=graph["lifecycle"]["derived_state"], before_graph=None,
            after_graph=graph, head_commit=context.head,
        ))
    if args.command == "create-branch":
        if not args.baseline or not args.yes: parser.error("create-branch requires --baseline and --yes")
        result = create_workflow_branch(args.repo, args.branch, args.baseline, True)
        created_context = _repo_context(args.repo, result.branch)
        return emit({
            "schema_version": "plan-graph-cli-receipt.v1",
            "operation": args.command,
            "repository": str(created_context.repository),
            "branch": result.branch,
            "baseline_commit": result.commit,
            "head_commit": created_context.head,
        })
    if args.command == "complete":
        if not args.workflow_id or args.revision is None: parser.error("complete requires --workflow-id and --revision")
        before = load_workflow(args.repo, args.branch, args.state_home)
        receipt = complete_for_human_review(args.repo, args.branch, args.workflow_id, args.revision, args.state_home)
        return emit(envelope(
            args.command, workflow_id=receipt.workflow_id,
            previous_revision=receipt.previous_revision or 0,
            current_revision=receipt.revision,
            before_state=before["lifecycle"]["derived_state"], after_state=receipt.state,
            before_graph=before, after_graph=None,
            head_commit=receipt.head or context.head,
        ))
    if args.command == "discover":
        receipt = discover_workflow(args.repo, args.branch, args.state_home)
        graph = load_workflow(args.repo, args.branch, args.state_home)
        return emit(envelope(
            args.command, workflow_id=receipt.workflow_id,
            previous_revision=receipt.revision, current_revision=receipt.revision,
            before_state=graph["lifecycle"]["derived_state"],
            after_state=graph["lifecycle"]["derived_state"], before_graph=graph,
            after_graph=graph, head_commit=context.head,
        ))
    if args.command == "load":
        graph = load_workflow(args.repo, args.branch, args.state_home)
        return emit(envelope(
            args.command, workflow_id=graph["workflow_id"],
            previous_revision=graph["graph_revision"], current_revision=graph["graph_revision"],
            before_state=graph["lifecycle"]["derived_state"],
            after_state=graph["lifecycle"]["derived_state"], before_graph=graph,
            after_graph=graph, head_commit=context.head,
        ))
    if args.command == "state":
        graph = load_workflow(args.repo, args.branch, args.state_home)
        state = derive_plan_state(graph)
        return emit(envelope(
            args.command, workflow_id=graph["workflow_id"],
            previous_revision=graph["graph_revision"], current_revision=graph["graph_revision"],
            before_state=state, after_state=state, before_graph=graph,
            after_graph=graph, head_commit=context.head,
        ))
    if not args.workflow_id or args.revision is None:
        parser.error(f"{args.command} requires --workflow-id and --revision")
    before = load_workflow(args.repo, args.branch, args.state_home)
    if args.command == "pause":
        receipt = pause_workflow(args.repo, args.branch, args.workflow_id, args.revision, args.state_home)
    elif args.command == "resume":
        receipt = resume_workflow(args.repo, args.branch, args.workflow_id, args.revision, args.state_home)
    else:
        if not args.yes:
            parser.error("discard requires --yes")
        discard_workflow(args.repo, args.branch, args.workflow_id, args.revision, True, args.state_home)
        return emit(envelope(
            args.command, workflow_id=args.workflow_id,
            previous_revision=args.revision, current_revision=args.revision,
            before_state=before["lifecycle"]["derived_state"], after_state="absent",
            before_graph=before, after_graph=None, head_commit=context.head,
        ))
    graph = load_workflow(args.repo, args.branch, args.state_home)
    return emit(envelope(
        args.command, workflow_id=receipt.workflow_id,
        previous_revision=receipt.previous_revision or 0,
        current_revision=receipt.revision,
        before_state=before["lifecycle"]["derived_state"],
        after_state=graph["lifecycle"]["derived_state"], before_graph=before,
        after_graph=graph, head_commit=context.head,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
