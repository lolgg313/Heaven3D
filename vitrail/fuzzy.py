# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Typo-tolerant search used by menus ("trnasform" finds "Transform")."""
from __future__ import annotations

import re

_SPLIT = re.compile(r"[\s_\-/.:,]+")


def osa_distance(a: str, b: str, limit: int = 99) -> int:
    """Optimal-string-alignment distance (Levenshtein + adjacent swaps)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > limit:
        return limit + 1
    prev2 = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and ai == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
        prev2, prev = prev, cur
    return prev[lb]


def _word_score(q: str, w: str):
    if w.startswith(q):
        return 0.0
    if q in w:
        return 1.0
    allowed = 0 if len(q) < 3 else (1 if len(q) < 6 else 2)
    if not allowed:
        return None
    best = allowed + 1
    for n in range(max(1, len(q) - allowed), min(len(w), len(q) + allowed) + 1):
        best = min(best, osa_distance(q, w[:n], allowed))
        if best == 0:
            break
    return 2.0 + best if best <= allowed else None


def fuzzy_score(query: str, text: str):
    """Lower is better; None means no match. Every query word must match a word."""
    q_words = [w for w in _SPLIT.split(query.lower()) if w]
    if not q_words:
        return 0.0
    t_words = [w for w in _SPLIT.split(text.lower()) if w] or [""]
    joined = text.lower()
    total = 0.0
    for qw in q_words:
        best = None
        for tw in t_words:
            s = _word_score(qw, tw)
            if s is not None and (best is None or s < best):
                best = s
        if best is None:
            best = 1.5 if qw in joined else None
        if best is None:
            return None
        total += best
    return total + len(text) * 0.001


def fuzzy_filter(query: str, items, key=str):
    """Return the items that match ``query`` sorted best-first."""
    if not query.strip():
        return list(items)
    scored = []
    for i, it in enumerate(items):
        s = fuzzy_score(query, key(it))
        if s is not None:
            scored.append((s, i, it))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [it for _, _, it in scored]
