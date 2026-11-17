"""Replaceable scorer; hashes are compared for equality, never textual distance."""
from typing import Protocol

SCORING_VERSION = "grouped-24-v4-1"
# Each group contributes its strongest agreeing rule, with document caps 8/5/5/5/1.
GROUPS = (
    ((1, 8), (2, 8), (3, 6), (4, 6)),
    ((5, 5), (6, 3), (7, 3), (8, 3), (9, 3)),
    ((10, 5), (12, 4)),
    ((11, 5), (13, 4)),
    ((14, 1),),
)
# Frozen for previously accepted v3 queue entries and baseline evaluation only.
LEGACY_V3_GROUPS = (
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
        groups = GROUPS if left.token_version == "v4" else LEGACY_V3_GROUPS
        scoring_version = SCORING_VERSION if left.token_version == "v4" else "grouped-24-v2"
        compatible &= left.token_version in {"v3", "v4"}
        matches = [i + 1 for i, (a, b) in enumerate(zip(left.tokens, right.tokens)) if compatible and a and a == b]
        scores = []
        for group in groups:
            scores.append(max([weight for position, weight in group if position in matches], default=0))
        total = sum(scores)
        return {"scoring_version": scoring_version, "score": total, "matched_tokens": matches, "group_scores": scores,
                "compatible": compatible, "classification": "match" if total >= 12 and sum(v > 0 for v in scores) >= 2 else "review" if total >= 6 else "below_threshold"}
