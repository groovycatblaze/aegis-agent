"""Hybrid retriever: BM25 (sparse) + vector search (dense), fused with
Reciprocal Rank Fusion, then reranked.

`mode` lets the benchmark compare bm25 | dense | hybrid | hybrid_rerank.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from rag.embeddings import get_embedder, tokenize
from rag.ingestion import Chunk, load_corpus
from rag.reranker import get_reranker

RRF_K = 60


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avgdl = sum(map(len, docs)) / max(len(docs), 1)
        df: Counter = Counter()
        for d in docs:
            df.update(set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tfs = [Counter(d) for d in docs]

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(len(self.docs), dtype=np.float32)
        for i, (tf, d) in enumerate(zip(self.tfs, self.docs)):
            norm = self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
            out[i] = sum(self.idf.get(t, 0) * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in query if t in tf)
        return out


class VectorStore:
    """Cosine-similarity vector index (numpy; uses FAISS if installed)."""

    def __init__(self, vectors: np.ndarray):
        self.vectors = vectors
        try:
            import faiss  # type: ignore
            self.index = faiss.IndexFlatIP(vectors.shape[1])
            self.index.add(vectors)
        except ImportError:
            self.index = None

    def search(self, q: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.index is not None:
            scores, ids = self.index.search(q.reshape(1, -1), k)
            return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]
        sims = self.vectors @ q
        top = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in top]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk.id, "doc_id": self.chunk.doc_id, "title": self.chunk.title,
                "section": self.chunk.section, "version": self.chunk.version, "text": self.chunk.text,
                "score": round(self.score, 4)}


class HybridRetriever:
    def __init__(self, chunks: list[Chunk] | None = None, embedder: str = "hashing"):
        self.chunks = chunks if chunks is not None else load_corpus()
        texts = [c.search_text for c in self.chunks]
        self.bm25 = BM25([tokenize(t) for t in texts])
        self.embedder = get_embedder(embedder).fit(texts)
        self.store = VectorStore(self.embedder.embed(texts))
        self.reranker = get_reranker()

    def search(self, query: str, k: int = 4, mode: str = "hybrid_rerank", candidates: int = 20) -> list[Hit]:
        n = len(self.chunks)
        candidates = min(candidates, n)
        bm = self.bm25.scores(tokenize(query))
        bm_order = [int(i) for i in np.argsort(-bm)[:candidates] if bm[i] > 0]
        dense = self.store.search(self.embedder.embed([query])[0], candidates)
        dense_order = [i for i, _ in dense]

        if mode == "bm25":
            return [Hit(self.chunks[i], float(bm[i]), bm25_rank=r + 1) for r, i in enumerate(bm_order[:k])]
        if mode == "dense":
            return [Hit(self.chunks[i], s, dense_rank=r + 1) for r, (i, s) in enumerate(dense[:k])]

        fused: dict[int, float] = {}
        for r, i in enumerate(bm_order):
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + r + 1)
        for r, i in enumerate(dense_order):
            fused[i] = fused.get(i, 0) + 1 / (RRF_K + r + 1)
        ranked = sorted(fused.items(), key=lambda x: -x[1])[:candidates]
        bm_rank = {i: r + 1 for r, i in enumerate(bm_order)}
        de_rank = {i: r + 1 for r, i in enumerate(dense_order)}

        if mode == "hybrid_rerank" and ranked:
            scores = self.reranker.score(query, [(self.chunks[i], s) for i, s in ranked])
            ranked = sorted(zip([i for i, _ in ranked], scores), key=lambda x: -x[1])
        return [Hit(self.chunks[i], s, bm_rank.get(i), de_rank.get(i)) for i, s in ranked[:k]]

    @staticmethod
    def unique_docs(hits: list[Hit]) -> list[str]:
        seen: list[str] = []
        for h in hits:
            if h.chunk.doc_id not in seen:
                seen.append(h.chunk.doc_id)
        return seen


@lru_cache(maxsize=2)
def get_retriever(embedder: str = "hashing") -> HybridRetriever:
    return HybridRetriever(embedder=embedder)
