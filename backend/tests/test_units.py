from types import SimpleNamespace

import pytest

from geonews.agents.skills import load_skills, skills_index
from geonews.agents.verifier import cited_ids, degrade
from geonews.feed.ranking import FEATURES, heuristic_score
from geonews.ingest import dedupe
from geonews.ingest.geo import bbox, extract_entities, haversine_km
from geonews.ingest.sources import canonical_url, url_hash
from geonews.llm.router import LLMRouter, cost_usd
from geonews_evals.calibrate import cohens_kappa
from geonews_evals.runner import regressions, summarize


def test_canonical_url_drops_tracking():
    assert url_hash("https://X.com/a/?utm_source=t") == url_hash("https://x.com/a")
    assert canonical_url("HTTPS://News.com/story/#frag") == "https://news.com/story"


def test_minhash_clusters_near_duplicates():
    base = ("The Oak River Bridge on Route 9 will close to all traffic from 9 p.m. to 5 a.m. "
            "nightly starting Monday for deck repairs, the county transportation department said.")
    near = base.replace("said.", "said on Friday.")
    other = "The library will open Sundays from noon to 5 p.m. starting in January, the board said."
    s1, s2, s3 = (dedupe.signature(t) for t in (base, near, other))
    assert dedupe.similarity(s1, s2) > 0.6
    assert dedupe.similarity(s1, s3) < 0.2
    assert dedupe.best_match(s2, [(1, s3), (2, s1)], 0.6)[0] == 2
    assert dedupe.best_match(s3, [(2, s1)], 0.6)[0] is None


def test_geo_helpers():
    assert haversine_km(37.7749, -122.4194, 34.0522, -118.2437) == pytest.approx(559, abs=5)
    lat0, lat1, lon0, lon1 = bbox(40.0, -100.0, 111.0)
    assert lat0 == pytest.approx(39.0) and lat1 == pytest.approx(41.0) and lon0 < -100 < lon1
    places, _ = extract_entities("A fire broke out near Riverton on Monday. Crews from Salem helped.")
    assert "Riverton" in places


def test_cited_ids_and_degrade():
    answer = "The council approved it [A1, A2]. It takes effect soon [A3]. Unrelated claim [A9]."
    assert cited_ids(answer) == ["A1", "A2", "A3", "A9"]
    verdict = {"unsupported": [{"text": "Unrelated claim [A9].", "citations": ["A9"]}]}
    out = degrade(answer, verdict)
    assert "Unrelated claim" not in out and "removed 1 point" in out


def test_heuristic_prefers_local_relevant_unseen():
    base = dict.fromkeys(FEATURES, 0.0)
    good = {**base, "newsworthiness": 0.8, "in_radius": 1, "locality": 1, "interest_sim": 0.6, "recency_h": 2}
    seen = {**good, "already_seen": 1}
    stale = {**good, "recency_h": 40}
    assert heuristic_score(good) > heuristic_score(seen)
    assert heuristic_score(good) > heuristic_score(stale)


def test_router_params_and_cost():
    r = LLMRouter(client=SimpleNamespace(), sink=None)
    opus = r._params("orchestrate", {"max_tokens": 10})
    assert opus["model"] == "claude-opus-5-5"
    assert opus["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in opus["betas"]
    assert opus["output_config"]["effort"] == "medium"
    haiku = r._params("classify", {"output_config": {"format": {"type": "json_schema"}}})
    assert haiku["model"] == "claude-haiku-5-5" and "fallbacks" not in haiku
    assert haiku["output_config"] == {"effort": "low", "format": {"type": "json_schema"}}
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0)


def test_skills_load():
    skills = load_skills()
    assert {"local-government", "breaking-news", "commute-impact"} <= set(skills)
    assert "breaking-news:" in skills_index()


def test_eval_gate():
    s = summarize([{"metric": "faithfulness", "score": 4}, {"metric": "faithfulness", "score": 5},
                   {"metric": "cost_usd", "score": 0.1}])
    assert s["faithfulness"] == 4.5
    assert regressions(s, {"faithfulness": 4.0, "cost_usd": 0.1}, 0.05) == []
    assert regressions({"faithfulness": 3.0}, {"faithfulness": 4.0}, 0.05)
    assert regressions({"cost_usd": 0.2}, {"cost_usd": 0.1}, 0.05)


def test_cohens_kappa():
    assert cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohens_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.0)
