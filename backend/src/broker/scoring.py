"""Replaceable scorer; hashes are compared for equality, never textual distance."""
from typing import Protocol

SCORING_VERSION = "grouped-24-v2"
# Explicit (token position, weight) pairs let private-healthcare alternatives
# share the Medicare group caps without changing the first 20 rules.
GROUPS = (
    ((1, 4), (2, 2), (3, 4), (4, 4)),
    ((5, 5), (6, 2), (7, 5), (21, 5), (22, 2), (23, 5)),
    ((8, 3), (9, 3), (10, 3), (11, 1)),
    ((12, 4), (13, 3), (14, 4)),
    ((15, 2), (16, 1), (17, 2), (18, 2)),
    ((19, 6), (20, 6), (24, 6), (25, 6)),
)


class Scorer(Protocol):
    def score(self, left, right) -> dict: ...


class WeightedScorer:
    def score(self, left, right):
        compatible = all(getattr(left, k) == getattr(right, k) for k in ("token_version", "key_id", "method"))
        matches = [i + 1 for i, (a, b) in enumerate(zip(left.tokens, right.tokens)) if compatible and a and a == b]
        scores = []
        for group in GROUPS:
            scores.append(max([weight for position, weight in group if position in matches], default=0))
        total = sum(scores)
        return {"score": total, "matched_tokens": matches, "group_scores": scores,
                "compatible": compatible, "classification": "match" if total >= 12 and sum(v > 0 for v in scores) >= 2 else "review" if total >= 6 else "below_threshold"}
