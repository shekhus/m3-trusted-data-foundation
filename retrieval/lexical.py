"""Lexical rankers: BM25 (the hybrid's lexical half, A15) and TF-IDF (the floor every result is reported
against).

Both are small, dependency-free numpy implementations fitted on the candidate set a query is allowed to see,
so a restricted document never contributes to term statistics either. TF-IDF mirrors the calibration probe
(synth/probes/rag_probe.py): unigrams and bigrams, English stop words, sublinear tf, smoothed idf, l2 norm,
cosine similarity.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

import numpy as np

TOKEN = re.compile(r"[a-z0-9]+(?:[.%][0-9]+%?)?")
STOP_WORDS = frozenset(
    """
a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have
having he her here hers him his how i if in into is it its itself just me more most my no nor not of
off on once only or other our ours out over own same she should so some such than that the their
theirs them then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your yours
""".split()
)
# negations and quantities carry meaning in this corpus ("not weighted", "no exclusions", "3 days")
STOP_WORDS = STOP_WORDS - {"no", "not", "nor", "only", "all", "any", "more", "most", "few", "before", "after"}


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP_WORDS]


class BM25:
    def __init__(self, documents: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(d)) for d in documents]
        self.lengths = np.array([sum(c.values()) for c in self.docs], dtype=float)
        self.avg = float(self.lengths.mean()) if len(self.docs) else 0.0
        df: Counter[str] = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> np.ndarray:
        out = np.zeros(len(self.docs))
        terms = set(tokens(query))
        for i, doc in enumerate(self.docs):
            norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg or 1.0))
            out[i] = sum(self.idf[t] * doc[t] * (self.k1 + 1) / (doc[t] + norm) for t in terms if t in doc)
        return out


class TfIdf:
    def __init__(self, documents: Sequence[str]) -> None:
        grams = [self._grams(d) for d in documents]
        vocab = sorted({g for doc in grams for g in doc})
        self.index = {g: i for i, g in enumerate(vocab)}
        n = len(documents)
        df = np.zeros(len(vocab))
        for doc in grams:
            for g in set(doc):
                df[self.index[g]] += 1
        self.idf = np.log((1 + n) / (1 + df)) + 1
        self.matrix = np.vstack([self._vector(doc) for doc in grams]) if grams else np.zeros((0, len(vocab)))

    @staticmethod
    def _grams(text: str) -> Counter[str]:
        words = tokens(text)
        return Counter(words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)])

    def _vector(self, grams: Counter[str]) -> np.ndarray:
        v = np.zeros(len(self.index))
        for g, count in grams.items():
            if g in self.index:
                v[self.index[g]] = (1 + math.log(count)) * self.idf[self.index[g]]
        norm = np.linalg.norm(v)
        return v / norm if norm else v

    def scores(self, query: str) -> np.ndarray:
        if not len(self.matrix):
            return np.zeros(0)
        return self.matrix @ self._vector(self._grams(query))
