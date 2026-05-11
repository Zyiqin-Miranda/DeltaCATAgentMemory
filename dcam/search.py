"""Pluggable search backends for dcam."""

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from typing import Dict, List, Tuple


class SearchBackend(ABC):
    """Base class for search backends."""

    @abstractmethod
    def search(self, query: str, documents: List[Tuple[int, str]], limit: int = 1000) -> List[Tuple[int, float]]:
        """Search documents. Returns list of (doc_index, score) sorted by relevance."""


class SubstringSearch(SearchBackend):
    """Simple substring matching (original behavior)."""

    def search(self, query: str, documents: List[Tuple[int, str]], limit: int = 1000) -> List[Tuple[int, float]]:
        q = query.lower()
        results = []
        for idx, content in documents:
            if q in content.lower():
                results.append((idx, 1.0))
        return results[:limit]


class BM25Search(SearchBackend):
    """BM25 ranking — matches individual terms, ranks by relevance."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def search(self, query: str, documents: List[Tuple[int, str]], limit: int = 1000) -> List[Tuple[int, float]]:
        if not documents:
            return []

        terms = self._tokenize(query)
        if not terms:
            return []

        # Build corpus stats
        n = len(documents)
        doc_tokens = [(idx, self._tokenize(content)) for idx, content in documents]
        avg_dl = sum(len(toks) for _, toks in doc_tokens) / n if n else 1

        # Document frequency per term
        df: Dict[str, int] = Counter()
        for _, toks in doc_tokens:
            for t in set(toks):
                df[t] += 1

        # Score each document
        scores = []
        for idx, toks in doc_tokens:
            score = 0.0
            tf_counts = Counter(toks)
            dl = len(toks)

            for term in terms:
                if term not in tf_counts:
                    continue
                tf = tf_counts[term]
                idf = math.log((n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1)
                tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / avg_dl))
                score += idf * tf_norm

            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'\w{2,}', text.lower())


# Registry
BACKENDS = {
    "substring": SubstringSearch,
    "bm25": BM25Search,
}

DEFAULT_BACKEND = "bm25"


def get_backend(name: str = DEFAULT_BACKEND) -> SearchBackend:
    cls = BACKENDS.get(name, BACKENDS[DEFAULT_BACKEND])
    return cls()
