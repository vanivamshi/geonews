import asyncio
import hashlib
from functools import lru_cache

import numpy as np

from geonews.config import get_settings


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. No model download; used in tests and CI."""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for tok in text.lower().split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
            n = np.linalg.norm(v)
            out.append((v / n if n else v).tolist())
        return out


class FastEmbedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(texts)]


@lru_cache
def get_embedder() -> HashEmbedder | FastEmbedder:
    s = get_settings()
    if s.embedding_backend == "hash":
        return HashEmbedder(s.embedding_dim)
    return FastEmbedder(s.embedding_model)


async def embed(texts: list[str]) -> list[list[float]]:
    # ONNX inference is CPU-bound; keep it off the event loop.
    return await asyncio.to_thread(get_embedder().embed, texts)


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]
