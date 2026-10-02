import json
from pathlib import Path


def register(ctx):
    root = Path(__file__).resolve().parent
    policy_path = root / "assets/authoring-runtime.json"
    if not policy_path.is_file():
        policy_path = root.parent / "content/policies/authoring-runtime.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "instructions"}:
        raise ValueError("Authoring runtime policy has unexpected fields")
    if policy["schema_version"] != "authoring-runtime.v1":
        raise ValueError("Authoring runtime policy has an unsupported schema")
    instructions = policy["instructions"]
    if not isinstance(instructions, str) or not instructions.strip() or len(instructions) > 1000:
        raise ValueError("Authoring runtime instructions must contain 1-1000 characters")
    ctx.register_system_prompt_section("expskill.authoring", instructions, position="after_memory", max_chars=1000)
    unslop_path = root / "assets/unslop-runtime.json"
    if not unslop_path.is_file():
        unslop_path = root.parent / "content/policies/unslop-runtime.json"
    unslop = json.loads(unslop_path.read_text(encoding="utf-8"))
    if not isinstance(unslop, dict) or set(unslop) != {"schema_version", "scope", "compaction_reminder"}:
        raise ValueError("Unslop runtime policy has unexpected fields")
    if unslop["schema_version"] != "unslop-runtime.v1":
        raise ValueError("Unslop runtime policy has an unsupported schema")
    for field in ("scope", "compaction_reminder"):
        if not isinstance(unslop[field], str) or not unslop[field].strip() or len(unslop[field]) > 1000:
            raise ValueError("Unslop runtime instructions must contain 1-1000 characters")
    skills = root / "skills"
    if not skills.is_dir():
        skills = root.parent / "content/skills"
    lines = (skills / "unslop/SKILL.md").read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError("Unslop skill frontmatter is missing")
    body = "\n".join(lines[lines.index("---", 1) + 1:]).strip()
    remaining = unslop["scope"] + "\n\n" + body
    if not body or len(remaining) + len(instructions) > 8000:
        raise ValueError("Unslop skill exceeds the Hermes prompt section budget")
    part = 1
    while remaining:
        end = len(remaining) if len(remaining) <= 4000 else remaining.rfind("\n", 0, 4001)
        if end <= 0:
            raise ValueError("Unslop skill has a line exceeding the Hermes prompt section limit")
        section_id = "expskill.unslop" if part == 1 else f"expskill.unslop.{part}"
        ctx.register_system_prompt_section(section_id, remaining[:end], position="after_memory", max_chars=4000)
        remaining = remaining[end:].lstrip("\n")
        part += 1
    for directory in sorted(skills.iterdir()):
        skill = directory / "SKILL.md"
        if directory.is_dir() and skill.is_file():
            ctx.register_skill(directory.name, skill)
