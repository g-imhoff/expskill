"""Focused tests for the pinned CLI acquisition and cache-integrity contract."""

from __future__ import annotations

import copy
import errno
import hashlib
import io
import json
import os
import socket
import tarfile
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests import cli_verification
from tests.cli_verification import (
    CLI_MODE_ENV,
    CLI_RELEASE_MANIFEST,
    CliAcquisitionUnavailable,
    CliVerificationError,
    _asset_for,
    ensure_binary,
    platform_key,
)


PLATFORM = ("linux", "x86_64")
_UNIT_ENVIRONMENT_KEYS = (
    "EXPSKILL_TEST_CODEX_BIN",
    "EXPSKILL_TEST_CODEX_BIN_SHA256",
    "EXPSKILL_TEST_OPENCODE_BIN",
    "EXPSKILL_TEST_OPENCODE_BIN_SHA256",
    CLI_MODE_ENV,
)


def _archive(member_name: str, payload: bytes) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        member.mode = 0o755
        bundle.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def _patched_manifest(kind: str, archive_name: str, binary_name: str, archive_bytes: bytes) -> dict[str, object]:
    manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
    cli = manifest["clis"][kind]
    asset = cli["platforms"]["linux-x86_64"]
    asset.update(
        {
            "archive": archive_name,
            "binary_name": binary_name,
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "url": "https://example.invalid/pinned-cli.tar.gz",
        }
    )
    return manifest


@contextmanager
def _assert_cli_failure(test_case: unittest.TestCase):
    """Treat an unexpected SkipTest as a hard test failure."""

    try:
        yield
    except unittest.SkipTest as error:
        test_case.fail(f"unexpected optional CLI skip: {error}")
    except Exception as error:
        test_case.assertIsInstance(error, CliVerificationError)
    else:
        test_case.fail("expected CliVerificationError")


class CliVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._environment_before = dict(os.environ)
        for name in _UNIT_ENVIRONMENT_KEYS:
            os.environ.pop(name, None)
        self.addCleanup(self._restore_environment)

    def _restore_environment(self) -> None:
        os.environ.clear()
        os.environ.update(self._environment_before)

    def test_manifest_keeps_authoritative_provenance_and_pinned_versions(self) -> None:
        self.assertEqual(
            CLI_RELEASE_MANIFEST["provenance"]["codex"],
            "https://api.github.com/repos/openai/codex/releases/tags/rust-v0.153.4",
        )
        self.assertEqual(
            CLI_RELEASE_MANIFEST["provenance"]["opencode"],
            "https://api.github.com/repos/anomalyco/opencode/releases/tags/v1.18.29",
        )
        self.assertEqual(CLI_RELEASE_MANIFEST["clis"]["codex"]["version"], "0.153.4")
        self.assertEqual(CLI_RELEASE_MANIFEST["clis"]["opencode"]["version"], "1.18.29")

    def test_required_mode_fails_when_platform_asset_is_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    mode="required",
                )

    def test_optional_mode_may_skip_unsupported_platform_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(unittest.SkipTest):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    mode="optional",
                )

    def test_platform_alias_matrix_selects_linux_and_macos_assets_for_each_cli(self) -> None:
        hosts = {
            ("Linux", "x86_64"): ("linux", "x86_64"),
            ("Linux", "amd64"): ("linux", "x86_64"),
            ("Linux", "aarch64"): ("linux", "aarch64"),
            ("Linux", "arm64"): ("linux", "aarch64"),
            ("Darwin", "x86_64"): ("darwin", "x86_64"),
            ("Darwin", "amd64"): ("darwin", "x86_64"),
            ("Darwin", "aarch64"): ("darwin", "arm64"),
            ("Darwin", "arm64"): ("darwin", "arm64"),
        }
        for kind in ("codex", "opencode"):
            for host, expected_key in hosts.items():
                with self.subTest(kind=kind, host=host):
                    key = platform_key(*host)
                    self.assertEqual(key, expected_key)
                    asset = _asset_for(kind, key, "required")
                    self.assertEqual(asset.kind, kind)

    def test_all_pinned_platform_entries_and_fixture_components_are_accepted(self) -> None:
        for kind, entry in CLI_RELEASE_MANIFEST["clis"].items():
            with self.subTest(kind=kind):
                self.assertIsInstance(entry["version"], str)
                for platform_name in entry["platforms"]:
                    system, machine = platform_name.split("-", 1)
                    asset = _asset_for(kind, (system, machine), "required")
                    self.assertEqual(asset.version, entry["version"])

        manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
        manifest["clis"]["codex"]["version"] = "release-2026.09_rc1"
        manifest["clis"]["codex"]["platforms"]["linux-x86_64"].update(
            {
                "archive": "fixture-codex-2026.09.tar.gz",
                "binary_name": "fixture-codex_2026.09",
            }
        )
        with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
            asset = _asset_for("codex", PLATFORM, "required")
        self.assertEqual(asset.version, "release-2026.09_rc1")
        self.assertEqual(asset.archive, "fixture-codex-2026.09.tar.gz")
        self.assertEqual(asset.binary_name, "fixture-codex_2026.09")

    def test_manifest_scalar_components_fail_closed_before_download_in_both_modes(self) -> None:
        invalid_components = (
            None,
            0,
            "",
            ".",
            "..",
            "old/../../victim",
            r"old\..\victim",
            "/absolute",
            r"C:\victim",
            "wild*card",
            "bad name",
            "bad\tname",
            "bad\x00name",
        )
        for mode in ("required", "optional"):
            for field in ("version", "archive", "binary_name"):
                for value in invalid_components:
                    with self.subTest(mode=mode, field=field, value=repr(value)):
                        manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
                        if field == "version":
                            manifest["clis"]["codex"][field] = value
                        else:
                            manifest["clis"]["codex"]["platforms"]["linux-x86_64"][field] = value
                        with tempfile.TemporaryDirectory() as temporary:
                            cache_root = Path(temporary)
                            sentinel = cache_root / "sentinel"
                            sentinel.write_bytes(b"preserve me")
                            before = sorted(path.name for path in cache_root.iterdir())
                            called = False

                            def downloader(_url: str, _destination: Path) -> None:
                                nonlocal called
                                called = True

                            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                                with _assert_cli_failure(self):
                                    ensure_binary(
                                        "codex",
                                        cache_root,
                                        platform_key=PLATFORM,
                                        mode=mode,
                                        downloader=downloader,
                                    )
                            self.assertFalse(called)
                            self.assertEqual(sorted(path.name for path in cache_root.iterdir()), before)
                            self.assertEqual(sentinel.read_bytes(), b"preserve me")

    def test_malformed_https_urls_fail_closed_before_download_in_both_modes(self) -> None:
        invalid_urls = (
            None,
            0,
            "",
            "https://",
            "https:///path",
            "https://:443/path",
            "https://example.invalid:",
            "https://example.invalid:bad/path",
            "https://example.invalid:99999/path",
            "https://[::1/path",
            "http://example.invalid/path",
            "https://example.invalid/path with space",
            r"https://bad\host.invalid/asset",
            'https://bad"host.invalid/asset',
            "https://bad{host}.invalid/asset",
            "https://[::1]garbage/asset",
            "https://example.invalid../asset",
            "https://-bad.invalid/asset",
            "https://bad-.invalid/asset",
            "https://bad..host.invalid/asset",
            "https://" + ("a" * 64) + ".invalid/asset",
        )
        for mode in ("required", "optional"):
            for url in invalid_urls:
                with self.subTest(mode=mode, url=url):
                    manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
                    manifest["clis"]["codex"]["platforms"]["linux-x86_64"]["url"] = url
                    with tempfile.TemporaryDirectory() as temporary:
                        cache_root = Path(temporary)
                        sentinel = cache_root / "sentinel"
                        sentinel.write_bytes(b"preserve me")
                        before = sorted(path.name for path in cache_root.iterdir())
                        called = False

                        def downloader(_url: str, _destination: Path) -> None:
                            nonlocal called
                            called = True

                        with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                            with _assert_cli_failure(self):
                                ensure_binary(
                                    "codex",
                                    cache_root,
                                    platform_key=PLATFORM,
                                    mode=mode,
                                    downloader=downloader,
                                )
                        self.assertFalse(called)
                        self.assertEqual(sorted(path.name for path in cache_root.iterdir()), before)
                        self.assertEqual(sentinel.read_bytes(), b"preserve me")

    def test_malformed_version_fails_before_unsupported_optional_platform_skip(self) -> None:
        manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
        manifest["clis"]["codex"]["version"] = "old/../../victim"
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            sentinel = cache_root / "sentinel"
            sentinel.write_bytes(b"preserve me")
            called = False

            def downloader(_url: str, _destination: Path) -> None:
                nonlocal called
                called = True

            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        cache_root,
                        platform_key=("haiku", "riscv64"),
                        mode="optional",
                        downloader=downloader,
                    )
            self.assertFalse(called)
            self.assertEqual(sentinel.read_bytes(), b"preserve me")

    def test_version_traversal_never_replaces_outside_sentinel_in_both_modes(self) -> None:
        archive_name = "fixture-codex.tar.gz"
        archive_bytes = _archive("fixture-codex", b"trusted binary")
        manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
        manifest["clis"]["codex"]["version"] = "old/../../victim"
        for mode in ("required", "optional"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                sandbox = Path(temporary)
                cache_root = sandbox / "cache"
                cache_root.mkdir()
                (cache_root / "codex-old").mkdir()
                victim = sandbox / "victim"
                victim.mkdir()
                sentinel = victim / "sentinel"
                sentinel.write_bytes(b"outside sentinel")
                (cache_root / archive_name).write_bytes(archive_bytes)
                called = False

                def downloader(_url: str, _destination: Path) -> None:
                    nonlocal called
                    called = True

                with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                    with _assert_cli_failure(self):
                        ensure_binary(
                            "codex",
                            cache_root,
                            platform_key=PLATFORM,
                            mode=mode,
                            downloader=downloader,
                        )
                self.assertFalse(called)
                self.assertTrue(victim.is_dir())
                self.assertEqual(sentinel.read_bytes(), b"outside sentinel")
                self.assertTrue((cache_root / "codex-old").is_dir())
                self.assertEqual((cache_root / archive_name).read_bytes(), archive_bytes)

    def test_computed_cache_directory_must_be_contained_even_if_asset_validation_is_bypassed(self) -> None:
        archive_name = "fixture-codex.tar.gz"
        archive_bytes = _archive("fixture-codex", b"trusted binary")
        malicious_asset = cli_verification.CliAsset(
            kind="codex",
            version="old/../../victim",
            archive=archive_name,
            binary_name="fixture-codex",
            sha256=hashlib.sha256(archive_bytes).hexdigest(),
            url="https://example.invalid/pinned-cli.tar.gz",
        )
        with tempfile.TemporaryDirectory() as temporary:
            sandbox = Path(temporary)
            cache_root = sandbox / "cache"
            cache_root.mkdir()
            (cache_root / "codex-old").mkdir()
            victim = sandbox / "victim"
            victim.mkdir()
            sentinel = victim / "sentinel"
            sentinel.write_bytes(b"outside sentinel")
            (cache_root / archive_name).write_bytes(archive_bytes)
            called = False

            def downloader(_url: str, _destination: Path) -> None:
                nonlocal called
                called = True

            with mock.patch.object(cli_verification, "_asset_for", return_value=malicious_asset):
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        cache_root,
                        platform_key=PLATFORM,
                        mode="required",
                        downloader=downloader,
                    )
            self.assertFalse(called)
            self.assertEqual(sentinel.read_bytes(), b"outside sentinel")
            self.assertTrue((cache_root / "codex-old").is_dir())

    def test_malformed_platform_configuration_fails_in_both_modes(self) -> None:
        for mode in ("required", "optional"):
            with self.subTest(mode=mode):
                for malformed_platforms in (["linux-x86_64"], {}):
                    with self.subTest(platforms=malformed_platforms):
                        manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
                        manifest["clis"]["codex"]["platforms"] = malformed_platforms
                        with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                            with tempfile.TemporaryDirectory() as temporary:
                                with _assert_cli_failure(self):
                                    ensure_binary(
                                        "codex",
                                        Path(temporary),
                                        platform_key=PLATFORM,
                                        mode=mode,
                                    )

    def test_malformed_platform_entry_fails_in_both_modes(self) -> None:
        for mode in ("required", "optional"):
            with self.subTest(mode=mode):
                manifest = copy.deepcopy(CLI_RELEASE_MANIFEST)
                manifest["clis"]["codex"]["platforms"]["linux-x86_64"] = "not-an-asset"
                with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                    with tempfile.TemporaryDirectory() as temporary:
                        with _assert_cli_failure(self):
                            ensure_binary(
                                "codex",
                                Path(temporary),
                                platform_key=PLATFORM,
                                mode=mode,
                            )

    def test_required_mode_fails_download_and_leaves_no_partial_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)

            def interrupted(_url: str, destination: Path) -> None:
                destination.write_bytes(b"partial archive")
                raise OSError("connection reset")

            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="required",
                    downloader=interrupted,
                )
            self.assertEqual(list(cache_root.glob("*.part")), [])
            self.assertFalse((cache_root / "codex-0.153.4").exists())

    def test_failed_download_does_not_replace_an_existing_unverified_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            stale_cache = cache_root / "codex-0.153.4"
            stale_cache.mkdir()
            (stale_cache / "bin").write_bytes(b"stale")
            (stale_cache / ".ready").write_text("0.153.4\n", encoding="utf-8")

            def interrupted(_url: str, destination: Path) -> None:
                destination.write_bytes(b"partial archive")
                raise OSError("connection reset")

            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="required",
                    downloader=interrupted,
                )
            self.assertEqual((stale_cache / "bin").read_bytes(), b"stale")
            self.assertFalse((stale_cache / ".integrity.json").exists())

    def test_optional_mode_explicitly_skips_download_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(unittest.SkipTest):
                ensure_binary(
                    "opencode",
                    Path(temporary),
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=lambda _url, _destination: (_ for _ in ()).throw(
                        CliAcquisitionUnavailable("offline")
                    ),
                )

    def test_optional_mode_fails_malformed_cache_before_offline_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            cache_dir = cache_root / "codex-0.153.4"
            cache_dir.mkdir()
            (cache_dir / "bin").write_bytes(b"untrusted cache")
            (cache_dir / ".integrity.json").write_text("not json", encoding="utf-8")
            called = False

            def offline(_url: str, _destination: Path) -> None:
                nonlocal called
                called = True
                raise CliAcquisitionUnavailable("offline")

            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=offline,
                )
            self.assertFalse(called)

    def test_optional_mode_fails_non_directory_cache_before_offline_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            (cache_root / "codex-0.153.4").write_bytes(b"not a cache directory")
            called = False

            def offline(_url: str, _destination: Path) -> None:
                nonlocal called
                called = True
                raise CliAcquisitionUnavailable("offline")

            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=offline,
                )
            self.assertFalse(called)

    def test_optional_mode_fails_nonregular_downloader_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)

            def produces_directory(_url: str, destination: Path) -> None:
                destination.unlink()
                destination.mkdir()

            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=produces_directory,
                )
            self.assertEqual(list(cache_root.glob("*.part")), [])

    def test_optional_mode_fails_local_downloader_filesystem_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=lambda _url, _destination: (_ for _ in ()).throw(
                        PermissionError("temporary archive is not writable")
                    ),
                )

    def test_optional_mode_fails_local_mkstemp_network_errno(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                cli_verification.tempfile,
                "mkstemp",
                side_effect=OSError(errno.ETIMEDOUT, "local staging timed out"),
            ):
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        Path(temporary),
                        platform_key=PLATFORM,
                        mode="optional",
                    )

    def test_optional_mode_fails_local_hash_network_errno(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                cli_verification,
                "_sha256",
                side_effect=OSError(errno.ETIMEDOUT, "local hash timed out"),
            ):
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        Path(temporary),
                        platform_key=PLATFORM,
                        mode="optional",
                        downloader=lambda _url, destination: destination.write_bytes(b"archive"),
                    )

    def test_optional_mode_fails_local_publication_network_errno(self) -> None:
        archive_name = "fixture-codex.tar.gz"
        archive_bytes = _archive("fixture-codex", b"trusted binary")
        manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with mock.patch.object(
                    cli_verification.os,
                    "replace",
                    side_effect=OSError(errno.ETIMEDOUT, "local publication timed out"),
                ):
                    with _assert_cli_failure(self):
                        ensure_binary(
                            "codex",
                            Path(temporary),
                            platform_key=PLATFORM,
                            mode="optional",
                            downloader=lambda _url, destination: destination.write_bytes(archive_bytes),
                        )

    def test_optional_mode_fails_wrapped_local_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wrapped = urllib.error.URLError(PermissionError(errno.EACCES, "local policy denied"))
            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=PLATFORM,
                    mode="optional",
                    downloader=lambda _url, _destination: (_ for _ in ()).throw(wrapped),
                )

    def test_default_downloader_distinguishes_destination_io_from_network_failure(self) -> None:
        class Response:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self, _size: int) -> bytes:
                payload, self.payload = self.payload, b""
                return payload

        with tempfile.TemporaryDirectory() as temporary:
            destination_error = OSError(errno.ETIMEDOUT, "destination write timed out")
            with mock.patch.object(
                cli_verification.urllib.request,
                "urlopen",
                return_value=Response(b"archive"),
            ), mock.patch.object(
                cli_verification.Path,
                "open",
                side_effect=destination_error,
            ):
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        Path(temporary),
                        platform_key=PLATFORM,
                        mode="optional",
                    )

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(
                cli_verification.urllib.request,
                "urlopen",
                side_effect=urllib.error.URLError("offline"),
            ):
                with self.assertRaises(unittest.SkipTest):
                    ensure_binary(
                        "codex",
                        Path(temporary),
                        platform_key=PLATFORM,
                        mode="optional",
                    )

    def test_default_downloader_dns_failure_skips_only_in_optional_mode(self) -> None:
        dns_failure = urllib.error.URLError(
            socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure")
        )
        for mode in ("optional", "required"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                with mock.patch.object(
                    cli_verification.urllib.request,
                    "urlopen",
                    side_effect=dns_failure,
                ):
                    if mode == "optional":
                        with self.assertRaises(unittest.SkipTest):
                            ensure_binary(
                                "codex",
                                Path(temporary),
                                platform_key=PLATFORM,
                                mode=mode,
                            )
                    else:
                        with _assert_cli_failure(self):
                            ensure_binary(
                                "codex",
                                Path(temporary),
                                platform_key=PLATFORM,
                                mode=mode,
                            )

    def test_default_downloader_http_error_responses_are_hard_failures(self) -> None:
        for status, reason in (
            (404, "Not Found"),
            (429, "Too Many Requests"),
            (500, "Internal Server Error"),
        ):
            for mode in ("required", "optional"):
                with self.subTest(status=status, mode=mode), tempfile.TemporaryDirectory() as temporary:
                    http_error = urllib.error.HTTPError(
                        "https://example.invalid/pinned-cli.tar.gz",
                        status,
                        reason,
                        hdrs=None,
                        fp=None,
                    )
                    with mock.patch.object(
                        cli_verification.urllib.request,
                        "urlopen",
                        side_effect=http_error,
                    ), _assert_cli_failure(self):
                        ensure_binary(
                            "codex",
                            Path(temporary),
                            platform_key=PLATFORM,
                            mode=mode,
                        )
                    self.assertEqual(list(Path(temporary).glob("*.part")), [])

    def test_optional_mode_fails_local_staging_and_publication_errors(self) -> None:
        archive_name = "fixture-codex.tar.gz"
        archive_bytes = _archive("fixture-codex", b"trusted binary")
        manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
        for patcher in (
            mock.patch.object(
                cli_verification.tempfile,
                "mkdtemp",
                side_effect=OSError("staging unavailable"),
            ),
            mock.patch.object(
                cli_verification.os,
                "replace",
                side_effect=OSError("publication unavailable"),
            ),
        ):
            with self.subTest(fault=patcher.attribute), tempfile.TemporaryDirectory() as temporary:
                cache_root = Path(temporary)
                (cache_root / archive_name).write_bytes(archive_bytes)
                with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                    with patcher, _assert_cli_failure(self):
                        ensure_binary(
                            "codex",
                            cache_root,
                            platform_key=PLATFORM,
                            mode="optional",
                        )

    def test_optional_mode_still_fails_integrity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(b"corrupt archive")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with _assert_cli_failure(self):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="optional")

    def test_corrupt_cached_archive_fails_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(b"corrupt archive")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with _assert_cli_failure(self):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")
            self.assertFalse((cache_root / "codex-0.153.4").exists())

    def test_unexpected_layout_fails_after_archive_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("unexpected", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with _assert_cli_failure(self):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")
            self.assertFalse((cache_root / "codex-0.153.4").exists())

    def test_cache_records_binary_digest_and_fails_when_binary_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-opencode.tar.gz"
            archive_bytes = _archive("fixture-opencode", b"trusted binary")
            manifest = _patched_manifest("opencode", archive_name, "fixture-opencode", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                binary = ensure_binary("opencode", cache_root, platform_key=PLATFORM, mode="required")
                metadata_path = binary.parent / ".integrity.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(metadata["binary_sha256"], hashlib.sha256(binary.read_bytes()).hexdigest())
                self.assertTrue(
                    binary.is_relative_to(cache_root),
                    f"destructive fixture escaped disposable cache root: {binary}",
                )
                binary.write_bytes(b"tampered binary")
                with _assert_cli_failure(self):
                    ensure_binary("opencode", cache_root, platform_key=PLATFORM, mode="required")

    def test_cache_fixture_is_contained_when_external_override_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            external_root = Path(temporary) / "external-override"
            external_root.mkdir()
            external_binary = external_root / "opencode"
            original_bytes = b"disposable external override"
            external_binary.write_bytes(original_bytes)
            external_binary.chmod(0o755)
            digest = hashlib.sha256(original_bytes).hexdigest()
            receipt = external_root / ".integrity.json"
            original_receipt = json.dumps({"binary_sha256": digest})
            receipt.write_text(original_receipt, encoding="utf-8")

            surrounding_environment = dict(os.environ)
            override_environment = {
                "EXPSKILL_TEST_OPENCODE_BIN": str(external_binary),
                "EXPSKILL_TEST_OPENCODE_BIN_SHA256": digest,
                CLI_MODE_ENV: "required",
            }
            with mock.patch.dict(os.environ, override_environment, clear=False):
                result = unittest.TestResult()
                vulnerable_case = type(self)("test_cache_records_binary_digest_and_fails_when_binary_changes")
                vulnerable_case.run(result)

            self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
            self.assertEqual(external_binary.read_bytes(), original_bytes)
            self.assertEqual(receipt.read_text(encoding="utf-8"), original_receipt)
            self.assertEqual(dict(os.environ), surrounding_environment)

    def test_marker_without_integrity_metadata_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            cache_dir = cache_root / "codex-0.153.4"
            cache_dir.mkdir()
            (cache_dir / "bin").write_bytes(b"untrusted cache")
            (cache_dir / ".ready").write_text("0.153.4\n", encoding="utf-8")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                binary = ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")
            self.assertEqual(binary.read_bytes(), b"trusted binary")
            self.assertTrue((cache_dir / ".integrity.json").is_file())

    def test_malformed_integrity_receipt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            cache_dir = cache_root / "codex-0.153.4"
            cache_dir.mkdir()
            (cache_dir / "bin").write_bytes(b"untrusted cache")
            (cache_dir / ".integrity.json").write_text("not json", encoding="utf-8")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with _assert_cli_failure(self):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")

    def test_cache_without_archive_is_not_authorized_by_its_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                binary = ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")
                (cache_root / archive_name).unlink()
                with _assert_cli_failure(self):
                    ensure_binary(
                        "codex",
                        cache_root,
                        platform_key=PLATFORM,
                        mode="required",
                        downloader=lambda _url, _destination: (_ for _ in ()).throw(
                            OSError("archive unavailable")
                        ),
                    )
            self.assertEqual(binary.read_bytes(), b"trusted binary")

    def test_self_consistent_forged_cache_never_passes_without_archive(self) -> None:
        forged = b"forged executable"
        for mode in ("required", "optional"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                cache_root = Path(temporary)
                archive_name = "fixture-codex.tar.gz"
                archive_bytes = _archive("fixture-codex", b"trusted binary")
                manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
                cache_dir = cache_root / "codex-0.153.4"
                cache_dir.mkdir()
                binary = cache_dir / "bin"
                binary.write_bytes(forged)
                binary.chmod(0o755)
                (cache_dir / ".ready").write_text("0.153.4\n", encoding="utf-8")
                (cache_dir / ".integrity.json").write_text(
                    json.dumps(
                        {
                            "archive": archive_name,
                            "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                            "binary_name": "fixture-codex",
                            "binary_sha256": hashlib.sha256(forged).hexdigest(),
                            "kind": "codex",
                            "schema": 1,
                            "url": "https://example.invalid/pinned-cli.tar.gz",
                            "version": "0.153.4",
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                    if mode == "optional":
                        with self.assertRaises(unittest.SkipTest):
                            ensure_binary(
                                "codex",
                                cache_root,
                                platform_key=PLATFORM,
                                mode=mode,
                                downloader=lambda _url, _destination: (_ for _ in ()).throw(
                                    CliAcquisitionUnavailable("archive unavailable")
                                ),
                            )
                    else:
                        with _assert_cli_failure(self):
                            ensure_binary(
                                "codex",
                                cache_root,
                                platform_key=PLATFORM,
                                mode=mode,
                                downloader=lambda _url, _destination: (_ for _ in ()).throw(
                                    CliAcquisitionUnavailable("archive unavailable")
                                ),
                            )
                self.assertEqual(binary.read_bytes(), forged)

    def test_self_consistent_forged_cache_never_passes_with_valid_archive(self) -> None:
        forged = b"forged executable"
        for mode in ("required", "optional"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                cache_root = Path(temporary)
                archive_name = "fixture-codex.tar.gz"
                archive_bytes = _archive("fixture-codex", b"trusted binary")
                manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
                (cache_root / archive_name).write_bytes(archive_bytes)
                cache_dir = cache_root / "codex-0.153.4"
                cache_dir.mkdir()
                binary = cache_dir / "bin"
                binary.write_bytes(forged)
                binary.chmod(0o755)
                (cache_dir / ".ready").write_text("0.153.4\n", encoding="utf-8")
                (cache_dir / ".integrity.json").write_text(
                    json.dumps(
                        {
                            "archive": archive_name,
                            "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                            "binary_name": "fixture-codex",
                            "binary_sha256": hashlib.sha256(forged).hexdigest(),
                            "kind": "codex",
                            "schema": 1,
                            "url": "https://example.invalid/pinned-cli.tar.gz",
                            "version": "0.153.4",
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                    with _assert_cli_failure(self):
                        ensure_binary(
                            "codex",
                            cache_root,
                            platform_key=PLATFORM,
                            mode=mode,
                        )
                self.assertEqual(binary.read_bytes(), forged)

    def test_override_requires_matching_digest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "codex"
            override.write_bytes(b"trusted override")
            override.chmod(0o755)
            environment = {"EXPSKILL_TEST_CODEX_BIN": str(override)}
            with _assert_cli_failure(self):
                ensure_binary("codex", Path(temporary), environ=environment, mode="required")
            environment["EXPSKILL_TEST_CODEX_BIN_SHA256"] = hashlib.sha256(
                b"trusted override"
            ).hexdigest()
            self.assertEqual(
                ensure_binary("codex", Path(temporary), environ=environment, mode="required"),
                override,
            )

    def test_relative_override_returns_absolute_path_across_cwd_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            elsewhere = root / "elsewhere"
            source.mkdir()
            elsewhere.mkdir()
            override = source / "codex"
            override.write_bytes(b"trusted relative override")
            override.chmod(0o755)
            environment = {
                "EXPSKILL_TEST_CODEX_BIN": "./codex",
                "EXPSKILL_TEST_CODEX_BIN_SHA256": hashlib.sha256(
                    b"trusted relative override"
                ).hexdigest(),
            }
            old_cwd = Path.cwd()
            try:
                os.chdir(source)
                resolved = ensure_binary(
                    "codex",
                    root / "cache",
                    environ=environment,
                    mode="required",
                )
                os.chdir(elsewhere)
                self.assertEqual(resolved, override)
                self.assertTrue(resolved.is_absolute())
            finally:
                os.chdir(old_cwd)

    def test_bare_name_override_uses_path_not_forged_cwd_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path_dir = root / "path-bin"
            cwd_dir = root / "cwd"
            path_dir.mkdir()
            cwd_dir.mkdir()
            trusted = path_dir / "opencode"
            trusted.write_bytes(b"trusted PATH override")
            trusted.chmod(0o755)
            forged = cwd_dir / "opencode"
            forged.write_bytes(b"forged cwd override")
            forged.chmod(0o755)
            environment = {
                "EXPSKILL_TEST_OPENCODE_BIN": "opencode",
                "EXPSKILL_TEST_OPENCODE_BIN_SHA256": hashlib.sha256(
                    b"trusted PATH override"
                ).hexdigest(),
                "PATH": str(path_dir),
            }
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd_dir)
                resolved = ensure_binary(
                    "opencode",
                    root / "cache",
                    environ=environment,
                    mode="required",
                )
            finally:
                os.chdir(old_cwd)
            self.assertEqual(resolved, trusted)
            self.assertTrue(resolved.is_absolute())

    def test_mode_environment_is_explicit_and_rejects_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {CLI_MODE_ENV: "sometimes"}
            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    environ=environment,
                )

    def test_mode_defaults_to_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with _assert_cli_failure(self):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    environ={},
                )


if __name__ == "__main__":
    unittest.main()
