"""Focused tests for the pinned CLI acquisition and cache-integrity contract."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import cli_verification
from tests.cli_verification import (
    CLI_MODE_ENV,
    CLI_RELEASE_MANIFEST,
    CliVerificationError,
    ensure_binary,
)


PLATFORM = ("linux", "x86_64")


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


class CliVerificationTests(unittest.TestCase):
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
            with self.assertRaises(CliVerificationError):
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

    def test_required_mode_fails_download_and_leaves_no_partial_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)

            def interrupted(_url: str, destination: Path) -> None:
                destination.write_bytes(b"partial archive")
                raise OSError("connection reset")

            with self.assertRaises(CliVerificationError):
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

            with self.assertRaises(CliVerificationError):
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
                        OSError("offline")
                    ),
                )

    def test_optional_mode_still_fails_integrity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(b"corrupt archive")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with self.assertRaises(CliVerificationError):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="optional")

    def test_corrupt_cached_archive_fails_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(b"corrupt archive")
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                with self.assertRaises(CliVerificationError):
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
                with self.assertRaises(CliVerificationError):
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
                binary.write_bytes(b"tampered binary")
                with self.assertRaises(CliVerificationError):
                    ensure_binary("opencode", cache_root, platform_key=PLATFORM, mode="required")

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
                with self.assertRaises(CliVerificationError):
                    ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")

    def test_verified_cache_can_be_reused_without_retaining_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache_root = Path(temporary)
            archive_name = "fixture-codex.tar.gz"
            archive_bytes = _archive("fixture-codex", b"trusted binary")
            manifest = _patched_manifest("codex", archive_name, "fixture-codex", archive_bytes)
            (cache_root / archive_name).write_bytes(archive_bytes)
            with mock.patch.object(cli_verification, "CLI_RELEASE_MANIFEST", manifest):
                binary = ensure_binary("codex", cache_root, platform_key=PLATFORM, mode="required")
                (cache_root / archive_name).unlink()
                reused = ensure_binary(
                    "codex",
                    cache_root,
                    platform_key=PLATFORM,
                    mode="required",
                    downloader=lambda _url, _destination: (_ for _ in ()).throw(
                        AssertionError("verified cache should not download")
                    ),
                )
            self.assertEqual(reused, binary)

    def test_override_requires_matching_digest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "codex"
            override.write_bytes(b"trusted override")
            override.chmod(0o755)
            environment = {"EXPSKILL_TEST_CODEX_BIN": str(override)}
            with self.assertRaises(CliVerificationError):
                ensure_binary("codex", Path(temporary), environ=environment, mode="required")
            environment["EXPSKILL_TEST_CODEX_BIN_SHA256"] = hashlib.sha256(
                b"trusted override"
            ).hexdigest()
            self.assertEqual(
                ensure_binary("codex", Path(temporary), environ=environment, mode="required"),
                override,
            )

    def test_mode_environment_is_explicit_and_rejects_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            environment = {CLI_MODE_ENV: "sometimes"}
            with self.assertRaises(CliVerificationError):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    environ=environment,
                )

    def test_mode_defaults_to_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CliVerificationError):
                ensure_binary(
                    "codex",
                    Path(temporary),
                    platform_key=("haiku", "riscv64"),
                    environ={},
                )


if __name__ == "__main__":
    unittest.main()
