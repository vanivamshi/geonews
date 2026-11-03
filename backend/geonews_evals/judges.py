"""LLM-as-judge with explicit rubrics.

Judges score one dimension each (separate calls beat one mega-rubric for consistency) and must
give a reason before the score. Agreement with humans is measured in calibrate.py; don't trust a
judge metric you haven't calibrated.
"""

from geonews.llm.router import get_router

JUDGE_VERSION = "judge-v1"


def _schema(score_desc: str, extra: dict | None = None) -> dict:
    props = {"reason": {"type": "string"}, "score": {"type": "integer", "description": score_desc}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


FAITHFULNESS = """Rate how faithful the ANSWER is to the SOURCES (1-5).
5 = every factual claim is directly supported by a cited source.
4 = all claims supported; minor imprecision in wording or numbers rounding.
3 = one claim unsupported or overstated.
2 = several unsupported claims, or one that materially misleads.
1 = largely unsupported or contradicts the sources.
Outdated figures presented as current count as unsupported."""

COVERAGE = """For each EXPECTED FACT, decide if the ANSWER conveys it (paraphrase is fine).
Return covered as a list of booleans in the same order as the facts. score = number covered."""

USEFULNESS = """Rate how useful the ANSWER is to this reader for this QUESTION (1-5).
5 = directly answers, leads with what matters to them, concise, actionable where relevant.
3 = answers but buries the lede, is padded, or misses obvious reader context.
1 = does not answer the question."""


async def judge_faithfulness(answer: str, sources: dict[str, str]) -> dict:
    src = "\n\n".join(f"<source id=\"{k}\">\n{v}\n</source>" for k, v in sources.items())
    return await get_router().complete_json(
        "judge", _schema("1-5"), max_tokens=2000, system=FAITHFULNESS,
        messages=[{"role": "user", "content": f"{src}\n\n<answer>\n{answer}\n</answer>"}],
    )


async def judge_coverage(answer: str, facts: list[str]) -> dict:
    if not facts:
        return {"reason": "no expected facts", "score": 0, "covered": []}
    listed = "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts))
    out = await get_router().complete_json(
        "judge", _schema("count covered", {"covered": {"type": "array", "items": {"type": "boolean"}}}),
        max_tokens=2000, system=COVERAGE,
        messages=[{"role": "user", "content": f"<facts>\n{listed}\n</facts>\n\n<answer>\n{answer}\n</answer>"}],
    )
    out["covered"] = (out["covered"] + [False] * len(facts))[: len(facts)]
    return out


async def judge_usefulness(question: str, answer: str, profile: dict) -> dict:
    return await get_router().complete_json(
        "judge", _schema("1-5"), max_tokens=2000, system=USEFULNESS,
        messages=[{"role": "user", "content":
                   f"<reader>{profile}</reader>\n<question>{question}</question>\n<answer>\n{answer}\n</answer>"}],
    )


async def judge_abstain(question: str, answer: str) -> dict:
    """Did the answer correctly say the corpus has no coverage, rather than make something up?"""
    return await get_router().complete_json(
        "judge", _schema("1 if it clearly says it found no relevant coverage and invents nothing, else 0"),
        max_tokens=1000, system="You check whether an assistant correctly declined to answer.",
        messages=[{"role": "user", "content": f"<question>{question}</question>\n<answer>\n{answer}\n</answer>"}],
    )
