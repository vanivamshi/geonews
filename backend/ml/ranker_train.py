"""Train the LightGBM LambdaRank model from logged briefing impressions and clicks.

  uv sync --extra ml
  uv run python -m ml.export ranker
  uv run python -m ml.ranker_train          # writes models/ranker.txt; the feed uses it on restart

Reports NDCG@5 of the learned model vs. the hand-weighted heuristic on held-out briefings; only
ship it if it wins.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    import lightgbm as lgb
    import numpy as np
    import pandas as pd
    from sklearn.metrics import ndcg_score

    from geonews.feed.ranking import FEATURES, heuristic_score

    df = pd.read_csv(ROOT / "data" / "ranker.csv")
    groups = df["group"].unique()
    rng = np.random.default_rng(0)
    val_groups = set(rng.choice(groups, size=max(1, len(groups) // 5), replace=False))
    tr, va = df[~df.group.isin(val_groups)], df[df.group.isin(val_groups)]

    def sizes(d):
        return d.groupby("group", sort=False).size().to_list()

    model = lgb.LGBMRanker(objective="lambdarank", n_estimators=300, learning_rate=0.05,
                           num_leaves=15, min_child_samples=20)
    model.fit(tr[FEATURES], tr["label"], group=sizes(tr),
              eval_set=[(va[FEATURES], va["label"])], eval_group=[sizes(va)], eval_at=[5])

    def mean_ndcg(scores) -> float:
        vals = []
        for _, g in va.assign(s=scores).groupby("group"):
            if g["label"].sum() > 0 and len(g) > 1:
                vals.append(ndcg_score([g["label"]], [g["s"]], k=5))
        return float(np.mean(vals)) if vals else float("nan")

    learned = mean_ndcg(model.predict(va[FEATURES]))
    heuristic = mean_ndcg([heuristic_score(r) for r in va[FEATURES].to_dict("records")])
    print(f"NDCG@5  learned={learned:.4f}  heuristic={heuristic:.4f}")
    if learned > heuristic:
        model.booster_.save_model(str(ROOT / "models" / "ranker.txt"))
        print("saved models/ranker.txt")
    else:
        print("learned ranker did not beat heuristic; not saving")


if __name__ == "__main__":
    main()
