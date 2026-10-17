"""Matching a provider without Reep bridges (football-data.org) onto Transfermarkt ids.

Clubs: the provider's full and short names against Reep's men's teams in the league's country that have a
Transfermarkt id (reserve and youth sides excluded); a league's `transfermarkt_clubs` config overrides any pick.
Players: same date of birth, then the closest name. Both refuse to guess: below the threshold there is no match.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from dugout_pipeline.clubs import normalise

# words that say nothing about which club it is ("Real" does: Real Madrid vs Rayo ... de Madrid)
_STOP = {"cf", "club", "de", "del", "la", "el", "ud", "cd", "rcd", "rc", "sd", "ca", "ac", "as", "ss", "us"}
_STOP |= {"sc", "calcio", "futbol", "balompie", "and", "of", "1"}
# youth and reserve sides ("Barcelona U19", "Jong PSV"); "Ajax II" is a reserve side only when "Ajax" is also a
# candidate (Willem II is a club of its own)
_JUNIOR = re.compile(r"\b(u\d{2}|youth|reserves?|juniors?|primavera|castilla|jong|academy|amateure?)\b", re.I)
_SECOND_TEAM = re.compile(r"^(.+?)\s(ii|iii|b|c)$", re.I)
CLUB_THRESHOLD = 0.75
PLAYER_THRESHOLD = 0.5


def _tokens(name: str) -> set[str]:
    return {t for t in normalise(name).split() if t not in _STOP}


def name_score(a: str, b: str) -> float:
    """0..1: half character similarity, half shared distinctive words."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    ta, tb = _tokens(a), _tokens(b)
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return 0.5 * SequenceMatcher(None, na, nb).ratio() + 0.5 * jac


def best_club(
    names: list[str], candidates: list[tuple[str, str, str]], prefer: set[str] | None = None
) -> tuple[str, str, str] | None:
    """The candidate (reep id, label, Transfermarkt id) whose label best matches any of `names`. Ties go to a
    Transfermarkt id in `prefer` (clubs that have played in the league: "Fortuna Sittard", not amateur "Sittard")."""
    labels = {c[1] for c in candidates}
    scored = []
    for cand in candidates:
        second = _SECOND_TEAM.match(cand[1])
        if _JUNIOR.search(cand[1]) or (second and second.group(1) in labels):
            continue
        bonus = 0.05 if prefer and cand[2] in prefer else 0.0
        scored.append((max(name_score(n, cand[1]) for n in names if n) + bonus, cand))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored[0][0] >= CLUB_THRESHOLD else None


def _surname_hit(a: str, b: str) -> bool:
    ta, tb = set(normalise(a).split()), set(normalise(b).split())
    return bool({t for t in ta & tb if len(t) > 2})


def best_player(name: str, candidates: list[tuple[str, str]]) -> str | None:
    """Among (Transfermarkt id, name) sharing the player's date of birth, the closest name (a shared word of three
    or more letters, or a close spelling: "Vinícius Júnior" vs "Vinicius Junior")."""
    scored = sorted(((name_score(name, n), tm, n) for tm, n in candidates), reverse=True)
    for score, tm, n in scored:
        if score >= PLAYER_THRESHOLD or _surname_hit(name, n):
            return tm
    return None
