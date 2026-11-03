"""Article classifier: topic, locality, newsworthiness.

This is the high-volume, narrow task that the LoRA model is trained to take over. Both backends
return the same schema so evals (geonews_evals, suite "classify") can compare them directly.
"""

import httpx

from geonews.config import get_settings
from geonews.llm.router import get_router

TOPICS = [
    "politics", "local_government", "business", "tech", "science", "health", "crime",
    "transport", "weather", "environment", "education", "sports", "culture", "other",
]
LOCALITIES = ["local", "regional", "national", "global"]
PROMPT_VERSION = "classify-v1"

SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "enum": TOPICS},
        "locality": {"type": "string", "enum": LOCALITIES},
        "newsworthiness": {"type": "number", "description": "0.0-1.0"},
    },
    "required": ["topic", "locality", "newsworthiness"],
    "additionalProperties": False,
}

SYSTEM = """You label news articles for a personalized local-news product.

topic: the single best fit from the allowed list.
locality: how geographically scoped the story's impact is.
  local = one city/neighborhood; regional = state/province or metro area;
  national = one country; global = multiple countries.
newsworthiness: 0.0-1.0, how much a resident who follows this area would want to know.
  Routine listings, ads, and evergreen content score below 0.3. Events that change daily life
  (closures, elections, major incidents, policy changes) score above 0.7."""


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + "..."


async def classify_claude(title: str, body: str) -> dict:
    result = await get_router().complete_json(
        "classify",
        SCHEMA,
        max_tokens=256,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Title: {title}\n\n{_clip(body, 3000)}"}],
    )
    result["newsworthiness"] = max(0.0, min(1.0, float(result["newsworthiness"])))
    return {**result, "classifier_version": f"{PROMPT_VERSION}:{get_settings().subagent_model}"}


async def classify_lora(title: str, body: str) -> dict:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(s.lora_endpoint, json={"title": title, "body": _clip(body, 3000)})
        r.raise_for_status()
        data = r.json()
    return {
        "topic": data["topic"] if data["topic"] in TOPICS else "other",
        "locality": data["locality"] if data["locality"] in LOCALITIES else "regional",
        "newsworthiness": float(data["newsworthiness"]),
        "classifier_version": data.get("version", "lora"),
    }


async def classify(title: str, body: str) -> dict:
    if get_settings().classifier_backend == "lora":
        return await classify_lora(title, body)
    return await classify_claude(title, body)
