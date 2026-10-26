"""Replaceable scorer; hashes are compared for equality, never textual distance."""
from typing import Protocol

SCORING_VERSION = "grouped-24-v1"
GROUPS = ((4, 2, 4, 4), (5, 2, 5), (3, 3, 3, 1), (4, 3, 4), (2, 1, 2, 2), (6, 6))


class Scorer(Protocol):
    def score(self, left, right) -> dict: ...


class WeightedScorer:
    def score(self, left, right):
        compatible = all(getattr(left, k) == getattr(right, k) for k in ("token_version", "key_id", "method"))
        matches = [i + 1 for i, (a, b) in enumerate(zip(left.tokens, right.tokens)) if compatible and a and a == b]
        offset, scores = 0, []
        for group in GROUPS:
            scores.append(max([weight for i, weight in enumerate(group, offset + 1) if i in matches], default=0))
            offset += len(group)
        total = sum(scores)
        return {"score": total, "matched_tokens": matches, "group_scores": scores,
                "compatible": compatible, "classification": "match" if total >= 12 and sum(v > 0 for v in scores) >= 2 else "review" if total >= 6 else "below_threshold"}
