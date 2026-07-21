# Skills for FUND Models
from pathlib import Path
from typing import List

import yaml


def scan_skills(skills_dir: Path) -> List[dict]:
    """Scan a directory for skills. Reads only YAML frontmatter (name + description).

    Returns a list of dicts with 'name', 'description', and 'path' (to SKILL.md).
    """
    skills = []
    if not skills_dir or not Path(skills_dir).exists():
        return skills
    for skill_md in sorted(Path(skills_dir).glob("*/SKILL.md")):
        text = skill_md.read_text()
        # Parse YAML frontmatter (between --- delimiters)
        if text.startswith("---"):
            _, frontmatter, body = text.split("---", 2)
            meta = yaml.safe_load(frontmatter)
            skills.append({
                "name": meta.get("name", skill_md.parent.name),
                "description": meta.get("description", ""),
                "path": str(skill_md),
            })
    return skills


def make_load_skill_tool(skill_registry: List[dict]):
    """Return a LangChain tool bound to a specific skill_registry list.

    This factory pattern allows each agent instance to have its own scoped
    load_skill tool rather than sharing a global one.
    """
    from langchain.tools import tool

    @tool
    def load_skill_from_fs(skill_name: str) -> str:
        """Load the full content of a skill from the filesystem.

        Use this when you need detailed guidance on how to approach a specific
        type of task. Skills contain reasoning strategies, heuristics, and
        guidelines — not actions.

        Args:
            skill_name: The name of the skill to load.
        """
        for s in skill_registry:
            if s["name"] == skill_name:
                text = Path(s["path"]).read_text()
                if text.startswith("---"):
                    _, _, body = text.split("---", 2)
                    content = body.strip()
                else:
                    content = text.strip()
                return f"Loaded skill: {skill_name}\n\n{content}"

        available = ", ".join(s["name"] for s in skill_registry)
        return f"Skill '{skill_name}' not found. Available skills: {available}"

    return load_skill_from_fs


if __name__ == "__main__":
    import sys
    skills_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".") / "skills"
    registry = scan_skills(skills_dir)
    for s in registry:
        print(f"{s['name']}\n{s['description']}")
        print(f"path: {s['path']}\n")
