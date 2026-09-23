"""Darwin API/capability simulation on Linux; this is not native macOS testing."""

import ctypes
import errno
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import install as module
from tests.test_install import (
    FakeResult, FakeRunner, install_results, marketplace_add_response,
    marketplace_list_response, plugin_list_response, receipt_path,
    removal_response, seed_repository,
    wait_for_crashed_child,
)


@pytest.fixture
def darwin_api():
    # Execute actual conditional operations, translating only Apple's ABI flags
    # to the host kernel. The installer cannot access Linux's symbol or helpers.
    native = ctypes.CDLL(None, use_errno=True).renameat2
    native.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    native.restype = ctypes.c_int
    calls = []

    def renameatx_np(source_fd, source, target_fd, target, flags):
        assert flags in {0x2, 0x4}
        calls.append(flags)
        return native(source_fd, source, target_fd, target, {0x2: 2, 0x4: 1}[flags])

    with mock.patch.object(module.sys, "platform", "darwin"), mock.patch.object(
        module.ctypes, "CDLL", return_value=SimpleNamespace(renameatx_np=renameatx_np)
    ), mock.patch.object(module.os, "O_TMPFILE", 0), mock.patch.object(
        module, "_link_open_descriptor", side_effect=AssertionError("Linux AT_EMPTY_PATH used")
    ):
        yield calls


@pytest.mark.parametrize("rollback", [False, True])
def test_darwin_codex_lifecycle(tmp_path: Path, darwin_api, rollback: bool):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    if rollback:
        runner = FakeRunner([
            marketplace_list_response(), marketplace_add_response(repo),
            plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
            removal_response(),
        ])
        with pytest.raises(module.InstallError, match="plugin add failed"):
            module.install(repo, home, state, runner)
        assert list((home / "agents").iterdir()) == []
    module.install(repo, home, state, FakeRunner(install_results(repo)))
    identities = {p: p.lstat().st_ino for p in (home / "agents").iterdir()}
    module.install(repo, home, state, FakeRunner(install_results(repo, True, True)))
    assert {p: p.lstat().st_ino for p in identities} == identities
    module.uninstall(repo, home, state, FakeRunner([
        plugin_list_response(repo), marketplace_list_response(repo),
        removal_response(), removal_response(),
    ]))
    assert list((home / "agents").iterdir()) == []
    assert not receipt_path(state).exists()
    assert set(darwin_api) == {0x2, 0x4}


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_missing_conditional_rename_rejected_before_install_mutation(tmp_path, platform):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    runner = FakeRunner([])
    with mock.patch.object(module.sys, "platform", platform), mock.patch.object(
        module.ctypes, "CDLL", return_value=SimpleNamespace()
    ), pytest.raises(module.InstallError, match="unavailable"):
        module.install(repo, home, state, runner, agents_only=True)
    assert runner.calls == []
    assert not home.exists(), "capability rejection occurred after profile mutation"
    assert not state.exists(), "capability rejection occurred after package mutation"


@pytest.mark.parametrize("operation", ["exclusive", "exchange", "hardlink"])
def test_darwin_missing_filesystem_capability_precedes_cli_mutation(tmp_path, darwin_api, operation):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    runner = FakeRunner([])
    native = module._conditional_rename_function("probe")
    def unsupported(source_fd, source, target_fd, target, flags):
        if (operation, flags) in {("exclusive", 4), ("exchange", 2)}:
            ctypes.set_errno(errno.EOPNOTSUPP)
            return -1
        return native(source_fd, source, target_fd, target, flags)
    with mock.patch.object(module.ctypes, "CDLL", return_value=SimpleNamespace(renameatx_np=unsupported)):
        if operation == "hardlink":
            with mock.patch.object(module.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")), pytest.raises(module.InstallError, match="unavailable"):
                module.install(repo, home, state, runner)
        else:
            with pytest.raises(module.InstallError, match="unavailable"):
                module.install(repo, home, state, runner)
    assert runner.calls == []
    assert not home.exists()
    assert not state.exists()


@pytest.mark.parametrize("anonymous", ["native", "missing-flag", "unsupported-filesystem", "missing-link"])
def test_linux_retirement_capabilities(tmp_path, anonymous):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    opening, linking = os.open, module._link_open_descriptor
    links = []
    def opened(path, flags, *args, **kwargs):
        if anonymous == "unsupported-filesystem" and flags & os.O_TMPFILE == os.O_TMPFILE:
            raise OSError(errno.EOPNOTSUPP, "filesystem lacks anonymous files")
        return opening(path, flags, *args, **kwargs)
    def linked(*args):
        links.append(args)
        if anonymous == "missing-link":
            raise module.InstallError("descriptor hard-link is unavailable")
        return linking(*args)
    with mock.patch.object(module.os, "O_TMPFILE", 0 if anonymous == "missing-flag" else os.O_TMPFILE), mock.patch.object(module.os, "open", opened), mock.patch.object(module, "_link_open_descriptor", linked):
        module.install(repo, home, state, FakeRunner([]), agents_only=True)
        module.uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert list((home / "agents").iterdir()) == []
    assert not receipt_path(state).exists()
    if anonymous == "native":
        assert links, "Linux stopped exercising anonymous descriptor publication"


def test_darwin_exclusive_rename_never_overwrites(tmp_path, darwin_api):
    (tmp_path / "source").write_text("owned")
    (tmp_path / "target").write_text("replacement")
    fd = os.open(tmp_path, module._directory_open_flags())
    try:
        with pytest.raises(OSError) as caught:
            module._renameat_noreplace(fd, "source", fd, "target")
        assert caught.value.errno == errno.EEXIST
        module._renameat_exchange(fd, "source", fd, "target")
    finally:
        os.close(fd)
    assert (tmp_path / "source").read_text() == "replacement"
    assert (tmp_path / "target").read_text() == "owned"


@pytest.mark.parametrize("boundary", ["created", "identified", "linked", "prepared", "exchanged"])
@pytest.mark.parametrize("rollback", [False, True])
def test_darwin_named_retirement_interruption(tmp_path, darwin_api, boundary, rollback):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    if not rollback:
        module.install(repo, home, state, FakeRunner([]), agents_only=True)
    pid = os.fork()
    if pid == 0:
        opening, syncing, linking, unlinking = os.open, os.fsync, os.link, os.unlink
        exchange = module._renameat_exchange
        def is_preparation(name):
            return str(name).endswith(".prepare.retire")
        def opened(path, *args, **kwargs):
            fd = opening(path, *args, **kwargs)
            if boundary == "created" and is_preparation(path):
                os._exit(73)
            return fd
        def synced(fd):
            syncing(fd)
            if boundary == "identified":
                for path in (home / "agents").glob("*.prepare.retire"):
                    if path.lstat().st_ino == os.fstat(fd).st_ino:
                        os._exit(73)
        def linked(source, target, *args, **kwargs):
            linking(source, target, *args, **kwargs)
            if boundary == "linked" and is_preparation(source):
                os._exit(73)
        def unlinked(path, *args, **kwargs):
            unlinking(path, *args, **kwargs)
            if boundary == "prepared" and is_preparation(path):
                os._exit(73)
        def exchanged(*args):
            exchange(*args)
            if boundary == "exchanged":
                os._exit(73)
        with mock.patch.object(module.os, "open", opened), mock.patch.object(module.os, "fsync", synced), mock.patch.object(module.os, "link", linked), mock.patch.object(module.os, "unlink", unlinked), mock.patch.object(module, "_renameat_exchange", exchanged):
            if rollback:
                module.install(repo, home, state, FakeRunner([
                    marketplace_list_response(), marketplace_add_response(repo),
                    plugin_list_response(), FakeResult(1, stderr="plugin add failed"),
                    removal_response(),
                ]))
            else:
                module.uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        os._exit(74)
    wait_for_crashed_child(pid)
    if boundary == "created":
        preparation, = (home / "agents").glob("*.prepare.retire")
        inode = preparation.lstat().st_ino
        for _ in range(2):
            with pytest.raises(module.InstallError, match="preparation.*unproven"):
                module.uninstall(repo, home, state, FakeRunner([]))
            assert preparation.lstat().st_ino == inode
            assert receipt_path(state).exists() or module._codex_install_journal_path(state).exists()
        # No identity was durably recorded. Only explicit removal of this
        # unproven empty preparation permits retry; it is never auto-adopted.
        preparation.unlink()
    for _ in range(2):
        module.uninstall(repo, home, state, FakeRunner([
            plugin_list_response(), marketplace_list_response(), removal_response(),
        ]))
    assert list((home / "agents").iterdir()) == []
    assert not receipt_path(state).exists()
    assert not module._codex_install_journal_path(state).exists()


@pytest.mark.parametrize("replacement", ["preparation", "retirement", "public"])
def test_darwin_named_retirement_preserves_replacements(tmp_path, darwin_api, replacement):
    repo = seed_repository(tmp_path / "repo")
    home, state = tmp_path / "codex", tmp_path / "state"
    result = module.install(repo, home, state, FakeRunner([]), agents_only=True)
    destination = result.created_links[0].destination
    saved = tmp_path / "saved"
    replaced = None
    linking, syncing, exchange = os.link, os.fsync, module._renameat_exchange
    def substitute(path):
        nonlocal replaced
        if replaced is not None:
            return
        replaced = path
        path.rename(saved)
        if saved.is_symlink():
            path.symlink_to(os.readlink(saved))
        else:
            path.write_bytes(saved.read_bytes())
    def synced(fd):
        syncing(fd)
        if replacement == "preparation":
            for path in destination.parent.glob("*.prepare.retire"):
                if path.lstat().st_ino == os.fstat(fd).st_ino:
                    substitute(path)
    def linked(source, target, *args, **kwargs):
        linking(source, target, *args, **kwargs)
        if replacement == "retirement" and str(source).endswith(".prepare.retire"):
            substitute(destination.parent / target)
    def exchanged(source_fd, source, target_fd, target):
        if replacement == "public" and source == destination.name:
            substitute(destination)
        exchange(source_fd, source, target_fd, target)
    with mock.patch.object(module.os, "fsync", synced), mock.patch.object(module.os, "link", linked), mock.patch.object(module, "_renameat_exchange", exchanged), pytest.raises(module.InstallError):
        module.uninstall(repo, home, state, FakeRunner([]), agents_only=True)
    assert replaced is not None
    inode = replaced.lstat().st_ino
    for _ in range(2):
        with pytest.raises(module.InstallError):
            module.uninstall(repo, home, state, FakeRunner([]), agents_only=True)
        assert replaced.lstat().st_ino == inode
        assert receipt_path(state).exists()
    replaced.unlink()
    if replacement != "public":
        saved.rename(replaced)
    module.uninstall(repo, home, state, FakeRunner([plugin_list_response(), marketplace_list_response()]))
    assert list(destination.parent.iterdir()) == []


@pytest.mark.parametrize("fault", ["error", "marker", "exchange"])
def test_darwin_recovery_directory_cleanup(tmp_path, darwin_api, fault):
    from tests import test_install as cases
    cases.test_recovery_package_cleanup_retains_exact_authority(tmp_path, "uninstall", fault)


@pytest.mark.parametrize("boundary", [
    "content", "marker", "exchange", "sentinel", "before-checkpoint",
    "after-checkpoint", "retired-object", "removed",
])
def test_darwin_swap_backup_cleanup_process_exit(tmp_path, darwin_api, boundary):
    from tests import test_install as cases
    cases.test_swap_backup_cleanup_process_exit(tmp_path, boundary)


@pytest.mark.parametrize("replacement", ["directory", "symlink"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_darwin_swap_backup_replacement_after_validation(tmp_path, darwin_api, replacement, interrupted):
    from tests import test_install as cases
    cases.test_swap_backup_replacement_after_validation(tmp_path, replacement, interrupted)


@pytest.mark.parametrize("replacement", [None, "public-file", "public-link", "private"])
def test_darwin_managed_rollback_retirement_recovers_before_install_conflicts(tmp_path, darwin_api, replacement):
    from tests import test_install as cases
    cases.test_managed_rollback_retirement_recovers_before_install_conflicts(tmp_path, replacement)


@pytest.mark.parametrize("interrupted", [False, True, "build"])
def test_darwin_package_replacement_during_build(tmp_path, darwin_api, interrupted):
    from tests import test_install as cases
    cases.test_package_replacement_during_build_preserves_original_authority(tmp_path, interrupted)


@pytest.mark.parametrize("boundary", ["retired-object", "journal-clear"])
def test_darwin_swap_backup_inode_reuse(darwin_api, boundary):
    from tests import test_install as cases
    cases.test_swap_backup_inode_reuse_after_removal_preserves_replacement(boundary)


@pytest.mark.parametrize("replacement", [None, "public-file", "public-link", "private"])
def test_darwin_receipt_retirement_recovers_before_install_conflicts(tmp_path, darwin_api, replacement):
    from tests import test_install as cases
    cases.test_receipt_retirement_recovers_before_install_conflicts(tmp_path, replacement)
