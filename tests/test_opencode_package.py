from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.build_opencode_package import build_opencode_package

ROOT = Path(__file__).resolve().parents[1]
CODEX_ROOT = ROOT / "packages" / "expskill"

NODE = shutil.which("node")
NPM = shutil.which("npm")
needs_node_and_npm = unittest.skipUnless(
    NODE and NPM, "node and npm are required for the opencode package smoke test"
)

EXPECTED_EXPORTS = ("ExecutionPolicyPlugin", "UnslopPlugin")
SHARED_HELPERS = ("design_state.py", "plan_graph.py", "worktrees.py")
THIRD_PARTY_LICENSES = ("mattpocock-skills-MIT.txt", "pstack-MIT.txt")


def is_python_cache(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}


def run(
    command: list[str], cwd: Path, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class OpencodePackageTests(unittest.TestCase):
    @needs_node_and_npm
    def test_packed_package_is_publishable_installable_and_self_contained(self) -> None:
        assert NODE is not None
        assert NPM is not None
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package_root = temporary_root / "package"
            build_opencode_package(ROOT, package_root)
            pack_result = run(
                [NPM, "pack", "--json", "--pack-destination", str(temporary_root)],
                package_root,
            )
            self.assertEqual(
                pack_result.returncode,
                0,
                f"npm pack failed:\n{pack_result.stdout}\n{pack_result.stderr}",
            )
            pack_report = json.loads(pack_result.stdout)
            self.assertEqual(len(pack_report), 1, pack_report)
            tarball = temporary_root / pack_report[0]["filename"]
            self.assertTrue(tarball.is_file(), pack_report)

            problems: list[str] = []
            with tarfile.open(tarball, "r:gz") as archive:
                members = {member.name: member for member in archive.getmembers()}
                links = sorted(
                    name
                    for name, member in members.items()
                    if member.issym() or member.islnk()
                )
                if links:
                    problems.append(f"tarball contains links: {links}")
                caches = sorted(
                    name for name in members if is_python_cache(Path(name))
                )
                if caches:
                    problems.append(f"tarball contains Python caches: {caches}")

                def packed_bytes(relative: str) -> bytes | None:
                    name = f"package/{relative}"
                    member = members.get(name)
                    if member is None:
                        problems.append(f"tarball is missing {relative}")
                        return None
                    if not member.isfile():
                        problems.append(f"tarball member is not a regular file: {relative}")
                        return None
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    return extracted.read()

                manifest_bytes = packed_bytes("package.json")
                if manifest_bytes is not None:
                    manifest = json.loads(manifest_bytes)
                    if manifest.get("private") is True:
                        problems.append("package manifest is private")
                    if manifest.get("license") != "MIT":
                        problems.append("package manifest does not declare the MIT license")
                    if manifest.get("exports") != {".": "./index.js"}:
                        problems.append("package manifest has no explicit root export")

                for relative in ("index.js", "LICENSE"):
                    packed_bytes(relative)

                missing_skills: list[str] = []
                changed_skills: list[str] = []
                for source in sorted((CODEX_ROOT / "skills").rglob("*")):
                    relative_path = source.relative_to(CODEX_ROOT / "skills")
                    if is_python_cache(relative_path) or not source.is_file():
                        continue
                    relative = relative_path.as_posix()
                    name = f"package/skills/{relative}"
                    member = members.get(name)
                    if member is None or not member.isfile():
                        missing_skills.append(relative)
                        continue
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    if extracted.read() != source.read_bytes():
                        changed_skills.append(relative)
                if missing_skills:
                    problems.append(
                        f"tarball is missing {len(missing_skills)} shared skill files"
                    )
                if changed_skills:
                    problems.append(
                        f"tarball changed {len(changed_skills)} shared skill files"
                    )

                for helper in SHARED_HELPERS:
                    contents = packed_bytes(f"scripts/{helper}")
                    source = CODEX_ROOT / "scripts" / helper
                    if contents is not None and contents != source.read_bytes():
                        problems.append(f"tarball changed shared helper scripts/{helper}")

                policy = packed_bytes("assets/execution-policy.json")
                policy_source = CODEX_ROOT / "assets" / "execution-policy.json"
                if policy is not None and policy != policy_source.read_bytes():
                    problems.append("tarball changed assets/execution-policy.json")

                for license_name in THIRD_PARTY_LICENSES:
                    contents = packed_bytes(f"third-party/licenses/{license_name}")
                    source = CODEX_ROOT / "third-party" / "licenses" / license_name
                    if contents is not None and contents != source.read_bytes():
                        problems.append(
                            f"tarball changed third-party license {license_name}"
                        )

            project = temporary_root / "consumer"
            project.mkdir()
            (project / "package.json").write_text(
                '{"name":"opencode-package-smoke","private":true,"type":"module"}\n',
                encoding="utf-8",
            )
            install_result = run(
                [
                    NPM,
                    "install",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    "--package-lock=false",
                    str(tarball),
                ],
                project,
            )
            if install_result.returncode != 0:
                problems.append(
                    "clean npm install failed: "
                    + (install_result.stderr.strip() or install_result.stdout.strip())
                )
            else:
                installed = project / "node_modules" / "opencode-expskill"
                installed_links = sorted(
                    path.relative_to(installed).as_posix()
                    for path in installed.rglob("*")
                    if path.is_symlink()
                )
                if installed_links:
                    problems.append(f"installed package contains links: {installed_links}")

                import_script = """
const plugin = await import("opencode-expskill");
const names = Object.keys(plugin).sort();
const expected = ["ExecutionPolicyPlugin", "UnslopPlugin"];
if (JSON.stringify(names) !== JSON.stringify(expected)) {
  throw new Error(`unexpected exports: ${JSON.stringify(names)}`);
}
for (const name of expected) {
  if (typeof plugin[name] !== "function") {
    throw new Error(`${name} is not a plugin function`);
  }
}
const policyHooks = await plugin.ExecutionPolicyPlugin({});
const args = { subagent_type: "expskill-implementer" };
for (let index = 0; index < 30; index++) {
  const input = { tool: "task", sessionID: "packed-policy", callID: `call-${index}` };
  await policyHooks["tool.execute.before"](input, { args });
  await policyHooks["tool.execute.after"]({ ...input, args }, {});
}
let blocked = false;
try {
  await policyHooks["tool.execute.before"](
    { tool: "task", sessionID: "packed-policy", callID: "call-30" }, { args },
  );
} catch (error) {
  blocked = error.message.includes("budget exhausted");
}
if (!blocked) {
  throw new Error("installed ExecutionPolicyPlugin did not enforce its packaged budget");
}
const hooks = await plugin.UnslopPlugin({});
const output = { system: ["base instructions"] };
await hooks["experimental.chat.system.transform"]({ sessionID: "packed" }, output);
if (!output.system[0].includes("<unslop-scope>")) {
  throw new Error("installed UnslopPlugin could not load its packaged skill");
}
console.log(JSON.stringify(names));
"""
                clean_env = dict(os.environ)
                clean_env.pop("EXPSKILL_HOME", None)
                import_result = run(
                    [NODE, "--input-type=module", "--eval", import_script],
                    project,
                    env=clean_env,
                )
                if import_result.returncode != 0:
                    problems.append(
                        "installed package root import failed: "
                        + (import_result.stderr.strip() or import_result.stdout.strip())
                    )
                else:
                    try:
                        exported = tuple(json.loads(import_result.stdout))
                    except json.JSONDecodeError:
                        problems.append(
                            "installed package root returned invalid export evidence: "
                            + import_result.stdout.strip()
                        )
                    else:
                        if exported != EXPECTED_EXPORTS:
                            problems.append(f"installed package exports are {exported!r}")

            self.assertEqual(problems, [], "\n".join(problems))

    @needs_node_and_npm
    def test_npm_pack_omits_python_caches_after_packaged_helper_runs(self) -> None:
        assert NPM is not None
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package = temporary_root / "package"
            build_opencode_package(ROOT, package)
            helper_env = dict(os.environ)
            helper_env.pop("PYTHONDONTWRITEBYTECODE", None)
            helper_result = run(
                [sys.executable, "-c", "import plan_graph"],
                package / "scripts",
                env=helper_env,
            )
            self.assertEqual(
                helper_result.returncode,
                0,
                f"packaged helper failed:\n{helper_result.stdout}\n{helper_result.stderr}",
            )
            self.assertTrue(
                any(
                    is_python_cache(path.relative_to(package))
                    for path in (package / "scripts").rglob("*")
                ),
                "packaged helper did not leave a Python cache to exercise npm ignores",
            )
            pack_result = run(
                [NPM, "pack", "--json", "--pack-destination", str(temporary_root)],
                package,
            )
            self.assertEqual(
                pack_result.returncode,
                0,
                f"npm pack failed:\n{pack_result.stdout}\n{pack_result.stderr}",
            )
            report = json.loads(pack_result.stdout)
            self.assertEqual(len(report), 1, report)
            tarball = temporary_root / report[0]["filename"]
            with tarfile.open(tarball, "r:gz") as archive:
                caches = sorted(
                    member.name
                    for member in archive.getmembers()
                    if is_python_cache(Path(member.name))
                )
            self.assertEqual(caches, [])


if __name__ == "__main__":
    unittest.main()
