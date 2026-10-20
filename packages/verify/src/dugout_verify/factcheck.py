"""Fact-Check: every non-stat factual claim needs independent support; every stat must be bound to the right thing.

News claims (injury / transfer / result / fact):
  retrieve passages (lexical entity filter -> BGE dense ranking) from official structured data and news,
  score each with DeBERTa-v3 NLI (premise = passage, hypothesis = claim), then count *independent* sources:
  one vote per ownership group (aggregators dropped, near-duplicate stories merged).
    Verified     >= 1 official (tier 1) source entails, or >= 2 independent tier <= 3 groups entail
    Conflicting  a tier <= 2 source contradicts it (whether or not others support it)
    Unverified   anything else
Stat claims: each cited ledger row's entity must be named in the sentence (or the one before, for pronouns),
and the sentence must use a word that fits the metric ("goals" for goals...). A mismatch is Conflicting.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from dugout_verify.claims import Claim, attribution, claims, secondary_type
from dugout_verify.ledger import PLACEHOLDER, EvidenceLedger
from dugout_verify.nli import NLI, Embedder

ENTAIL, CONTRA = 0.7, 0.7
TOP_K = 8
MAX_AGE = {"injury": timedelta(days=14), "transfer": timedelta(days=60), "result": timedelta(days=21)}

# Outlet -> ownership group. Two outlets in one group are one source, not two.
GROUPS = {
    "the guardian": "guardian_media_group",
    "the observer": "guardian_media_group",
    "bbc sport": "bbc",
    "bbc": "bbc",
    "sky sports": "sky",
    "sky news": "sky",
    "espn": "espn",
    "the athletic": "new_york_times",
    "the times": "news_uk",
    "the sun": "news_uk",
    "the independent": "independent_standard",
    "evening standard": "independent_standard",
    "daily mirror": "reach",
    "liverpool echo": "reach",
    "manchester evening news": "reach",
    "daily express": "reach",
    "daily mail": "dmg_media",
    "metro": "dmg_media",
    "the telegraph": "telegraph",
    # names the Rumour Mill and the club sites use for the same mastheads (dugout_models.rumours heat)
    "the mirror": "reach",
    "daily star": "reach",
    "daily record": "reach",
    "talksport": "news_uk",
    "the standard": "independent_standard",
    "mailonline": "dmg_media",
}
AGGREGATORS = {"yahoo sports", "msn", "newsnow", "google news", "onefootball", "flipboard"}


def ownership_group(outlet: str) -> str | None:
    """The ownership group an outlet counts under (one vote per group), None for an aggregator."""
    name = outlet.lower().strip()
    return None if name in AGGREGATORS else GROUPS.get(name, name)


# Clause boundaries for the "metric word near its number" rule.
_CLAUSE = re.compile(r",|;|:| and | but | while | whereas | with ", re.I)

# Words that must appear near a cited metric (lower-case regex). Metrics not listed are not checked.
METRIC_WORDS = {
    "season_goals_scored": r"goal|scor|net",
    "goals": r"goal|scor|net",
    "season_assists": r"assist|set up|created",
    "season_minutes": r"minute|played|playing time",
    "season_expected_goals": r"\bxg\b|expected goals|chances? worth",
    "market_value": r"valu|worth|price|transfermarkt",
    "fair_fee": r"valu|worth|fee|price|cost",
    "fair_fee_low": r"valu|worth|fee|price|cost|likely|between|range",
    "fair_fee_high": r"valu|worth|fee|price|cost|likely|between|range",
    "league_position": r"place|position|table|sit|ranked|top|bottom|\bin\b",
    "league_points": r"point",
    "league_goalsFor": r"goal|scor",
    "league_goalsAgainst": r"goal|concede",
    "home_goals": r"beat|won|lost|drew|draw|score|result|\d|-",
    "away_goals": r"beat|won|lost|drew|draw|score|result|\d|-",
    "season_chance_of_playing_next_round": r"chance|availab|fit|doubt|likely|play",
    "p_home_win": r"chance|probab|likel|win|odds",
    "p_draw": r"chance|probab|likel|draw|odds",
    "p_away_win": r"chance|probab|likel|win|odds",
    "injury_risk_14d": r"risk|injur|chance|probab",
    "injury_risk_tier": r"injury[- ]risk|risk",
    "sale_likelihood": r"sale|sold|sell|leav|transfer window",
    "contract_expires": r"contract",
    "realism": r"realis|feasib|likel|chance|a move for|transfer window",
    "expected_minutes_share": r"minute|playing time",
    "exp_home_goals": r"goal|xg",
    "exp_away_goals": r"goal|xg",
    "elo": r"\belo\b|rating",
    "age": r"\bage|year|old",
    "one_in": r"came true|track record|one time in",
}


@dataclass
class Passage:
    text: str
    outlet: str
    tier: int
    url: str | None = None
    published_at: datetime | None = None
    official: bool = False

    @property
    def group(self) -> str | None:
        return ownership_group(self.outlet)


@dataclass
class ClaimResult:
    index: int
    text: str
    type: str
    status: str  # verified | conflicting | unverified | analysis
    reason: str = ""
    support: list[dict] = field(default_factory=list)
    contradict: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class FactCheckReport:
    claims: list[ClaimResult]

    @property
    def ok(self) -> bool:
        return not any(c.status == "conflicting" for c in self.claims)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.claims:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {"ok": self.ok, "counts": self.counts(), "claims": [c.as_dict() for c in self.claims]}


def chunk(text: str, words: int = 70, overlap: int = 20) -> list[str]:
    toks = text.split()
    if len(toks) <= words:
        return [text] if toks else []
    step = words - overlap
    return [" ".join(toks[i : i + words]) for i in range(0, len(toks) - overlap, step)]


def _shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    t = re.findall(r"\w+", text.lower())
    return {tuple(t[i : i + n]) for i in range(max(1, len(t) - n + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    sa, sb = _shingles(a), _shingles(b)
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= threshold


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _names(claim: str) -> list[str]:
    """Capitalised tokens (not sentence-initial stop words) used as a cheap entity filter."""
    stop = {
        "The",
        "A",
        "An",
        "He",
        "She",
        "They",
        "His",
        "Her",
        "Their",
        "It",
        "In",
        "On",
        "At",
        "After",
        "But",
    }
    return [w for w in re.findall(r"\b[A-Z][\w'’-]+", claim) if w not in stop]


class FactChecker:
    def __init__(
        self,
        passages: list[Passage],
        nli: NLI,
        embedder: Embedder,
        *,
        now: datetime,
        aliases: dict[str, list[str]] | None = None,
    ):
        self.passages, self.nli, self.embedder, self.now = passages, nli, embedder, now
        self.aliases = aliases or {}
        self._emb: np.ndarray | None = None

    def _folded(self, i: int) -> str:
        if not hasattr(self, "_fold_cache"):
            self._fold_cache = [_fold(p.text) for p in self.passages]
        return self._fold_cache[i]

    @property
    def emb(self) -> np.ndarray:
        if self._emb is None:
            self._emb = self.embedder.embed([p.text for p in self.passages])
        return self._emb

    # -- news claims -------------------------------------------------------------------------------------
    @property
    def entity_words(self) -> set[str]:
        """Folded words of every known entity name: what makes a capitalised token a name, not just a word."""
        if not hasattr(self, "_entity_words"):
            self._entity_words = {
                _fold(w) for names in self.aliases.values() for n in names for w in n.split() if len(w) >= 3
            }
        return self._entity_words

    def retrieve(self, claim: str, ctype: str) -> list[int]:
        """Passages that name *every* entity the claim names (so another player's news cannot support or
        contradict it), fresh enough for the claim type, ranked by dense similarity."""
        caps = [_fold(n) for n in _names(claim)]
        must = [n for n in caps if n in self.entity_words] if self.aliases else []
        names = must or caps
        max_age = MAX_AGE.get(ctype)
        cand = []
        for i, p in enumerate(self.passages):
            if max_age is not None and p.published_at is not None and self.now - p.published_at > max_age:
                continue
            text = self._folded(i)
            if names and not (all(n in text for n in must) if must else any(n in text for n in names)):
                continue
            cand.append(i)
        if not cand:
            return []
        q = self.embedder.embed([claim], query=True)[0]
        sims = self.emb[cand] @ q
        return [cand[j] for j in np.argsort(-sims)[:TOP_K]]

    def check_news(self, index: int, text: str, ctype: str) -> ClaimResult:
        idx = self.retrieve(text, ctype)
        if not idx:
            return ClaimResult(index, text, ctype, "unverified", "no relevant source found")
        probs = self.nli.scores([self.passages[i].text for i in idx], text)
        support, contra = [], []
        for i, (pe, _pn, pc) in zip(idx, probs, strict=True):
            p = self.passages[i]
            row = {
                "outlet": p.outlet,
                "group": p.group,
                "tier": p.tier,
                "url": p.url,
                "official": p.official,
                "excerpt": p.text[:240],
            }
            if pe >= ENTAIL:
                support.append({**row, "p": round(float(pe), 3)})
            elif pc >= CONTRA:
                contra.append({**row, "p": round(float(pc), 3)})
        support = _independent(support)
        contra = _independent(contra)
        if any(c["tier"] <= 2 for c in contra):
            return ClaimResult(
                index, text, ctype, "conflicting", "contradicted by a tier 1-2 source", support, contra
            )
        official = any(s["official"] and s["tier"] == 1 for s in support)
        groups = {s["group"] for s in support if s["tier"] <= 3}
        if official or len(groups) >= 2:
            why = "official source" if official else f"{len(groups)} independent sources"
            return ClaimResult(index, text, ctype, "verified", why, support, contra)
        return ClaimResult(
            index, text, ctype, "unverified", f"{len(groups)} independent source(s); need 2", support, contra
        )

    def check_attributed(self, index: int, text: str) -> ClaimResult:
        """ "According to X, ..." is a claim about what X published: X's own text must entail the content."""
        outlet, content = attribution(text) or ("", text)
        mine = [i for i, p in enumerate(self.passages) if p.outlet.lower() == outlet.lower()]
        if not mine:
            return ClaimResult(
                index, text, "attributed", "unverified", f"no text from {outlet} in the corpus"
            )
        q = self.embedder.embed([content], query=True)[0]
        top = [mine[j] for j in np.argsort(-(self.emb[mine] @ q))[:TOP_K]]
        probs = self.nli.scores([self.passages[i].text for i in top], content)
        best = int(np.argmax(probs[:, 0]))
        p = self.passages[top[best]]
        row = {
            "outlet": p.outlet,
            "group": p.group,
            "tier": p.tier,
            "url": p.url,
            "official": p.official,
            "excerpt": p.text[:240],
            "p": round(float(probs[best, 0]), 3),
        }
        if probs[best, 0] >= ENTAIL:
            return ClaimResult(index, text, "attributed", "verified", f"published by {outlet}", [row])
        return ClaimResult(index, text, "attributed", "unverified", f"{outlet}'s text does not support it")

    # -- stat binding --------------------------------------------------------------------------------------
    def check_binding(
        self, claim: Claim, previous: str, ledger: EvidenceLedger, subject: str = ""
    ) -> ClaimResult:
        """Each number must belong to the entity named closest to it (when the sentence names several), and a
        word describing its metric must appear in its clause or the clause before."""
        text = claim.text
        bare = PLACEHOLDER.sub(" ", text).lower()
        context = " ".join([bare, PLACEHOLDER.sub(" ", previous).lower(), subject.lower()])
        problems = []
        holes = list(PLACEHOLDER.finditer(text))
        evs = {m.group(1): ledger.get(m.group(1)) for m in holes}
        # where each cited entity is named in this sentence: (start, end, entity)
        occ = [
            (m.start(), m.end(), ent)
            for ent in {ev.entity_id for ev in evs.values() if ev}
            for a in self.aliases.get(ent, [])
            for m in re.finditer(rf"\b{re.escape(a)}\b", text, re.I)
        ]
        several = len({o[2] for o in occ}) > 1
        masked = PLACEHOLDER.sub(lambda m: "#" * len(m.group(0)), text)  # placeholders contain ':'
        cuts = [0, *(m.end() for m in _CLAUSE.finditer(masked)), len(text) + 1]
        for m in holes:
            ev = evs[m.group(1)]
            if ev is None:
                problems.append(f"{m.group(1)} unknown")
                continue
            names = self.aliases.get(ev.entity_id)
            if names and not any(re.search(rf"\b{re.escape(n.lower())}\b", context) for n in names):
                problems.append(f"{ev.metric} belongs to {names[0]}, who is not named here")
            elif names and several:
                dist = [(max(o[0] - m.end(), m.start() - o[1], 0), o[2]) for o in occ]
                near = min(d for d, _ in dist)
                if ev.entity_id not in {e for d, e in dist if d == near}:
                    problems.append(f"{ev.metric} of {names[0]} is placed next to another name")
            words = METRIC_WORDS.get(ev.metric)
            if words:
                k = max(i for i, c in enumerate(cuts) if c <= m.start())
                window = PLACEHOLDER.sub(" ", text[cuts[max(k - 1, 0)] : cuts[k + 1]]).lower()
                if not re.search(words, window):
                    problems.append(f"no word for {ev.metric} near its number")
        if problems:
            return ClaimResult(claim.index, claim.text, "stat", "conflicting", "; ".join(problems))
        return ClaimResult(claim.index, claim.text, "stat", "verified", "bound to ledger rows")

    # -- document ------------------------------------------------------------------------------------------
    def check(
        self,
        template: str,
        ledger: EvidenceLedger,
        *,
        render: callable | None = None,
        subject: str = "",
    ) -> FactCheckReport:
        """`render` turns a template sentence into plain text (fills placeholders) for NLI on mixed sentences."""
        results = []
        cl = claims(template)
        for k, c in enumerate(cl):
            prev = cl[k - 1].text if k else ""
            if c.type == "analysis":
                results.append(
                    ClaimResult(c.index, c.text, "analysis", "analysis", "judgement, labelled as such")
                )
            elif c.type == "stat":
                r = self.check_binding(c, prev, ledger, subject)
                own = [
                    w for eid in c.stat_ids if (ev := ledger.get(eid)) and (w := METRIC_WORDS.get(ev.metric))
                ]
                extra = secondary_type(c.text, own)
                if r.status == "verified" and extra:
                    plain = render(c.text) if render else PLACEHOLDER.sub("", c.text)
                    news = self.check_news(c.index, plain, extra)
                    if news.status != "verified":
                        r = ClaimResult(
                            c.index,
                            c.text,
                            "stat",
                            news.status,
                            f"{extra} clause: {news.reason}",
                            news.support,
                            news.contradict,
                        )
                results.append(r)
            elif c.type == "attributed":
                results.append(self.check_attributed(c.index, c.text))
            else:
                results.append(self.check_news(c.index, c.text, c.type))
        return FactCheckReport(results)


def _independent(rows: list[dict]) -> list[dict]:
    """One row per ownership group (best score), aggregators dropped, near-duplicate stories merged."""
    best: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: -r["p"]):
        if r["group"] is None:
            continue
        if r["group"] in best:
            continue
        if any(
            near_duplicate(r["excerpt"], b["excerpt"]) and not (r["official"] or b["official"])
            for b in best.values()
        ):
            continue  # same wire copy under two mastheads
        best[r["group"]] = r
    return list(best.values())


def official_passages(rows: Iterable[dict]) -> list[Passage]:
    """Structured official facts as sentences, e.g. from FPL availability flags (tier 1)."""
    return [
        Passage(
            text=r["text"],
            outlet=r["outlet"],
            tier=r.get("tier", 1),
            url=r.get("url"),
            published_at=r.get("published_at"),
            official=True,
        )
        for r in rows
    ]
