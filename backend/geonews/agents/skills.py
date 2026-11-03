"""Skills: instruction packs loaded on demand (progressive disclosure).

Only each skill's one-line description sits in the orchestrator's system prompt; the body is
pulled in via load_skill when relevant, so adding skills doesn't grow every request's context.
"""

from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


@lru_cache
def load_skills() -> dict[str, dict[str, str]]:
    skills = {}
    for path in sorted(SKILLS_DIR.glob("*.md")):
        text = path.read_text()
        _, front, body = text.split("---", 2)
        meta = dict(line.split(":", 1) for line in front.strip().splitlines())
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        skills[meta["name"]] = {"description": meta["description"], "body": body.strip()}
    return skills


def skills_index() -> str:
    return "\n".join(f"- {name}: {s['description']}" for name, s in load_skills().items())


def get_skill(name: str) -> str | None:
    s = load_skills().get(name)
    return s["body"] if s else None
