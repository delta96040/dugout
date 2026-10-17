"""football-data.co.uk: free historical results + bookmaker odds CSVs (the benchmark the predictor must beat)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from dugout_pipeline.adapters.base import DAY, TIER_DATA, SourceAdapter
from dugout_pipeline.clubs import ClubResolver
from dugout_pipeline.db import upsert
from dugout_pipeline.http import FetchError

# Closing odds columns in preference order (Pinnacle closing, market average closing, Bet365 closing, pre-match).
ODDS_COLUMNS = [
    ("PSCH", "PSCD", "PSCA", "pinnacle_close"),
    ("AvgCH", "AvgCD", "AvgCA", "market_avg_close"),
    ("B365CH", "B365CD", "B365CA", "bet365_close"),
    ("PSH", "PSD", "PSA", "pinnacle"),
    ("AvgH", "AvgD", "AvgA", "market_avg"),
    ("B365H", "B365D", "B365A", "bet365"),
]


def season_code(season: str) -> str:
    """'2024-25' -> '2425'."""
    a, b = season.split("-")
    return a[2:] + b[-2:]


def parse_date(s: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(s)


class FootballDataCoUkAdapter(SourceAdapter):
    name = "football_data_couk"
    requires = "football_data_couk"
    tier = TIER_DATA
    ttl = 12 * 60 * 60
    rate, per = 1, 3.0
    base_url = "https://www.football-data.co.uk/mmz4281/"

    def ingest(self, seasons: list[str] | None = None) -> dict[str, int]:
        cfg = self.ctx.league_cfg
        seasons = seasons or [*cfg["history_seasons"], cfg["season"]]
        div = cfg["provider_ids"]["football_data_couk"]
        resolver = ClubResolver(
            aliases=self.ctx.clubs.aliases
        )  # historical clubs: permissive name resolution
        if not cfg["provider_ids"].get("fpl"):
            # leagues whose clubs come from football-data.org: "Sociedad", "Celta", "M'gladbach" resolve by prefix or
            # suffix against the league's own clubs first, so this season's results join their history
            self.ctx.load_clubs()
            resolver = _LeagueFirst(self.ctx.clubs, resolver)
        n = 0
        for s in seasons:
            past = s != cfg["season"]
            try:
                res = self.client.get(
                    f"{self.base_url}{season_code(s)}/{div}.csv", ttl=365 * DAY if past else None
                )
            except FetchError:
                if past:
                    raise
                continue  # the current-season file only appears after the first matchday
            n += upsert(self.ctx.con, "historical_results", self.parse(res.content, s, resolver))
        return {"historical_results": n}

    def parse(self, content: bytes, season: str, resolver: ClubResolver) -> list[dict]:
        text = content.decode("utf-8-sig", errors="replace")
        rows = []
        for r in csv.DictReader(io.StringIO(text)):
            if not r.get("HomeTeam") or not r.get("Date"):
                continue
            odds = next(
                (
                    (r[h], r[d], r[a], label)
                    for h, d, a, label in ODDS_COLUMNS
                    if r.get(h) and r.get(d) and r.get(a)
                ),
                (None, None, None, None),
            )
            rows.append(
                {
                    "league": self.ctx.league,
                    "season": season,
                    "date": parse_date(r["Date"]),
                    "home": resolver.resolve(r["HomeTeam"]) or r["HomeTeam"],
                    "away": resolver.resolve(r["AwayTeam"]) or r["AwayTeam"],
                    "home_goals": _int(r.get("FTHG")),
                    "away_goals": _int(r.get("FTAG")),
                    "odds_home": _flt(odds[0]),
                    "odds_draw": _flt(odds[1]),
                    "odds_away": _flt(odds[2]),
                    "odds_source": odds[3],
                    "source": self.name,
                }
            )
        return rows


class _LeagueFirst(ClubResolver):
    """The league's clubs first; any other name (a club since relegated) as the permissive resolver has it."""

    def __init__(self, league: ClubResolver, fallback: ClubResolver):
        super().__init__(set(league.slugs), dict(league.aliases), dict(league.by_provider))
        self.fallback = fallback

    def resolve(
        self, name: str, provider: str | None = None, external_id: str | int | None = None
    ) -> str | None:
        return super().resolve(name, provider, external_id) or self.fallback.resolve(name)


def _int(x):
    return int(x) if x not in (None, "") else None


def _flt(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None
