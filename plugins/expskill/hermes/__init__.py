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
    skills = root / "skills"
    if not skills.is_dir():
        skills = root.parent / "content/skills"
    for directory in sorted(skills.iterdir()):
        skill = directory / "SKILL.md"
        if directory.is_dir() and skill.is_file():
            ctx.register_skill(directory.name, skill)
