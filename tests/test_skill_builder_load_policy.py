from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests import test_skill_builder_state as support


NAME = "sample-skill"
USER_SENTENCE = "Only the user can load $sample-skill."
RESTRICTED_DESCRIPTION = f"{support.SAMPLE_DESCRIPTION} {USER_SENTENCE}"
CODEX_HIDDEN = support.fixture_codex_adapter(True)
CODEX_LISTED = support.fixture_codex_adapter(False)
CODEX_INTERFACE = (
    'interface:\n  short_description: "Build the exact sample skill"\n'
    '  default_prompt: "Use $sample-skill to build the exact sample skill."\n'
).encode("utf-8")
CODEX_BRIEF = {
    "short_description": support.CODEX_SHORT_DESCRIPTION,
    "default_prompt": support.CODEX_DEFAULT_PROMPT,
}


def policy(kind: str, skills: list[str] | None = None, packages: list[str] | None = None) -> dict[str, object]:
    return {
        "policy": kind,
        "loader_skills": skills or [],
        "loader_packages": packages or [],
    }


def skill_md(description: str | None, *, metadata: str = "", body: str = "# Sample skill\n") -> bytes:
    lines = ["---", f"name: {NAME}"]
    if description is not None:
        lines.append(f"description: {description}")
    if metadata:
        lines.append(metadata.rstrip("\n"))
    return ("\n".join(lines) + "\n---\n" + body).encode("utf-8")


RESTRICTED_SKILL = skill_md(RESTRICTED_DESCRIPTION)


def contract_v2(**overrides: object) -> dict[str, object]:
    payload = support.contract_v1_payload("1" * 64)
    payload.update(
        {
            "schema_version": "skill-builder-contract.v2",
            "load_policy": policy("agent"),
            "hosts": ["claude-code"],
            "aliases": [],
            "ambiguity": [],
            "collaboration": [],
        }
    )
    payload.update(overrides)
    return payload


def check(
    helper: ModuleType,
    skill: bytes,
    *,
    openai_yaml: bytes | None = None,
    load_policy: dict[str, object] | None = None,
    hosts: list[str] | None = None,
    description: str | None = None,
) -> None:
    load_policy = load_policy or policy("user")
    if description is None:
        description = (
            support.SAMPLE_DESCRIPTION
            if load_policy["policy"] == "agent"
            else RESTRICTED_DESCRIPTION
        )
    helper.check_candidate_entrypoint(
        skill,
        openai_yaml,
        name=NAME,
        load_policy=load_policy,
        hosts=hosts or ["claude-code"],
        description=description,
        codex_interface=CODEX_BRIEF,
    )


def design_stage(
    helper: ModuleType, tmp_path: Path, *, selection_v5: bool = False
) -> tuple[Path, str, int]:
    state_root = tmp_path / "state"
    target = tmp_path / "skills" / NAME
    started = helper.initialize_run(
        host_identity=support.host_identity(tmp_path),
        target_identity=support.target_identity(target),
        mode="create",
        authority=support.authority(),
        absence_evidence={"searched": [str(target)], "exists": False},
        overlap_map={"exact": [], "near_neighbours": []},
        git_identity={"present": False},
        state_root=state_root,
    )
    workflow_id = started["workflow_id"]
    if selection_v5:
        support.mark_run_as_selection_v5(helper, state_root, workflow_id)
    sequence = 0
    for artifact_id, artifact_type, event, stage in (
        ("baseline", "baseline-report", "capture-baseline", "baseline"),
        ("research", "research-pack", "complete-research", "research"),
        ("sieve", "evidence-sieve", "sieve-evidence", "sieve"),
        ("design", "design-record", "accept-design", "design"),
    ):
        sequence = support.advance_with_artifact(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            event=event,
            destination_stage=stage,
        )["sequence"]
    return state_root, workflow_id, sequence


def retain_contract(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    payload: dict[str, object],
) -> dict[str, object]:
    return support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="contract",
        artifact_type="skill-contract",
        payload=payload,
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )


def rewound_to_evaluation(
    helper: ModuleType, tmp_path: Path, **build: object
) -> tuple[Path, str, int, dict[str, str]]:
    """Accept one candidate, then supersede it as a repair would."""
    state_root, workflow_id, sequence, _ = support.build_candidate_stage(
        helper, tmp_path, **build
    )
    sequence = helper.invalidate_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        change_kind="candidate",
        changed_artifact_id="candidate",
        reason="repair the lowest category",
        state_root=state_root,
    )["sequence"]
    index = helper.load_run(workflow_id=workflow_id, state_root=state_root)[
        "artifact_index"
    ]
    digests = {
        name: index[name]["digest"] for name in ("contract", "confirmation", "evaluation")
    }
    return state_root, workflow_id, sequence, digests


def retain_repair_brief(
    helper: ModuleType,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    digests: dict[str, str],
    artifact_id: str,
    **overrides: object,
) -> dict[str, object]:
    return support.retain_brief(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        contract_digest=digests["contract"],
        confirmation_digest=digests["confirmation"],
        evaluation_digest=digests["evaluation"],
        cases=support.frozen_cases(),
        owned_paths=["SKILL.md"],
        overrides=overrides or None,
    )


def accept_repair_candidate(
    helper: ModuleType,
    tmp_path: Path,
    state_root: Path,
    workflow_id: str,
    sequence: int,
    digests: dict[str, str],
    artifact_id: str,
    brief_digest: str,
) -> dict[str, object]:
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    candidate = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id=artifact_id,
        artifact_type="candidate-record",
        payload=support.candidate_payload(
            tmp_path=tmp_path / artifact_id,
            candidate_id=artifact_id,
            revision=f"{artifact_id}-revision",
            contract_digest=digests["contract"],
            confirmation_digest=digests["confirmation"],
            evaluation_digest=digests["evaluation"],
            snapshot_digest=current["target_snapshot"]["snapshot_digest"],
            brief_digest=brief_digest,
        ),
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    return helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=candidate["sequence"],
        event="accept-candidate",
        destination_stage="candidate",
        artifact_ids=[artifact_id],
        state_root=state_root,
    )


@pytest.mark.parametrize(
    ("load_policy", "expected"),
    (
        (policy("user"), "Only the user can load $x."),
        (policy("agent"), None),
        (policy("user-or-skills", ["a"]), "Only the user or $a can load $x."),
        (policy("user-or-skills", ["a", "b"]), "Only the user or $a or $b can load $x."),
        (
            policy("user-or-skills", ["a", "b", "c"]),
            "Only the user or $a, $b, or $c can load $x.",
        ),
        (
            policy("user-or-skills", packages=["ExpSkill"]),
            "Only the user or another ExpSkill skill can load $x.",
        ),
        (
            policy("user-or-skills", ["a"], ["Pkg"]),
            "Only the user or $a or another Pkg skill can load $x.",
        ),
        (
            policy("user-or-skills", ["a", "b"], ["Pkg"]),
            "Only the user or $a, $b, or another Pkg skill can load $x.",
        ),
        (
            policy("user-or-skills", packages=["Pkg", "Acme Tools"]),
            "Only the user or another Pkg skill or another Acme Tools skill can load $x.",
        ),
    ),
)
def test_load_sentence_renders_every_policy_shape(
    load_policy: dict[str, object], expected: str | None
) -> None:
    helper = support.load_helper()
    assert helper.load_sentence(load_policy, "x") == expected


@pytest.mark.parametrize(
    ("load_policy", "name"),
    (
        (policy("user-or-skills", ["x"]), "x"),
        (policy("user"), "Not A Name"),
        (policy("user"), "$x"),
        (policy("user"), "pdf_tools"),
        (policy("agent"), ""),
        (policy("nobody"), "x"),
    ),
)
def test_load_sentence_rejects_self_loaders_bad_names_and_policies(
    load_policy: dict[str, object], name: str
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        helper.load_sentence(load_policy, name)


def test_load_sentence_cli_renders_the_sentence() -> None:
    request = {"load_policy": policy("user-or-skills", ["plan"], ["ExpSkill"]), "name": "design"}
    result = subprocess.run(
        [sys.executable, str(support.HELPER), "load-sentence"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "load_sentence": "Only the user or $plan or another ExpSkill skill can load $design."
    }


@pytest.mark.parametrize(
    "load_policy",
    (
        policy("user"),
        policy("agent"),
        policy("user-or-skills", ["use-expskill"]),
        policy("user-or-skills", packages=["ExpSkill"]),
    ),
)
def test_contract_v2_accepts_typed_load_policy_and_hosts(load_policy: dict[str, object]) -> None:
    helper = support.load_helper()
    helper._validate_artifact_payload(
        "skill-contract",
        contract_v2(load_policy=load_policy, hosts=["claude-code", "codex", "opencode", "hermes"]),
    )


def test_contract_v1_payload_stays_readable() -> None:
    helper = support.load_helper()
    helper._validate_artifact_payload("skill-contract", support.contract_v1_payload("1" * 64))


@pytest.mark.parametrize(
    "overrides",
    (
        {"load_policy": {"policy": "user", "loader_skills": []}},
        {"load_policy": policy("anyone")},
        {"load_policy": policy("user", ["plan"])},
        {"load_policy": policy("agent", packages=["ExpSkill"])},
        {"load_policy": policy("user-or-skills")},
        {"load_policy": policy("user-or-skills", ["Bad_Name"])},
        {"load_policy": policy("user-or-skills", ["plan", "plan"])},
        {"load_policy": policy("user-or-skills", packages=["Exp, Skill"])},
        {"hosts": []},
        {"hosts": ["vscode"]},
        {"hosts": ["codex", "codex"]},
        {"aliases": "sample"},
        {"collaboration": [""]},
    ),
)
def test_contract_v2_rejects_malformed_load_policy_hosts_and_lists(
    overrides: dict[str, object],
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        helper._validate_artifact_payload("skill-contract", contract_v2(**overrides))


def test_contract_v2_requires_every_new_field() -> None:
    helper = support.load_helper()
    for field in ("load_policy", "hosts", "aliases", "ambiguity", "collaboration"):
        payload = contract_v2()
        del payload[field]
        with pytest.raises(helper.RunStateError, match="schema"):
            helper._validate_artifact_payload("skill-contract", payload)


def test_new_runs_require_contract_v2_and_older_runs_keep_v1(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence = design_stage(helper, tmp_path / "new")
    design_digest = support.accepted_artifact(
        helper, state_root, workflow_id, "design-record"
    )[1]
    with pytest.raises(helper.RunStateError, match="semantics"):
        retain_contract(
            helper, state_root, workflow_id, sequence, support.contract_v1_payload(design_digest)
        )
    old_root, old_workflow, old_sequence = design_stage(
        helper, tmp_path / "old", selection_v5=True
    )
    old_design = support.accepted_artifact(helper, old_root, old_workflow, "design-record")[1]
    with pytest.raises(helper.RunStateError, match="semantics"):
        retain_contract(
            helper,
            old_root,
            old_workflow,
            old_sequence,
            contract_v2(design_digest=old_design),
        )


def test_contract_acceptance_rejects_a_target_that_loads_itself(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence = design_stage(helper, tmp_path)
    contract = support.contract_payload(
        helper,
        state_root,
        workflow_id,
        load_policy=policy("user-or-skills", [NAME]),
    )
    retained = retain_contract(helper, state_root, workflow_id, sequence, contract)
    with pytest.raises(helper.RunStateError, match="itself"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=retained["sequence"],
            event="accept-contract",
            destination_stage="contract",
            artifact_ids=["contract"],
            state_root=state_root,
        )


@pytest.mark.parametrize(
    ("skill", "openai_yaml", "load_policy", "hosts"),
    (
        (RESTRICTED_SKILL, None, policy("user"), ["claude-code", "hermes"]),
        (RESTRICTED_SKILL, CODEX_HIDDEN, policy("user"), ["codex"]),
        (
            skill_md(RESTRICTED_DESCRIPTION, metadata='metadata:\n  opencode/autoinvoke: "false"'),
            None,
            policy("user"),
            ["opencode"],
        ),
        (
            skill_md(RESTRICTED_DESCRIPTION, metadata="metadata:\n  opencode/autoinvoke: false"),
            CODEX_HIDDEN,
            policy("user"),
            ["codex", "opencode"],
        ),
        (skill_md(support.SAMPLE_DESCRIPTION), None, policy("agent"), ["claude-code", "opencode"]),
        (skill_md(support.SAMPLE_DESCRIPTION), CODEX_INTERFACE, policy("agent"), ["codex"]),
        (skill_md(support.SAMPLE_DESCRIPTION), CODEX_LISTED, policy("agent"), ["codex"]),
        (
            skill_md(support.SAMPLE_DESCRIPTION, metadata="metadata:\n  opencode/autoinvoke: 'true'"),
            None,
            policy("agent"),
            ["opencode"],
        ),
    ),
)
def test_candidate_checks_pass_matching_descriptions_and_host_flags(
    skill: bytes,
    openai_yaml: bytes | None,
    load_policy: dict[str, object],
    hosts: list[str],
) -> None:
    helper = support.load_helper()
    check(helper, skill, openai_yaml=openai_yaml, load_policy=load_policy, hosts=hosts)


def test_candidate_checks_render_package_loaders_into_the_description() -> None:
    helper = support.load_helper()
    loaders = policy("user-or-skills", ["use-expskill"], ["ExpSkill"])
    description = (
        f"{support.SAMPLE_DESCRIPTION} Only the user or $use-expskill or another "
        "ExpSkill skill can load $sample-skill."
    )
    check(helper, skill_md(description), load_policy=loaders, description=description)


@pytest.mark.parametrize(
    ("skill", "description", "load_policy"),
    (
        (skill_md(support.SAMPLE_DESCRIPTION), support.SAMPLE_DESCRIPTION, policy("user")),
        (skill_md(USER_SENTENCE), USER_SENTENCE, policy("user")),
        (
            skill_md(f"{USER_SENTENCE} {support.SAMPLE_DESCRIPTION}"),
            f"{USER_SENTENCE} {support.SAMPLE_DESCRIPTION}",
            policy("user"),
        ),
        (
            skill_md(f"{RESTRICTED_DESCRIPTION} {USER_SENTENCE}"),
            f"{RESTRICTED_DESCRIPTION} {USER_SENTENCE}",
            policy("user"),
        ),
        (skill_md(RESTRICTED_DESCRIPTION), RESTRICTED_DESCRIPTION, policy("agent")),
        (skill_md(None), RESTRICTED_DESCRIPTION, policy("user")),
        (skill_md(RESTRICTED_DESCRIPTION), f"{support.SAMPLE_DESCRIPTION}  {USER_SENTENCE}", policy("user")),
    ),
)
def test_candidate_checks_reject_a_description_off_its_load_policy_or_brief(
    skill: bytes, description: str, load_policy: dict[str, object]
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="description"):
        check(helper, skill, load_policy=load_policy, description=description)


@pytest.mark.parametrize(
    ("openai_yaml", "load_policy"),
    (
        (None, policy("user")),
        (b"interface:\n  display_name: \"Sample\"\n", policy("user")),
        (CODEX_LISTED, policy("user")),
        (CODEX_HIDDEN, policy("agent")),
    ),
)
def test_candidate_checks_reject_a_codex_flag_off_its_load_policy(
    openai_yaml: bytes | None, load_policy: dict[str, object]
) -> None:
    helper = support.load_helper()
    skill = skill_md(
        support.SAMPLE_DESCRIPTION if load_policy["policy"] == "agent" else RESTRICTED_DESCRIPTION
    )
    with pytest.raises(helper.RunStateError, match="allow_implicit_invocation"):
        check(helper, skill, openai_yaml=openai_yaml, load_policy=load_policy, hosts=["codex"])


@pytest.mark.parametrize(
    ("metadata", "load_policy"),
    (
        ("", policy("user")),
        ('metadata:\n  opencode/autoinvoke: "true"', policy("user")),
        ('metadata:\n  opencode/autoinvoke: "no"', policy("user")),
        ('metadata:\n  opencode/autoinvoke: "false"', policy("agent")),
    ),
)
def test_candidate_checks_reject_an_opencode_flag_off_its_load_policy(
    metadata: str, load_policy: dict[str, object]
) -> None:
    helper = support.load_helper()
    description = (
        support.SAMPLE_DESCRIPTION if load_policy["policy"] == "agent" else RESTRICTED_DESCRIPTION
    )
    with pytest.raises(helper.RunStateError, match="autoinvoke"):
        check(
            helper,
            skill_md(description, metadata=metadata),
            load_policy=load_policy,
            hosts=["opencode"],
        )


@pytest.mark.parametrize(
    "openai_yaml",
    (
        b"policy:\n  allow_implicit_invocation: false\npolicy:\n  allow_implicit_invocation: false\n",
        b"policy:\n  allow_implicit_invocation: false\n  allow_implicit_invocation: false\n",
        b"policy: {allow_implicit_invocation: false}\n",
        b"policy:\n  allow_implicit_invocation: no\n",
        b"policy:\n  allow_implicit_invocation: false # hidden\n",
        b"policy:\n    allow_implicit_invocation: false\n",
        b"policy:\n\tallow_implicit_invocation: false\n",
        b"policy:\r\n  allow_implicit_invocation: false\r\n",
        b"  allow_implicit_invocation: false\n",
        b"interface:\n  short_description: \"open\npolicy:\n  allow_implicit_invocation: false\n",
    ),
)
def test_candidate_checks_fail_closed_on_ambiguous_codex_yaml(openai_yaml: bytes) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        check(helper, RESTRICTED_SKILL, openai_yaml=openai_yaml, hosts=["codex"])


@pytest.mark.parametrize(
    "skill",
    (
        b"---\nname: sample-skill\ndescription: >\n  Build it. Only the user can load $sample-skill.\n---\n",
        b"---\nname: sample-skill\ndescription: Build the exact\n  sample skill. Only the user can load $sample-skill.\n---\n",
        RESTRICTED_SKILL.replace(b"name: sample-skill\n", b"description: other\nname: sample-skill\n"),
        RESTRICTED_SKILL.replace(b"\n---\n# Sample", b"\n# Sample"),
        RESTRICTED_SKILL.replace(b"\n", b"\r\n"),
        b"\xef\xbb\xbf" + RESTRICTED_SKILL,
        RESTRICTED_SKILL.replace(b"name: sample-skill", b'name: "sample\ndescription: x"'),
        skill_md(RESTRICTED_DESCRIPTION, metadata='metadata:\n  opencode/autoinvoke: "false"\n  opencode/autoinvoke: "false"'),
        skill_md(RESTRICTED_DESCRIPTION, metadata="metadata:\n  opencode:\n    autoinvoke: false"),
        skill_md(RESTRICTED_DESCRIPTION, metadata="metadata: {opencode/autoinvoke: false}"),
        skill_md('"Build it. Only the user can load $sample-skill."'.replace("Build", 'Bu"ild')),
        skill_md("Build: it. Only the user can load $sample-skill."),
    ),
)
def test_candidate_checks_fail_closed_on_ambiguous_frontmatter(skill: bytes) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        check(helper, skill, hosts=["opencode"])


@pytest.mark.parametrize(
    "body",
    (
        "$sample-skill is standalone and explicit-only.\n",
        "Use this skill only when the user names it.\n",
        "Stay inactive unless the request names this skill.\n",
        "Only the user's explicit request activates this skill.\n",
        "Load `$sample-skill` only on an explicit request.\n",
        "## Boundary\n\n- Read the plan first. Activate $sample-skill only on request.\n",
        "| Rule |\n|---|\n| Use $sample-skill only when asked. |\n",
    ),
)
def test_self_restatement_check_finds_load_policy_restatements(body: str) -> None:
    helper = support.load_helper()
    assert helper.restatement_sentences(body, NAME)
    with pytest.raises(helper.RunStateError, match="restates"):
        check(helper, skill_md(RESTRICTED_DESCRIPTION, body=body))


@pytest.mark.parametrize(
    "body",
    (
        "Use the helper to validate the request.\n",
        "Use this skill's references before the first edit.\n",
        'User: "Use $sample-skill on toggle naming." Ask which toggle first.\n',
        "```text\nUse $sample-skill only when asked.\n```\n",
        "> Use this skill only when asked.\n",
        "Run `use this skill` as the literal command.\n",
        "Use $use-expskill to route other requests.\n",
        "After loading this skill, read the plan.\n",
        "Return $sample-skill output to the caller.\n",
    ),
)
def test_self_restatement_check_spares_instructions_examples_and_code(body: str) -> None:
    helper = support.load_helper()
    assert helper.restatement_sentences(body, NAME) == []
    check(helper, skill_md(RESTRICTED_DESCRIPTION, body=body))


def test_restricted_candidate_with_hide_flags_is_accepted(tmp_path: Path) -> None:
    helper = support.load_helper()
    skill = skill_md(
        RESTRICTED_DESCRIPTION, metadata='metadata:\n  opencode/autoinvoke: "false"'
    )
    state_root, workflow_id, sequence, _ = support.build_candidate_stage(
        helper,
        tmp_path,
        load_policy=policy("user"),
        hosts=["claude-code", "codex", "opencode", "hermes"],
        candidate_loaded_skill_bytes=skill,
        candidate_additional_skill_files={"agents/openai.yaml": CODEX_HIDDEN},
    )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"
    run = state_root / "live" / workflow_id
    assert helper._artifact_payload_json(run, "candidate")["checker_version"] == (
        helper.CANDIDATE_CHECKER_VERSION
    )
    brief = helper._artifact_payload_json(run, "brief")
    assert brief["load_sentence"] == USER_SENTENCE
    assert brief["host_flags"] == {
        "claude-code": {},
        "codex": {
            "allow_implicit_invocation": False,
            "adapter_path": "agents/openai.yaml",
            **CODEX_BRIEF,
        },
        "opencode": {"opencode/autoinvoke": "false"},
        "hermes": {},
    }


@pytest.mark.parametrize(
    ("skill", "extra_files", "match"),
    (
        (RESTRICTED_SKILL, {}, "allow_implicit_invocation"),
        (RESTRICTED_SKILL, {"agents/openai.yaml": CODEX_LISTED}, "allow_implicit_invocation"),
        (
            skill_md(RESTRICTED_DESCRIPTION, body="Use this skill only when asked.\n"),
            {"agents/openai.yaml": CODEX_HIDDEN},
            "restates",
        ),
        (skill_md(support.SAMPLE_DESCRIPTION), {"agents/openai.yaml": CODEX_HIDDEN}, "description"),
    ),
)
def test_candidate_acceptance_rejects_failed_entrypoint_checks(
    tmp_path: Path, skill: bytes, extra_files: dict[str, bytes], match: str
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match=match):
        support.build_candidate_stage(
            helper,
            tmp_path,
            load_policy=policy("user"),
            hosts=["codex"],
            candidate_loaded_skill_bytes=skill,
            candidate_additional_skill_files=extra_files or None,
        )


def test_entrypoint_checks_run_at_acceptance_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, candidate = support.build_candidate_stage(
        helper, tmp_path
    )

    def stricter(*_: object, **__: object) -> None:
        raise helper.RunStateError("a later checker rejects this body")

    monkeypatch.setattr(helper, "check_candidate_entrypoint", stricter)
    monkeypatch.setattr(helper, "load_sentence", stricter)
    monkeypatch.setattr(helper, "_check_brief_policy", stricter)
    monkeypatch.setattr(helper, "_check_accepted_contract", stricter)
    monkeypatch.setattr(helper, "CANDIDATE_CHECKER_VERSION", "skill-builder-candidate-checks.v2")
    monkeypatch.setattr(
        helper,
        "_CANDIDATE_CHECKER_VERSIONS",
        frozenset({"skill-builder-candidate-checks.v1", "skill-builder-candidate-checks.v2"}),
    )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"
    trials = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=sequence,
        artifact_id="trials",
        artifact_type="trial-pack",
        payload=support.trial_payload(candidate["artifact_digest"], cases=support.frozen_cases()),
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
        frozen_trial_cases=support.frozen_cases(),
    )
    advanced = helper.transition_run(
        workflow_id=workflow_id,
        expected_sequence=trials["sequence"],
        event="complete-trials",
        destination_stage="trials",
        artifact_ids=["trials"],
        state_root=state_root,
    )
    assert advanced["stage"] == "trials"


def test_candidate_acceptance_requires_the_current_checker_version(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, digests = rewound_to_evaluation(helper, tmp_path)
    brief = retain_repair_brief(helper, state_root, workflow_id, sequence, digests, "brief-2")
    current = helper.load_run(workflow_id=workflow_id, state_root=state_root)
    payload = support.candidate_payload(
        tmp_path=tmp_path / "candidate-2",
        candidate_id="candidate-2",
        revision="candidate-2",
        contract_digest=digests["contract"],
        confirmation_digest=digests["confirmation"],
        evaluation_digest=digests["evaluation"],
        snapshot_digest=current["target_snapshot"]["snapshot_digest"],
        brief_digest=brief["artifact_digest"],
    )
    payload["checker_version"] = "skill-builder-candidate-checks.v0"
    retained = support.retain_json(
        helper,
        state_root=state_root,
        workflow_id=workflow_id,
        sequence=brief["sequence"],
        artifact_id="candidate-2",
        artifact_type="candidate-record",
        payload=payload,
        input_bindings=support.current_bindings(helper, state_root, workflow_id),
    )
    with pytest.raises(helper.RunStateError, match="checker"):
        helper.transition_run(
            workflow_id=workflow_id,
            expected_sequence=retained["sequence"],
            event="accept-candidate",
            destination_stage="candidate",
            artifact_ids=["candidate-2"],
            state_root=state_root,
        )


def test_brief_schema_holds_only_the_typed_fields() -> None:
    helper = support.load_helper()
    valid = support.brief_payload(
        contract_digest="1" * 64,
        confirmation_digest="2" * 64,
        evaluation_digest="3" * 64,
        cases=support.frozen_cases(),
        owned_paths=["SKILL.md"],
    )
    helper._validate_artifact_payload("implementer-brief", valid)
    invalid: list[dict[str, object]] = []
    for field in valid:
        missing = dict(valid)
        del missing[field]
        invalid.append(missing)
    invalid.extend(
        {**valid, **change}
        for change in (
            {"non_triggers": ["unrelated request"]},
            {"description": "Build it.\nAnd more."},
            {"body_rules": []},
            {"owned_paths": []},
            {"owned_paths": ["SKILL.md", "SKILL.md"]},
            {"host_flags": {"vscode": {}}},
            {"irreversible_prohibitions": [{"forbidden_action": "remote delivery", "action": "push"}]},
            {"visible_cases": valid["visible_cases"] * 2},
            {"visible_cases": [{"case_id": "case-1", "request": "", "expected": "x"}]},
            {"schema_version": "skill-builder-implementer-brief.v2"},
        )
    )
    for payload in invalid:
        with pytest.raises(helper.RunStateError):
            helper._validate_artifact_payload("implementer-brief", payload)


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        ({"load_sentence": USER_SENTENCE}, "load sentence"),
        ({"description": RESTRICTED_DESCRIPTION}, "description"),
        (
            {"host_flags": {"claude-code": {}, "opencode": {"opencode/autoinvoke": "false"}}},
            "host flags",
        ),
        ({"host_flags": {"claude-code": {"opencode/autoinvoke": "false"}}}, "host flags"),
        (
            {
                "irreversible_prohibitions": [
                    {
                        "forbidden_action": "delete the repository",
                        "action": "delete the repository",
                        "reason": "it cannot be restored",
                        "alternative": "leave it in place",
                    }
                ]
            },
            "prohibits",
        ),
        (
            {"visible_cases": [{"case_id": "case-2", "request": "case-2:request\n", "expected": "x"}]},
            "visible loaded",
        ),
        (
            {"visible_cases": [{"case_id": "case-1", "request": "another request", "expected": "x"}]},
            "visible loaded",
        ),
        ({"owned_paths": ["SKILL.md", "references"]}, "owned paths"),
        ({"contract_digest": "f" * 64}, "brief"),
        ({"evaluation_digest": "f" * 64}, "brief"),
    ),
)
def test_candidate_entry_rejects_a_brief_off_its_contract_or_pack(
    tmp_path: Path, overrides: dict[str, object], match: str
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match=match):
        support.build_candidate_stage(helper, tmp_path, brief_overrides=overrides)


def test_brief_rejects_a_visible_selection_case(tmp_path: Path) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="visible loaded"):
        support.build_candidate_stage(
            helper,
            tmp_path,
            selection={"case-1": "candidate"},
            brief_overrides={
                "visible_cases": [
                    {"case_id": "case-1", "request": "case-1:request\n", "expected": "x"}
                ]
            },
        )


def test_candidate_must_bind_exactly_one_brief(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, digests = rewound_to_evaluation(helper, tmp_path)
    first = retain_repair_brief(helper, state_root, workflow_id, sequence, digests, "brief-2")
    second = retain_repair_brief(
        helper, state_root, workflow_id, first["sequence"], digests, "brief-3"
    )
    with pytest.raises(helper.RunStateError, match="exactly one implementer brief"):
        accept_repair_candidate(
            helper,
            tmp_path,
            state_root,
            workflow_id,
            second["sequence"],
            digests,
            "candidate-2",
            first["artifact_digest"],
        )


def test_candidate_brief_digest_must_name_its_bound_brief(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, digests = rewound_to_evaluation(helper, tmp_path)
    brief = retain_repair_brief(helper, state_root, workflow_id, sequence, digests, "brief-2")
    with pytest.raises(helper.RunStateError, match="brief binding is stale"):
        accept_repair_candidate(
            helper,
            tmp_path,
            state_root,
            workflow_id,
            brief["sequence"],
            digests,
            "candidate-2",
            "e" * 64,
        )


def test_repair_candidate_needs_a_new_brief(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, digests = rewound_to_evaluation(helper, tmp_path)
    index = helper.load_run(workflow_id=workflow_id, state_root=state_root)["artifact_index"]
    assert index["brief"]["derived_status"] == "invalidated"
    stale_bindings = support.current_bindings(helper, state_root, workflow_id) + [
        {"artifact_id": "brief", "digest": index["brief"]["digest"]}
    ]
    with pytest.raises(helper.RunStateError, match="invalidated"):
        support.retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="candidate-2",
            artifact_type="candidate-record",
            payload=support.candidate_payload(
                tmp_path=tmp_path / "candidate-2",
                candidate_id="candidate-2",
                revision="candidate-2",
                contract_digest=digests["contract"],
                confirmation_digest=digests["confirmation"],
                evaluation_digest=digests["evaluation"],
                snapshot_digest=helper.load_run(
                    workflow_id=workflow_id, state_root=state_root
                )["target_snapshot"]["snapshot_digest"],
                brief_digest=index["brief"]["digest"],
            ),
            input_bindings=stale_bindings,
        )
    brief = retain_repair_brief(helper, state_root, workflow_id, sequence, digests, "brief-2")
    accepted = accept_repair_candidate(
        helper,
        tmp_path,
        state_root,
        workflow_id,
        brief["sequence"],
        digests,
        "candidate-2",
        brief["artifact_digest"],
    )
    assert accepted["stage"] == "candidate"
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"


def test_runs_before_contract_v2_reject_briefs_and_finalize_without_one(
    tmp_path: Path,
) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = support.build_verified_stage(
        helper, tmp_path, selection_v5=True
    )
    run = state_root / "live" / workflow_id
    assert helper._artifact_payload_json(run, "contract")["schema_version"] == (
        "skill-builder-contract.v1"
    )
    assert "brief_digest" not in helper._artifact_payload_json(run, "candidate")
    with pytest.raises(helper.RunStateError, match="semantics"):
        support.retain_json(
            helper,
            state_root=state_root,
            workflow_id=workflow_id,
            sequence=sequence,
            artifact_id="late-brief",
            artifact_type="implementer-brief",
            payload=support.brief_payload(
                contract_digest="1" * 64,
                confirmation_digest="2" * 64,
                evaluation_digest="3" * 64,
                cases=support.frozen_cases(),
                owned_paths=["SKILL.md"],
            ),
        )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    assert finalized["stage"] == "finalized"
    assert helper.recover_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "finalized"


def restricted_package(
    hosts: list[str], description: str = RESTRICTED_DESCRIPTION
) -> dict[str, bytes]:
    metadata = 'metadata:\n  opencode/autoinvoke: "false"' if "opencode" in hosts else ""
    package = {"SKILL.md": skill_md(description, metadata=metadata)}
    if "codex" in hosts:
        package["agents/openai.yaml"] = CODEX_HIDDEN
    return package


def verified_with_policy(
    helper: ModuleType,
    tmp_path: Path,
    *,
    load_policy: dict[str, object],
    hosts: list[str],
    selection: dict[str, str],
) -> tuple[Path, str, int, dict[str, object]]:
    package = (
        restricted_package(
            hosts,
            f"{support.SAMPLE_DESCRIPTION} {support.fixture_load_sentence(load_policy)}",
        )
        if load_policy["policy"] != "agent"
        else {"SKILL.md": support.CANDIDATE_SKILL_BYTES}
        | ({"agents/openai.yaml": CODEX_INTERFACE} if "codex" in hosts else {})
    )
    extra = {path: data for path, data in package.items() if path != "SKILL.md"}
    return support.build_verified_stage(
        helper,
        tmp_path,
        load_policy=load_policy,
        hosts=hosts,
        selection=selection,
        candidate_loaded_skill_bytes=package["SKILL.md"],
        candidate_additional_skill_files=extra or None,
        trial_loaded_skill_files=package,
    )


@pytest.mark.parametrize("hosts", (["codex"], ["opencode", "hermes"], ["codex", "opencode"]))
def test_hide_flag_hosts_score_restricted_selection_rows_without_trials(
    tmp_path: Path, hosts: list[str]
) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = verified_with_policy(
        helper, tmp_path, load_policy=policy("user"), hosts=hosts, selection={}
    )
    finalized = helper.finalize_run(
        workflow_id=workflow_id,
        expected_sequence=sequence,
        release_artifact_id="release",
        state_root=state_root,
    )
    assert finalized["stage"] == "finalized"


@pytest.mark.parametrize(
    ("load_policy", "hosts"),
    (
        (policy("user"), ["claude-code", "codex"]),
        (policy("user"), ["claude-code", "hermes"]),
        (policy("agent"), ["codex"]),
        (policy("agent"), ["claude-code"]),
    ),
)
def test_claude_code_and_agent_targets_still_need_selection_trials(
    tmp_path: Path, load_policy: dict[str, object], hosts: list[str]
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="selection trials"):
        verified_with_policy(
            helper, tmp_path, load_policy=load_policy, hosts=hosts, selection={}
        )


def test_restricted_claude_code_target_passes_with_selection_trials(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, sequence, _ = verified_with_policy(
        helper,
        tmp_path,
        load_policy=policy("user"),
        hosts=["claude-code", "codex"],
        selection=support.SCORED_SELECTION,
    )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "verified"


@pytest.mark.parametrize("name", ("PDF", "pdf_tools", "3d-print"))
def test_agent_load_policy_renders_no_sentence_for_any_target_name(name: str) -> None:
    helper = support.load_helper()
    assert helper.load_sentence(policy("agent"), name) is None


def test_restricted_load_sentence_allows_a_leading_digit() -> None:
    helper = support.load_helper()
    assert helper.load_sentence(policy("user"), "3d-print") == "Only the user can load $3d-print."


HIDE_METADATA = 'metadata:\n  opencode/autoinvoke: "false"'


def frontmatter(*lines: str) -> bytes:
    return ("---\n" + "\n".join(lines) + "\n---\n# Sample skill\n").encode("utf-8")


@pytest.mark.parametrize(
    "skill",
    (
        frontmatter(
            f"name: {NAME}",
            f"description: {RESTRICTED_DESCRIPTION}",
            f"license: &a 'x\n{HIDE_METADATA}\n  z: y'",
        ),
        frontmatter(
            f"name: {NAME}",
            f"description: {RESTRICTED_DESCRIPTION}",
            f"license: !!str 'x\n{HIDE_METADATA}\n  z: y'",
        ),
        frontmatter(
            f"name: {NAME}",
            f"license: &a 'x\ndescription: {RESTRICTED_DESCRIPTION}\n{HIDE_METADATA}\n  z: y'",
        ),
        frontmatter(f"name: {NAME}", f"description: {RESTRICTED_DESCRIPTION}", "license: |\n  MIT", HIDE_METADATA),
        frontmatter(f"name: {NAME}", f"description: {RESTRICTED_DESCRIPTION}", "license:\n  kind: MIT", HIDE_METADATA),
        frontmatter(f"name: {NAME}", f"description: {RESTRICTED_DESCRIPTION}", "? 'x", HIDE_METADATA),
        frontmatter(
            f"name: {NAME} description: Build it. license: |",
            f"description: {RESTRICTED_DESCRIPTION}",
            HIDE_METADATA,
        ),
        frontmatter(f"name: {NAME}\u0085x: y", f"description: {RESTRICTED_DESCRIPTION}", HIDE_METADATA),
        frontmatter(f"name: {NAME}: x", f"description: {RESTRICTED_DESCRIPTION}", HIDE_METADATA),
    ),
)
def test_candidate_checks_fail_closed_on_values_that_hide_later_lines(skill: bytes) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        check(helper, skill, hosts=["opencode"])


@pytest.mark.parametrize(
    "openai_yaml",
    (
        b"zzz: &a 'x\npolicy:\n  allow_implicit_invocation: false\n  z: y'\n",
        b"interface: &a 'x\npolicy:\n  allow_implicit_invocation: false\n  z: y'\n",
        b"interface: !!str 'x\npolicy:\n  allow_implicit_invocation: false\n  z: y'\n",
        b"zzz:\n  - - 'x\npolicy:\n  allow_implicit_invocation: false\n  z: y'\n",
        "policy:\n  x: y   allow_implicit_invocation: true zzz: |\n"
        "  allow_implicit_invocation: false\n".encode("utf-8"),
        "policy:\n  allow_implicit_invocation: false\u0085  allow_implicit_invocation: true\n".encode(
            "utf-8"
        ),
        "policy:\n  allow_implicit_invocation: false zzz: y\n".encode("utf-8"),
        CODEX_HIDDEN.replace(b"to build the exact", b"to build: the exact"),
        b"interface: 5\npolicy:\n  allow_implicit_invocation: false\n",
        b"policy:\n  allow_implicit_invocation: false\n  products: codex\n",
        b"dependencies:\n  tools: x\npolicy:\n  allow_implicit_invocation: false\n",
        b'policy:\n  allow_implicit_invocation: "false"\n',
        b"policy:\n  allow_implicit_invocation: false\n  x: y\n",
        b"zzz: y\n" + CODEX_HIDDEN,
        CODEX_HIDDEN + b"dependencies:\n",
    ),
)
def test_candidate_checks_fail_closed_on_codex_yaml_the_host_reads_differently(
    openai_yaml: bytes,
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError):
        check(helper, RESTRICTED_SKILL, openai_yaml=openai_yaml, hosts=["codex"])


def test_every_repository_codex_adapter_passes_the_strict_parser() -> None:
    helper = support.load_helper()
    adapters = sorted(
        (support.ROOT / "plugins/expskill/codex/skill-adapters").glob("*/agents/openai.yaml")
    )
    assert adapters
    for adapter in adapters:
        implicit, interface = helper._codex_adapter(adapter.read_bytes())
        assert isinstance(implicit, bool)
        assert interface["default_prompt"].startswith(f"Use ${adapter.parent.parent.name}")


@pytest.mark.parametrize(
    ("skill", "load_policy", "hosts", "match"),
    (
        (RESTRICTED_SKILL.replace(b"name: sample-skill", b"name: other-skill"), policy("user"), ["claude-code"], "name"),
        (
            skill_md(RESTRICTED_DESCRIPTION, metadata="disable-model-invocation: true"),
            policy("user"),
            ["claude-code"],
            "disable-model-invocation",
        ),
        (
            skill_md(support.SAMPLE_DESCRIPTION, metadata="disable-model-invocation: false"),
            policy("agent"),
            ["claude-code", "opencode"],
            "disable-model-invocation",
        ),
        (
            skill_md(RESTRICTED_DESCRIPTION, metadata="when_to_use: Use whenever samples come up."),
            policy("user"),
            ["claude-code"],
            "listing",
        ),
    ),
)
def test_candidate_checks_reject_frontmatter_that_hides_or_renames_the_candidate(
    skill: bytes, load_policy: dict[str, object], hosts: list[str], match: str
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match=match):
        check(helper, skill, load_policy=load_policy, hosts=hosts)


def test_when_to_use_stays_allowed_where_no_load_sentence_can_be_cut() -> None:
    helper = support.load_helper()
    extra = "when_to_use: Use whenever samples come up."
    check(helper, skill_md(support.SAMPLE_DESCRIPTION, metadata=extra), load_policy=policy("agent"))
    check(helper, skill_md(RESTRICTED_DESCRIPTION, metadata=extra), hosts=["hermes"])


@pytest.mark.parametrize(
    "description",
    (
        f"{'Build the exact sample skill and more ' * 45}now. {USER_SENTENCE}",
        f"Build the exact sample skill {USER_SENTENCE}",
        f"Build the exact sample skill. The user alone may invoke $sample-skill. {USER_SENTENCE}",
    ),
    ids=("over-listing-limit", "unpunctuated-prefix", "prefix-names-target"),
)
def test_candidate_checks_reject_a_description_whose_load_sentence_can_be_lost(
    description: str,
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="load sentence"):
        check(helper, skill_md(description), description=description)


def test_candidate_checks_accept_any_closing_punctuation_before_the_load_sentence() -> None:
    helper = support.load_helper()
    description = f"Build the exact sample skill! {USER_SENTENCE}"
    check(helper, skill_md(description), description=description)


@pytest.mark.parametrize(
    "body",
    (
        "Only the user loads $sample-skill.\n",
        "This skill is used only on an explicit request.\n",
        "It uses $sample-skill only when the user names it.\n",
    ),
)
def test_self_restatement_check_counts_inflected_load_and_use(body: str) -> None:
    helper = support.load_helper()
    assert helper.restatement_sentences(body, NAME)


@pytest.mark.parametrize(
    "body",
    (
        f'"{USER_SENTENCE}"\n',
        f"`{USER_SENTENCE}`\n",
        "Only the user can\nload $sample-skill.\n",
    ),
)
def test_candidate_checks_reject_the_load_sentence_in_the_body(body: str) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="restates"):
        check(helper, skill_md(RESTRICTED_DESCRIPTION, body=body))


def test_self_restatement_check_spares_a_namesake_check() -> None:
    helper = support.load_helper()
    assert helper.restatement_sentences("Confirm you loaded this skill, not a namesake.\n", NAME) == []


PROHIBITION = {
    "forbidden_action": "remote delivery",
    "action": "push the sample to a remote",
    "reason": "a pushed sample cannot be recalled",
    "alternative": "leave the sample in the isolated candidate",
}


@pytest.mark.parametrize(
    "overrides",
    (
        {"body_rules": ["Decline every unrelated request."]},
        {"body_rules": ["Skip AUTOMATIC\n  installation."]},
        {"body_rules": ["Never do remote delivery."]},
        {"resources": ["unrelated request"]},
        {"description": "Build the exact sample skill without automatic installation."},
        {"irreversible_prohibitions": [dict(PROHIBITION, reason="it is an unrelated request")]},
        {"irreversible_prohibitions": [dict(PROHIBITION, alternative="defer remote delivery")]},
    ),
)
def test_candidate_acceptance_rejects_a_brief_carrying_withheld_lists(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="withheld"):
        support.build_candidate_stage(helper, tmp_path, brief_overrides=overrides)


def test_a_prohibition_may_name_its_own_forbidden_action(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, _, _ = support.build_candidate_stage(
        helper,
        tmp_path,
        brief_overrides={
            "irreversible_prohibitions": [dict(PROHIBITION, action="remote delivery")]
        },
    )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"


@pytest.mark.parametrize(
    ("expected", "accepted"),
    (("all assertions pass", True), ("returns pass", True), ("Decline: unrelated request", False)),
)
def test_brief_visible_case_expectations_come_from_the_frozen_case(
    tmp_path: Path, expected: str, accepted: bool
) -> None:
    helper = support.load_helper()
    cases = [
        dict(case, expected=expected)
        for case in support.visible_loaded_brief_cases(support.frozen_cases())
    ]
    if accepted:
        support.build_candidate_stage(helper, tmp_path, brief_overrides={"visible_cases": cases})
        return
    with pytest.raises(helper.RunStateError, match="frozen expectations"):
        support.build_candidate_stage(helper, tmp_path, brief_overrides={"visible_cases": cases})


def codex_brief(**changes: object) -> dict[str, object]:
    flags = support.fixture_host_flags(policy("user"), ["codex"])
    flags["codex"] = {**flags["codex"], **changes}
    return {"host_flags": flags}


@pytest.mark.parametrize(
    ("brief_overrides", "adapter", "match"),
    (
        (codex_brief(default_prompt="Run $sample-skill now."), CODEX_HIDDEN, "adapter rules"),
        (
            codex_brief(short_description="Build $sample-skill"),
            CODEX_HIDDEN,
            "adapter rules",
        ),
        (
            codex_brief(short_description="Only the user can load $x"),
            CODEX_HIDDEN,
            "adapter rules",
        ),
        (
            None,
            support.fixture_codex_adapter(True, short_description="Build a sample"),
            "interface text",
        ),
        (codex_brief(allow_implicit_invocation=True), CODEX_HIDDEN, "host flags"),
    ),
)
def test_candidate_acceptance_checks_codex_adapter_text_against_the_brief(
    tmp_path: Path,
    brief_overrides: dict[str, object] | None,
    adapter: bytes,
    match: str,
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match=match):
        support.build_candidate_stage(
            helper,
            tmp_path,
            load_policy=policy("user"),
            hosts=["codex"],
            candidate_loaded_skill_bytes=RESTRICTED_SKILL,
            candidate_additional_skill_files={"agents/openai.yaml": adapter},
            brief_overrides=brief_overrides,
        )


@pytest.mark.parametrize(
    "adapter_path",
    (
        "/abs/agents/openai.yaml",
        "agents/../agents/openai.yaml",
        "../../etc/passwd",
        "agents/openai.yml",
        "..\\agents\\openai.yaml",
    ),
)
def test_brief_rejects_an_unsafe_codex_adapter_path(adapter_path: str) -> None:
    helper = support.load_helper()
    payload = support.brief_payload(
        contract_digest="1" * 64,
        confirmation_digest="2" * 64,
        evaluation_digest="3" * 64,
        cases=support.frozen_cases(),
        owned_paths=["SKILL.md"],
        host_flags=support.fixture_host_flags(policy("user"), ["codex"], adapter_path),
    )
    with pytest.raises(helper.RunStateError, match="adapter path"):
        helper._validate_artifact_payload("implementer-brief", payload)


def test_candidate_acceptance_reads_an_adapter_kept_outside_the_package(tmp_path: Path) -> None:
    helper = support.load_helper()
    state_root, workflow_id, _, _ = support.build_candidate_stage(
        helper,
        tmp_path,
        load_policy=policy("user"),
        hosts=["codex"],
        candidate_loaded_skill_bytes=RESTRICTED_SKILL,
        external_codex_adapter=CODEX_HIDDEN,
        retained_codex_adapter=CODEX_HIDDEN,
    )
    assert helper.load_run(workflow_id=workflow_id, state_root=state_root)["stage"] == "candidate"
    brief = helper._artifact_payload_json(state_root / "live" / workflow_id, "brief")
    assert brief["host_flags"]["codex"]["adapter_path"] == support.EXTERNAL_CODEX_ADAPTER_PATH


@pytest.mark.parametrize(
    ("external", "retained", "match"),
    (
        (CODEX_HIDDEN, None, "isolated copy"),
        (CODEX_HIDDEN, CODEX_LISTED, "isolated copy"),
        (None, CODEX_HIDDEN, "isolated copy"),
        (CODEX_LISTED, CODEX_LISTED, "allow_implicit_invocation"),
    ),
)
def test_candidate_acceptance_fails_closed_on_an_outside_adapter_mismatch(
    tmp_path: Path, external: bytes | None, retained: bytes | None, match: str
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match=match):
        support.build_candidate_stage(
            helper,
            tmp_path,
            load_policy=policy("user"),
            hosts=["codex"],
            candidate_loaded_skill_bytes=RESTRICTED_SKILL,
            external_codex_adapter=external,
            retained_codex_adapter=retained,
            brief_overrides=None
            if external is not None
            else codex_brief(adapter_path=support.EXTERNAL_CODEX_ADAPTER_PATH),
        )


def test_an_in_package_adapter_path_ignores_an_outside_adapter(tmp_path: Path) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="allow_implicit_invocation"):
        support.build_candidate_stage(
            helper,
            tmp_path,
            load_policy=policy("user"),
            hosts=["codex"],
            candidate_loaded_skill_bytes=RESTRICTED_SKILL,
            external_codex_adapter=CODEX_HIDDEN,
            retained_codex_adapter=CODEX_HIDDEN,
            brief_overrides=codex_brief(adapter_path="agents/openai.yaml"),
        )


def test_confirmation_records_the_hermes_limitation_for_a_restricted_target(
    tmp_path: Path,
) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="Hermes"):
        support.build_candidate_stage(
            helper,
            tmp_path,
            load_policy=policy("user"),
            hosts=["hermes"],
            candidate_loaded_skill_bytes=RESTRICTED_SKILL,
            confirmation_limitations=[],
        )


def test_hermes_runs_selection_trials_for_named_loaders(tmp_path: Path) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="selection trials"):
        verified_with_policy(
            helper,
            tmp_path,
            load_policy=policy("user-or-skills", ["use-expskill"]),
            hosts=["hermes"],
            selection={},
        )


@pytest.mark.parametrize("character", ("\u2028", "\u2029", "\u0085", "\x0c", "\ufeff", "\x1b"))
def test_candidate_text_rejects_hidden_line_breaks_and_controls(character: str) -> None:
    helper = support.load_helper()
    with pytest.raises(helper.RunStateError, match="line break"):
        helper._strict_text(f"name: x{character}y\n".encode("utf-8"), "candidate file")
