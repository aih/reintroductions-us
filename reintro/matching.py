"""Find a bill's version in an earlier Congress, or its reintroduction in a later one.

Congress.gov records no "reintroduced as" relationship across Congresses, so the
match is made from the titles: the official title compared exactly after
normalisation, and each short title compared with its year removed
("... Act of 2023" and "... Act of 2025" are one key). A shared sponsor raises the
score; a shared title is required.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

_YEAR_SUFFIX = re.compile(r",?\s+of\s+(19|20)\d\d\b", re.I)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def norm_title(t: str) -> str:
    t = t.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    t = _PUNCT.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def short_title_key(t: str) -> str:
    return norm_title(_YEAR_SUFFIX.sub("", t))


# Official titles too generic to identify a bill on their own.
GENERIC_OFFICIAL = {
    norm_title(t) for t in [
        "For the relief of",
        "To provide for the relief of",
        "Making appropriations",
    ]
}


@dataclass(frozen=True)
class BillKey:
    bill_id: str            # hr597-119 (unitedstates/congress form)
    official_title: str
    short_titles: tuple[str, ...]
    sponsor: str | None     # bioguide id
    introduced_at: str | None


@dataclass
class Match:
    candidate: BillKey
    score: float
    reasons: list[str]


class TitleIndex:
    def __init__(self, bills: Iterable[BillKey]):
        self.by_official: dict[str, list[BillKey]] = defaultdict(list)
        self.by_short: dict[str, list[BillKey]] = defaultdict(list)
        for b in bills:
            o = norm_title(b.official_title)
            if o and o not in GENERIC_OFFICIAL and len(o) > 25:
                self.by_official[o].append(b)
            for s in set(short_title_key(s) for s in b.short_titles):
                if s:
                    self.by_short[s].append(b)

    def candidates(self, bill: BillKey) -> list[Match]:
        found: dict[str, Match] = {}

        def add(c: BillKey, pts: float, why: str):
            m = found.setdefault(c.bill_id, Match(c, 0.0, []))
            m.score += pts
            m.reasons.append(why)

        o = norm_title(bill.official_title)
        for c in self.by_official.get(o, []):
            # An official title shared by many bills ("To amend the Internal Revenue
            # Code of 1986 to ...") is weaker evidence than a unique one.
            add(c, 0.6 if len(self.by_official[o]) <= 3 else 0.3, "official title")
        for s in set(short_title_key(s) for s in bill.short_titles):
            for c in self.by_short.get(s, []):
                add(c, 0.5 if len(self.by_short[s]) <= 3 else 0.25, "short title")
        for m in found.values():
            if bill.sponsor and m.candidate.sponsor == bill.sponsor:
                m.score += 0.3
                m.reasons.append("same sponsor")
            m.score = round(min(m.score, 1.0), 2)
        return sorted(found.values(), key=lambda m: (-m.score, m.candidate.introduced_at or ""), reverse=False)

    def best(self, bill: BillKey, threshold: float = 0.5) -> Match | None:
        cs = self.candidates(bill)
        if not cs or cs[0].score < threshold:
            return None
        # Prefer the latest introduced among equal scores: the most recent version.
        top = [c for c in cs if c.score == cs[0].score]
        return max(top, key=lambda m: m.candidate.introduced_at or "")
