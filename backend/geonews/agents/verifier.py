"""Verifier: checks each factual sentence of an answer against the articles it cites.

Two layers: a deterministic citation check (ids must exist and have been seen during the run),
then a model check of support. Kept outside the orchestrator's loop on purpose: grounding is
enforced by architecture, not by asking the writer to be careful.
"""

import re

from sqlalchemy import select

from geonews.agents.prompts import VERIFIER_SYSTEM
from geonews.db import session_scope
from geonews.llm.router import get_router
from geonews.models import Article

CITATION = re.compile(r"\[(A\d+(?:\s*,\s*A\d+)*)\]")
SOURCE_CHARS = 2500

SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "supported": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "citations", "supported", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def cited_ids(answer: str) -> list[str]:
    ids: list[str] = []
    for group in CITATION.findall(answer):
        for i in re.split(r"\s*,\s*", group):
            if i not in ids:
                ids.append(i)
    return ids


async def load_sources(ids: list[str]) -> dict[str, str]:
    nums = [int(i[1:]) for i in ids]
    if not nums:
        return {}
    async with session_scope() as s:
        rows = await s.execute(select(Article).where(Article.id.in_(nums)))
        return {
            f"A{a.id}": f"{a.title} ({a.source}, {a.published_at:%Y-%m-%d})\n{a.body[:SOURCE_CHARS]}"
            for a in rows.scalars()
        }


async def verify(answer: str, seen_ids: set[str]) -> dict:
    ids = cited_ids(answer)
    invalid = [i for i in ids if i not in seen_ids]
    sources = await load_sources([i for i in ids if i in seen_ids])
    invalid += [i for i in ids if i in seen_ids and i not in sources]
    source_block = "\n\n".join(f"<source id=\"{k}\">\n{v}\n</source>" for k, v in sources.items())
    result = await get_router().complete_json(
        "verify",
        SCHEMA,
        max_tokens=4000,
        system=VERIFIER_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"{source_block}\n\n<answer>\n{answer}\n</answer>\n\n"
                       f"Ids not available (treat as unsupported): {invalid or 'none'}",
        }],
    )
    sentences = result["sentences"]
    for s in sentences:  # the deterministic check overrides the model
        if any(c in invalid for c in s["citations"]):
            s["supported"], s["reason"] = False, "cites an id that was not retrieved"
    unsupported = [s for s in sentences if not s["supported"]]
    return {
        "all_supported": not unsupported,
        "unsupported": unsupported,
        "n_sentences": len(sentences),
        "invalid_citations": invalid,
        "citation_validity": 1 - len(invalid) / len(ids) if ids else 1.0,
    }


def degrade(answer: str, verdict: dict) -> str:
    """Out of retries: drop unsupported sentences and say so, rather than ship them."""
    out = answer
    for s in verdict["unsupported"]:
        out = out.replace(s["text"], "")
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    n = len(verdict["unsupported"])
    note = f"\n\n_Note: I removed {n} point{'s' if n != 1 else ''} I couldn't verify against the sources._"
    return (out or "I couldn't find well-supported coverage to answer this.") + note
