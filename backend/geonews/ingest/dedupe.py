"""Near-duplicate detection and story clustering with MinHash.

Articles whose estimated Jaccard similarity (5-word shingles of title + lead) exceeds the
threshold join an existing story. At v0 volumes (thousands of articles in the window) a linear
scan over recent signatures is fast enough; swap in MinHashLSH or pgvector ANN past ~100k.
"""

import re

import numpy as np
from datasketch import MinHash

NUM_PERM = 128
_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str, k: int = 5) -> set[str]:
    words = _WORD.findall(text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def signature(text: str) -> list[int]:
    m = MinHash(num_perm=NUM_PERM, seed=7)
    for s in shingles(text):
        m.update(s.encode())
    return m.hashvalues.tolist()


def similarity(a: list[int], b: list[int]) -> float:
    return float(np.mean(np.asarray(a) == np.asarray(b)))


def dedupe_text(title: str, summary: str, body: str) -> str:
    lead = (body or summary)[:600]
    return f"{title} {lead}"


def best_match(
    sig: list[int], candidates: list[tuple[int, list[int]]], threshold: float
) -> tuple[int | None, float]:
    """candidates: (story_id, signature). Returns the best story above threshold, if any."""
    best_id, best = None, 0.0
    for story_id, other in candidates:
        sim = similarity(sig, other)
        if sim > best:
            best_id, best = story_id, sim
    return (best_id, best) if best >= threshold else (None, best)
