"""Export training data from production tables.

  uv run python -m ml.export classify   -> data/classify_{train,val}.jsonl  (SFT, chat format)
  uv run python -m ml.export dpo        -> data/dpo.jsonl                    (prompt/chosen/rejected)
  uv run python -m ml.export ranker     -> data/ranker.csv                   (features + clicked)

Classifier labels are distilled from the prompted Claude classifier, with human relevance labels
overriding newsworthiness where present. Splits are by story, so near-duplicates never straddle
train and validation.
"""

import asyncio
import csv
import json
import random
import sys
from pathlib import Path

from sqlalchemy import select

from geonews.db import session_scope
from geonews.feed.ranking import FEATURES
from geonews.llm.classifier import SYSTEM as CLASSIFY_SYSTEM
from geonews.models import Article, BriefingItem, Event, Label, Preference

OUT = Path(__file__).resolve().parent.parent / "data"


async def export_classify(val_frac: float = 0.1) -> None:
    async with session_scope() as s:
        human = {lb.target_id: lb.value for lb in (await s.execute(
            select(Label).where(Label.kind == "relevance", Label.target_type == "article"))).scalars()}
        arts = list((await s.execute(
            select(Article).where(Article.classifier_version.like("classify-%"),
                                  Article.source.not_like("[eval]%"))
        )).scalars())
    random.seed(0)
    val_stories = {sid for sid in {a.story_id for a in arts} if random.random() < val_frac}
    OUT.mkdir(exist_ok=True)
    with (OUT / "classify_train.jsonl").open("w") as tr, (OUT / "classify_val.jsonl").open("w") as va:
        for a in arts:
            target = {"topic": a.topic, "locality": a.locality,
                      "newsworthiness": round(human.get(a.id, a.newsworthiness), 2)}
            row = {"messages": [
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": f"Title: {a.title}\n\n{(a.body or a.summary)[:3000]}"},
                {"role": "assistant", "content": json.dumps(target)},
            ]}
            (va if a.story_id in val_stories else tr).write(json.dumps(row) + "\n")
    print(f"classify: {len(arts)} rows, {len(val_stories)} val stories")


async def export_dpo() -> None:
    async with session_scope() as s:
        prefs = list((await s.execute(select(Preference))).scalars())
    OUT.mkdir(exist_ok=True)
    with (OUT / "dpo.jsonl").open("w") as f:
        for p in prefs:
            f.write(json.dumps({"prompt": p.prompt, "chosen": p.chosen, "rejected": p.rejected}) + "\n")
    print(f"dpo: {len(prefs)} pairs")


async def export_ranker() -> None:
    """One row per briefing impression; label = clicked within that briefing."""
    async with session_scope() as s:
        items = list((await s.execute(select(BriefingItem))).scalars())
        clicks = {(e.briefing_id, e.story_id) for e in (await s.execute(
            select(Event).where(Event.kind == "click", Event.briefing_id.is_not(None)))).scalars()}
    OUT.mkdir(exist_ok=True)
    with (OUT / "ranker.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", *FEATURES, "label"])
        for it in items:
            w.writerow([it.briefing_id, *[it.features.get(k, 0.0) for k in FEATURES],
                        int((it.briefing_id, it.story_id) in clicks)])
    print(f"ranker: {len(items)} rows, {sum(1 for i in items if (i.briefing_id, i.story_id) in clicks)} clicks")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "classify"
    asyncio.run({"classify": export_classify, "dpo": export_dpo, "ranker": export_ranker}[which]())
