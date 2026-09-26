"""Second-stage reranking of hybrid-retrieval candidates.

Default: a transparent feature reranker (query-term coverage, heading match,
first-stage fused score). Optional: a cross-encoder when sentence-transformers
is installed (AEGIS_RERANKER=cross-encoder).
"""
from __future__ import annotations

import os

from rag.embeddings import tokenize


class FeatureReranker:
    name = "feature"

    def score(self, query: str, candidates: list[tuple[object, float]]) -> list[float]:
        q = set(tokenize(query))
        if not candidates:
            return []
        max_first = max(s for _, s in candidates) or 1.0
        out = []
        for chunk, first in candidates:
            body = set(tokenize(chunk.text))
            head = set(tokenize(f"{chunk.title} {chunk.section}"))
            coverage = len(q & (body | head)) / max(len(q), 1)
            heading = len(q & head) / max(len(q), 1)
            out.append(0.45 * (first / max_first) + 0.35 * coverage + 0.20 * heading)
        return out


class CrossEncoderReranker:
    def __init__(self, model: str | None = None):
        from sentence_transformers import CrossEncoder  # optional dependency
        self.model_name = model or os.environ.get("AEGIS_CE_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
        self.model = CrossEncoder(self.model_name)
        self.name = f"ce:{self.model_name}"

    def score(self, query: str, candidates: list[tuple[object, float]]) -> list[float]:
        return [float(s) for s in self.model.predict([(query, c.search_text) for c, _ in candidates])]


def get_reranker(kind: str | None = None):
    kind = kind or os.environ.get("AEGIS_RERANKER", "feature")
    return CrossEncoderReranker() if kind == "cross-encoder" else FeatureReranker()
