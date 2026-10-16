"""Leagues as config: league-scoped sources, football-data.org clubs and squads for leagues without FPL."""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pandas as pd
import pytest
from conftest import make_ctx
from dugout_pipeline.adapters import football_data_org as fdo
from dugout_pipeline.adapters.base import SourceAdapter, SourceDisabled
from dugout_pipeline.clubs import league_config, leagues
from dugout_pipeline.ids import ReepMatch, StaticIndex
from dugout_pipeline.matching import best_club, best_player
from dugout_pipeline.registry import ADAPTERS
from dugout_pipeline.runner import run_leagues, takes

SPAIN = [
    ("rt-esp", "Espanyol", "714"),
    ("rt-bar", "Barcelona", "131"),
    ("rt-bar-u19", "Barcelona U19", "9001"),
    ("rt-val", "Valencia", "1049"),
    ("rt-rac", "Racing de Santander", "630"),
]


def test_config_leagues():
    keys = leagues()
    assert keys[0] == "premier_league"  # the default league runs the whole job
    assert {"la_liga", "serie_a", "bundesliga", "ligue_1", "eredivisie"} <= set(keys)
    for k in keys[1:]:
        cfg = league_config(k)
        assert cfg["provider_ids"]["football_data_org"] and cfg["provider_ids"]["transfermarkt"]
        assert "fpl" not in cfg["provider_ids"] and cfg["sources"] == ["football_data_org"]
        assert cfg["slug_suffix"] and cfg["country"]


def test_best_club_uses_short_name_and_skips_youth_sides():
    assert best_club(["RCD Espanyol de Barcelona", "Espanyol"], SPAIN)[1] == "Espanyol"
    assert best_club(["FC Barcelona", "Barça"], SPAIN)[1] == "Barcelona"
    assert best_club(["Real Madrid CF", "Real Madrid"], SPAIN) is None  # no candidate: no guess
    youth = [("a", "Ajax II", "1"), ("b", "Willem II", "403"), ("c", "Ajax", "610")]
    assert best_club(["AFC Ajax", "Ajax"], youth)[2] == "610"
    assert best_club(["Willem II Tilburg", "Willem II"], youth)[2] == "403"


def test_best_club_ties_go_to_clubs_of_the_league():
    cands = [("s", "Sittard", "34455"), ("f", "Fortuna Sittard", "385")]
    assert best_club(["Fortuna Sittard", "Sittard"], cands, prefer={"385"})[2] == "385"


def test_best_player_needs_a_name_match():
    same_dob = [("1", "Marc Casadó"), ("2", "Pedri")]
    assert best_player("Marc Casado", same_dob) == "1"
    assert best_player("Pedro González López", [("2", "Pedri")]) is None


def test_takes_respects_sources_and_provider_ids():
    cfg = {"provider_ids": {"football_data_org": "PD"}, "sources": ["football_data_org"]}
    assert takes("la_liga", cfg, "football_data_org", default=False)
    assert not takes("la_liga", cfg, "guardian", default=False)  # not listed
    assert not takes("la_liga", cfg, "fpl", default=True)  # needs provider_ids.fpl


class _PerLeague(SourceAdapter):
    name = "football_data_org"
    requires = "football_data_org"
    required = True
    seen: ClassVar[list[str]] = []

    def ingest(self):
        _PerLeague.seen.append(self.ctx.league)
        return {"rows": 1}


class _Once(SourceAdapter):
    name = "wikidata"
    scope = "global"
    seen: ClassVar[list[str]] = []

    def ingest(self):
        _Once.seen.append(self.ctx.league)
        return {}


def test_run_leagues_runs_league_sources_per_league_and_global_once(ctx, monkeypatch):
    monkeypatch.setitem(ADAPTERS, "football_data_org", _PerLeague)
    monkeypatch.setitem(ADAPTERS, "wikidata", _Once)
    _PerLeague.seen, _Once.seen = [], []
    job = run_leagues(ctx, ["football_data_org", "wikidata"], ["premier_league", "la_liga", "serie_a"])
    assert _PerLeague.seen == ["premier_league", "la_liga", "serie_a"] and _Once.seen == ["premier_league"]
    labels = [r.source for r in job.results]
    assert labels == [
        "football_data_org",
        "football_data_org@la_liga",
        "football_data_org@serie_a",
        "wikidata",
    ]
    assert [r.required for r in job.results] == [
        True,
        False,
        False,
        False,
    ]  # only the default league fails a job


def test_league_adapter_refuses_a_league_without_its_provider(settings, reep):
    ctx = make_ctx(settings, reep)
    ctx.league = "la_liga"
    with pytest.raises(SourceDisabled):
        ADAPTERS["fpl"](ctx)


def _teams_doc() -> dict:
    return {
        "teams": [
            {
                "id": 95,
                "name": "Valencia CF",
                "shortName": "Valencia",
                "tla": "VAL",
                "coach": {},
                "squad": [
                    {
                        "id": 1,
                        "name": "Giorgi Mamardashvili",
                        "position": "Goalkeeper",
                        "dateOfBirth": "2000-09-29",
                    },
                    {"id": 2, "name": "Unknown Kid", "position": "Midfield", "dateOfBirth": "2009-01-01"},
                ],
            },
            {
                "id": 80,
                "name": "RCD Espanyol de Barcelona",
                "shortName": "Espanyol",
                "tla": "ESP",
                "squad": [],
            },
            {"id": 5335, "name": "Real Racing Club de Santander", "shortName": "Santander", "tla": "SAN"},
        ]
    }


@pytest.fixture
def la_liga(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(fdo, "TM_DIR", tmp_path)  # transfermarkt-datasets excerpt
    pd.DataFrame(
        {
            "player_id": [111],
            "name": ["Giorgi Mamardashvili"],
            "date_of_birth": [pd.Timestamp("2000-09-29")],
        }
    ).to_parquet(tmp_path / "players.parquet")
    entries = {("transfermarkt", "verein", tm): ReepMatch(rid, label, {}) for rid, label, tm in SPAIN}
    entries[("transfermarkt", "spieler", "111")] = ReepMatch("rp-mama", "Giorgi Mamardashvili", {})
    ctx = make_ctx(settings, StaticIndex(entries, {"Spain": SPAIN}))
    ctx.league = "la_liga"
    # another league already uses the slug "valencia"
    ctx.con.execute(
        "INSERT INTO clubs (club_id, name, league, season, updated_at) VALUES ('valencia', 'Valencia', 'x', 's', now())"
    )
    return ctx


def test_new_clubs_get_reep_names_unique_slugs_and_overrides(la_liga):
    a = fdo.FootballDataOrgAdapter(la_liga)
    clubs, ids = a.parse_new_clubs(_teams_doc())
    by_name = {c["name"]: c for c in clubs}
    assert by_name["Valencia"]["club_id"] == "valencia-esp"  # collides with another league: suffixed
    assert by_name["Espanyol"]["club_id"] == "espanyol" and by_name["Espanyol"]["reep_id"] == "rt-esp"
    # config pins Racing (name matching scores it just under the threshold)
    assert by_name["Racing de Santander"]["club_id"] == "racing-de-santander"
    assert ("club", "valencia-esp", "transfermarkt", "1049") in {
        (r["entity_type"], r["dugout_id"], r["provider"], r["external_id"]) for r in ids
    }
    a.close()


def test_slugs_stay_put_and_squads_map_to_transfermarkt(la_liga):
    from dugout_pipeline.db import upsert

    a = fdo.FootballDataOrgAdapter(la_liga)
    clubs, ids = a.parse_new_clubs(_teams_doc())
    upsert(la_liga.con, "clubs", clubs)
    upsert(la_liga.con, "entity_ids", ids)
    again, _ = a.parse_new_clubs(_teams_doc())  # second night: same slugs (by football-data.org id)
    assert {c["club_id"] for c in again} == {c["club_id"] for c in clubs}
    la_liga.load_clubs()
    players, _ = a.parse_squads(_teams_doc())
    by = {p["name"]: p for p in players}
    assert by["Giorgi Mamardashvili"]["player_id"] == "rp-mama"
    assert (
        by["Giorgi Mamardashvili"]["club_id"] == "valencia-esp"
        and by["Giorgi Mamardashvili"]["position"] == "GK"
    )
    assert by["Unknown Kid"]["player_id"] == "football_data_org:2"  # no confident match: own id, no guess
    assert by["Unknown Kid"]["birth_date"] == date(2009, 1, 1)
    upsert(la_liga.con, "players", players)
    a.release_departed({"rp-mama"})
    left = la_liga.con.execute(
        "SELECT club_id FROM players WHERE player_id = 'football_data_org:2'"
    ).fetchone()
    assert left == (None,)
    a.close()


def test_fixtures_are_written_for_leagues_without_fpl(la_liga):
    from dugout_pipeline.db import upsert

    a = fdo.FootballDataOrgAdapter(la_liga)
    clubs, ids = a.parse_new_clubs(_teams_doc())
    upsert(la_liga.con, "clubs", clubs)
    upsert(la_liga.con, "entity_ids", ids)
    la_liga.load_clubs()
    doc = {
        "matches": [
            {
                "id": 9,
                "matchday": 10,
                "utcDate": "2026-10-25T15:00:00Z",
                "status": "TIMED",
                "homeTeam": {"id": 95, "name": "Valencia CF"},
                "awayTeam": {"id": 80, "name": "RCD Espanyol de Barcelona"},
                "score": {"fullTime": {"home": None, "away": None}},
            }
        ]
    }
    [m] = a.parse_fixtures(doc, "2026-27", la_liga.now)
    assert m["match_id"] == "la_liga:2026-27:valencia-esp:espanyol" and m["status"] == "scheduled"
    assert m["gameweek"] == 10 and m["kickoff_utc"].hour == 15
    a.close()


def test_wikidata_year_only_appointment_is_not_a_date(ctx):
    from dugout_pipeline.adapters.wikidata import WikidataAdapter

    b = {
        "tm": {"value": "418"},
        "coach": {"value": "http://www.wikidata.org/entity/Q2"},
        "coachLabel": {"value": "A Coach"},
        "start": {"value": "2026-01-01T00:00:00Z"},
        "sprec": {"value": "9"},  # year precision
    }
    a = WikidataAdapter(ctx)
    _, managers = a.parse({"results": {"bindings": [b]}}, {"418": "real-madrid"}, ctx.now)
    a.close()
    assert managers[0]["start_date"] is None
