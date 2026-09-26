"""Embedding back-ends.

* HashingEmbedder (default): TF-IDF weighted feature hashing of word unigrams,
  bigrams and character trigrams into a fixed-size L2-normalised vector.
  Needs no model download, so it works offline and in CI. It is a lexical
  stand-in for a neural encoder, and the retrieval benchmark reports it as such.
* SentenceTransformerEmbedder: any sentence-transformers model
  (AEGIS_EMBEDDER=sentence-transformers, AEGIS_ST_MODEL=all-MiniLM-L6-v2).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter

import numpy as np

STOPWORDS = set("""a an the and or of to in for on at by with from is are be as it this that my i me
we our you your can do does what how when which who will would should may per any all than
into up if not no its their they them was were been has have had about out""".split())


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(t) for t in toks if t not in STOPWORDS]


def stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _features(text: str) -> Counter:
    toks = tokenize(text)
    feats: Counter = Counter()
    for t in toks:
        feats["w:" + t] += 1.0
        padded = f"#{t}#"
        for i in range(len(padded) - 2):
            feats["c:" + padded[i:i + 3]] += 0.25
    for a, b in zip(toks, toks[1:]):
        feats[f"b:{a}_{b}"] += 1.0
    return feats


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "little")
    return h % dim, (1.0 if (h >> 63) & 1 else -1.0)


class HashingEmbedder:
    name = "hashing-tfidf-1024"

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.idf: dict[str, float] = {}

    def fit(self, texts: list[str]) -> "HashingEmbedder":
        df: Counter = Counter()
        for t in texts:
            df.update(set(_features(t)))
        n = len(texts)
        self.idf = {f: math.log((1 + n) / (1 + c)) + 1.0 for f, c in df.items()}
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        default_idf = math.log(1 + max(len(self.idf), 1)) + 1.0
        for i, t in enumerate(texts):
            for f, tf in _features(t).items():
                idx, sign = _bucket(f, self.dim)
                out[i, idx] += sign * (1 + math.log(tf)) * self.idf.get(f, default_idf)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class SentenceTransformerEmbedder:
    def __init__(self, model: str | None = None):
        from sentence_transformers import SentenceTransformer  # optional dependency
        self.model_name = model or os.environ.get("AEGIS_ST_MODEL", "all-MiniLM-L6-v2")
        self.model = SentenceTransformer(self.model_name)
        self.name = f"st:{self.model_name}"

    def fit(self, texts: list[str]) -> "SentenceTransformerEmbedder":
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True), dtype=np.float32)


def get_embedder(kind: str = "hashing"):
    if kind in ("sentence-transformers", "st"):
        return SentenceTransformerEmbedder()
    return HashingEmbedder()
