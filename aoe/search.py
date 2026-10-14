"""Search ranking for the unit / civ pickers: prefix matches first, then word-prefix, then substring."""
from __future__ import annotations

import re


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def rank_matches(query: str, labels: list[str]) -> list[str]:
    """Return labels matching `query`, ordered:
    1. label starts with the query
    2. any word in the label starts with the query
    3. query appears anywhere in the label
    Ties keep alphabetical order. An empty query returns all labels.
    """
    q = _norm(query)
    if not q:
        return list(labels)
    tiers: tuple[list[str], list[str], list[str]] = ([], [], [])
    for label in labels:
        n = _norm(label)
        if n.startswith(q):
            tiers[0].append(label)
        elif any(w.startswith(q) for w in re.split(r"[\s\-()/']+", n) if w):
            tiers[1].append(label)
        elif q in n:
            tiers[2].append(label)
    return [x for tier in tiers for x in sorted(tier, key=str.lower)]
