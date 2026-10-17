"""The six specialists and how the Sporting Director routes a question to them."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from dataclasses import dataclass

from dugout_agents import tools
from dugout_agents.facts import Fact, Toolbox


@dataclass(frozen=True)
class Specialist:
    name: str
    title: str
    role: str
    gather: Callable[[Toolbox, str, list[str]], list[Fact]]
    keywords: str  # routing regex
    dossier_question: str
    max_facts: int = 8  # sentences when writing from templates (a lineup needs all eleven)
    prose: bool = True  # False: always the templates (a lineup is a list; an LLM summary drops players)


def _club(tb: Toolbox, club: str, focus: list[str]) -> list[Fact]:
    out = tools.club_profile(tb, club)
    for pid in focus:
        out += tools.player_season(tb, pid)
    return out + tools.manager_style(tb, club)


def _news(tb: Toolbox, club: str, focus: list[str]) -> list[Fact]:
    out: list[Fact] = []
    for pid in focus:
        out += tools.availability(tb, club, pid)
    if not focus:
        out += tools.availability(tb, club)
    return out + tools.headlines(tb, club)


def _transfer(tb: Toolbox, club: str, focus: list[str]) -> list[Fact]:
    if focus:
        return [f for pid in focus for f in tools.squad_market(tb, club, pid)]
    return tools.squad_market(tb, club) + tools.rumours(tb, club, "out")


SPECIALISTS: dict[str, Specialist] = {
    s.name: s
    for s in (
        Specialist(
            "club",
            "Club & Tactics",
            "club and tactics analyst",
            _club,
            r"tactic|style|press|manager|coach|table|position|form|points|league|play(?:s|ing)? (?:like|under)|"
            r"goals?|scor|assist|minutes|xg|season|how (?:are|is)|doing",
            "How are {club} doing this season, and how do they play under their head coach?",
        ),
        Specialist(
            "match",
            "Next Match",
            "match analyst",
            lambda tb, c, f: tools.next_match(tb, c),
            r"predict|forecast|win|beat|odds|chance|next (?:\w+ )?(?:match|game|fixture)|fixture|probab|result|"
            r"expect|kick-?off|when (?:is|are|do|does)\b.*\b(?:match|game|fixture|play)",
            "What should {club} expect from their next league match?",
        ),
        Specialist(
            "lineup",
            "Predicted XI",
            "lineup selector",
            lambda tb, c, f: tools.predicted_xi(tb, c),
            r"line-?up|\bxi\b|start(?:ing|s)?\b|formation|selection|bench|pick|rotate|rotation",
            "What is {club}'s predicted lineup for the next match?",
        ),
        Specialist(
            "news",
            "Team News",
            "news and medical liaison",
            _news,
            r"injur|(?:is|are|be|he|they) (?:\w+ )?fit\b(?! in| into| with| at| our| the system| your)|fit to play|fitness|news|"
            r"suspen|availab|doubt|return|miss|medical|knock|ban",
            "Who is unavailable for {club}, and what is the latest news?",
        ),
        Specialist(
            "transfer",
            "Transfers & Values",
            "transfer and valuation analyst",
            _transfer,
            r"valu|worth|sell|sold|sale|leave|exit|contract|fee|price|cost|offer|bid|market",
            "Which {club} players could be sold, and what are the key players worth?",
        ),
        Specialist(
            "scout",
            "Recruitment",
            "chief scout",
            lambda tb, c, f: tools.shortlist(tb, c),
            r"sign|buy|target|replace|recruit|scout|shortlist|need|strengthen|candidate|who should",
            "Where should {club} strengthen, and who fits?",
        ),
        Specialist(
            "potential",
            "Player Development",
            "player development analyst",
            lambda tb, c, f: [x for pid in f for x in tools.player_potential(tb, pid)],
            r"potential|future|become|ceiling|how good|develop|prospect|projection|wonderkid|peak|grow",
            "",
        ),
        Specialist(
            "fit",
            "Player Fit",
            "recruitment analyst",
            lambda tb, c, f: [x for pid in f for x in tools.player_fit(tb, pid, c)],
            r"\bfit (?:in|into|with|at)\b|\bfit (?:the|our)\b|would \w+(?: \w+)? fit(?! for)|\bsuit|right for|"
            r"work (?:at|in|for)|playing time|minutes would",
            "",
        ),
    )
}
for _name, _n in (("lineup", 14), ("news", 12), ("transfer", 15), ("scout", 31), ("club", 10)):
    SPECIALISTS[_name] = dataclasses.replace(SPECIALISTS[_name], max_facts=_n)
SPECIALISTS["lineup"] = dataclasses.replace(SPECIALISTS["lineup"], prose=False)

# specialists that only answer about a named player; the dossier runs the six club-level ones
PLAYER_ONLY = ("potential", "fit")
CLUB_LEVEL = [n for n in SPECIALISTS if n not in PLAYER_ONLY]


def route(question: str) -> list[str]:
    """Keyword routing (deterministic, auditable). Falls back to Club & Tactics plus News & Injury."""
    q = question.lower()
    hits = [name for name, s in SPECIALISTS.items() if re.search(s.keywords, q)]
    # "worth" questions are about value, not form; "who should we sign" is scouting, not news
    if "scout" in hits and "transfer" in hits and re.search(r"sign|buy|target|replace|recruit|strengthen", q):
        hits.remove("transfer")
    return hits or ["club", "news"]


def find_players(tb: Toolbox, question: str, club: str | None = None) -> list[str]:
    """Player ids named in the question (longest alias first; current-club players preferred)."""
    found: dict[str, int] = {}
    for pid, aliases in tb.aliases.items():
        if (
            pid.startswith(("wikidata:", "football_data_org:", "club_name:", "league:"))
            or pid.count(":") == 3
        ):
            continue  # managers, display-only names and matches
        if pid in tb.names and pid in _club_ids(tb):
            continue
        for a in aliases:
            if len(a) >= 4 and re.search(rf"\b{re.escape(a)}\b", question, re.I):
                found[pid] = max(found.get(pid, 0), len(a))
                break
    if not found:
        return []
    clubs = dict(tb.con.execute("SELECT player_id, club_id FROM players").fetchall())
    best = max(found.values())
    picks = [p for p, n in found.items() if n == best]
    picks.sort(key=lambda p: (clubs.get(p) != club, clubs.get(p) is None))
    return picks[:1]


def _club_ids(tb: Toolbox) -> set[str]:
    if not hasattr(tb, "_club_ids"):
        tb._club_ids = {r[0] for r in tb.con.execute("SELECT club_id FROM clubs").fetchall()}
    return tb._club_ids


def find_club(tb: Toolbox, question: str) -> str | None:
    for cid in _club_ids(tb):
        if any(re.search(rf"\b{re.escape(a)}\b", question, re.I) for a in tb.aliases.get(cid, [])):
            return cid
    return None
