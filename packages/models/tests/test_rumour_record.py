"""Rumour track record: name resolution, club timelines, labels and the estimates, on tiny synthetic tables."""

import numpy as np
import pandas as pd
from dugout_models import rumours as R


def _tables() -> dict[str, pd.DataFrame]:
    clubs = pd.DataFrame(
        {
            "club_id": [31, 631, 148, 418, 10],
            "club_code": ["fc-liverpool", "fc-chelsea", "tottenham-hotspur", "real-madrid", "ik-start"],
            "name": ["Liverpool FC", "Chelsea FC", "Tottenham Hotspur", "Real Madrid", "IK Start"],
            "domestic_competition_id": ["GB1", "GB1", "GB1", "ES1", "NO1"],
        }
    )
    names = {31: "Liverpool", 631: "Chelsea", 148: "Tottenham", 418: "Real Madrid", 10: "Start"}
    # enough moves for every club to enter the gazetteer (MIN_MOVES), between anonymous players
    pairs = [(a, b) for a in names for b in names if a != b] * 2
    filler = [
        {
            "player_id": 900 + i,
            "transfer_date": "2010-07-01",
            "from_club_id": a,
            "to_club_id": b,
            "transfer_fee": 0,
        }
        for i, (a, b) in enumerate(pairs)
    ]
    real = [
        # player 1 (Ann Able): IK Start -> Chelsea in 2015, Chelsea -> Liverpool for a fee on 2019-07-01
        {
            "player_id": 1,
            "transfer_date": "2015-07-01",
            "from_club_id": 10,
            "to_club_id": 631,
            "transfer_fee": 1e6,
        },
        {
            "player_id": 1,
            "transfer_date": "2019-07-01",
            "from_club_id": 631,
            "to_club_id": 31,
            "transfer_fee": 5e7,
        },
    ]
    tr = pd.DataFrame(filler + real)
    tr["from_club_name"] = tr.from_club_id.map(names)
    tr["to_club_name"] = tr.to_club_id.map(names)
    players = pd.DataFrame(
        {
            "player_id": [1, 2, 3],
            "name": ["Ann Able", "Ben Baker", "Ann Able"],  # a namesake, retired long ago
            "date_of_birth": pd.to_datetime(["1995-01-01", "1990-01-01", "1960-01-01"]),
            "highest_market_value_in_eur": [6e7, 1e7, 1e5],
            "last_season": [2025, 2021, 1990],
        }
    )
    # player 2 has no transfer history (the dataset's gap for players inactive since 2023): appearances and
    # dated valuation names show him at Tottenham, then at Real Madrid from February 2018
    # (the June 2019 game is for his country, which is not a club)
    apps = pd.DataFrame(
        {
            "player_id": [2] * 7,
            "date": pd.to_datetime(
                [
                    "2016-09-01",
                    "2017-03-01",
                    "2017-10-01",
                    "2018-02-10",
                    "2018-09-01",
                    "2019-03-01",
                    "2019-06-20",
                ]
            ),
            "player_club_id": [148, 148, 148, 418, 418, 418, 3672],
            "competition_id": ["GB1", "GB1", "GB1", "ES1", "ES1", "ES1", "FIWC"],
            "minutes_played": [90] * 7,
        }
    )
    comps = pd.DataFrame(
        {
            "competition_id": ["GB1", "ES1", "FIWC"],
            "type": ["domestic_league"] * 2 + ["national_team_competition"],
        }
    )
    vals = pd.DataFrame(
        {
            "player_id": [2, 2, 2, 2, 1, 1],
            "date": pd.to_datetime(
                ["2016-09-05", "2017-03-05", "2018-02-15", "2018-09-05", "2018-12-01", "2019-12-01"]
            ),
            "market_value_in_eur": [1e7, 1.2e7, 2e7, 2.2e7, 4e7, 6e7],
            "current_club_name": [
                "Tottenham Hotspur",
                "Tottenham Hotspur",
                "Real Madrid",
                "Real Madrid",
                "Chelsea FC",
                "Liverpool FC",
            ],
            "current_club_id": [418, 418, 418, 418, 31, 31],  # today's club, not the club on the day
        }
    )
    return {
        "clubs": clubs,
        "transfers": tr,
        "players": players,
        "appearances": apps,
        "competitions": comps,
        "player_valuations": vals,
    }


def _setup():
    t = _tables()
    gaz = R.Gazetteer.build(t["transfers"], t["clubs"])
    return t, gaz, R.People.build(t, gaz)


def test_gazetteer_matches_capitalised_club_names_and_aliases():
    _, gaz, _ = _setup()
    found = gaz.find(
        "Spurs and Real Madrid want him; Liverpool too. A good start for Start? Not the real deal."
    )
    assert [c for c, _ in found] == ["148", "418", "31"]  # "Start" alone is in GENERIC: a word, not a club
    assert gaz.find("IK Start") == [("10", "ik start")]
    assert gaz.find("real madrid") == []  # lower case is a phrase, not a club


def test_people_timeline_fills_the_transfer_gap():
    _, _, people = _setup()
    assert people.club_on(2, pd.Timestamp("2017-06-01"))[0] == "148"
    assert people.club_on(2, pd.Timestamp("2018-06-01"))[0] == "418"
    assert people.club_on(2, pd.Timestamp("2019-07-01"))[0] == "418"  # a World Cup game is not a move
    assert people.club_on(1, pd.Timestamp("2019-08-01"))[0] == "31"
    arr = people.arrivals()
    assert ((arr.tm_player_id == "2") & (arr.tm_club_id == "418")).any()
    # namesakes: only the one of playing age on the day
    assert people.candidates("Ann Able", pd.Timestamp("2019-01-01")) == [1]


def test_realism_snapshot_uses_the_club_on_the_day():
    from dugout_models import realism

    t, _, people = _setup()
    # Ben Baker was at Tottenham on 1 June 2017; the valuations' club id says Real Madrid (his club today)
    dated = realism.snapshots(t, [2017], people=people, covered_only=False).set_index("player_id")
    undated = realism.snapshots(t, [2017], dated=False, covered_only=False).set_index("player_id")
    assert dated.loc[2, "club_id"] == 148 and undated.loc[2, "club_id"] == 418
    assert dated.loc[2, "minutes_share"] > 0  # his Tottenham games count for him
    assert dated.loc[2, "tenure"] > 0.5  # in the timeline since September 2016
    # covered_only keeps only players the dataset has a transfer history for (Ben Baker has none)
    assert 2 not in set(realism.snapshots(t, [2017], people=people, covered_only=True).player_id)


def test_links_skip_the_players_own_club_and_bold_clubs_and_label_by_horizon():
    _, gaz, people = _setup()
    rumours = pd.DataFrame(
        {
            "rumour_id": ["r1-p0", "r2-p0"],
            "published_at": pd.to_datetime(["2019-03-01", "2017-05-01"]).tz_localize("UTC"),
            "players": [["Ann Able", "Liverpool"], ["Ben Baker"]],
            "outlet": ["The Sun", "The Guardian"],
            "body_text": [
                "Liverpool want Ann Able, and Chelsea may let her go.",
                "Real Madrid are watching Ben Baker at Tottenham.",
            ],
        }
    )
    links, stats = R.links_from(rumours, gaz, people)
    assert stats["bold_clubs"] == 1
    assert sorted(zip(links.player_name, links.tm_club_id, strict=True)) == [
        ("Ann Able", "31"),
        ("Ben Baker", "418"),
    ]
    moves = R.all_moves(people, None, gaz)
    labelled = R.label(links, moves, pd.Timestamp("2026-07-06"))
    six = dict(zip(labelled.player_name, labelled.joined_6m, strict=True))
    twelve = dict(zip(labelled.player_name, labelled.joined_12m, strict=True))
    assert six == {"Ann Able": True, "Ben Baker": False}  # Baker was first seen at Madrid after nine months
    assert twelve["Ben Baker"] is True
    # a window that has not closed yet is not labelled (Able's March 2019 rumour, data to May 2019)
    early = R.label(links, moves, pd.Timestamp("2019-05-01")).set_index("player_name").joined_6m
    assert early["Ann Able"] is None and early["Ben Baker"] is False


def test_beta_prior_shrinks_small_outlets_and_caps_the_strength():
    a, b = R.beta_prior(np.array([10.0, 30.0, 2.0]), np.array([100.0, 100.0, 20.0]))
    assert 0.05 < a / (a + b) < 0.4 and a + b < R.MAX_PRIOR_STRENGTH
    # identical outlets: the likelihood keeps rising with the strength; it stops at the cap
    a, b = R.beta_prior(np.array([10.0, 10.0, 10.0]), np.array([100.0, 100.0, 100.0]))
    assert abs((a + b) - R.MAX_PRIOR_STRENGTH) < 1 and abs(a / (a + b) - 0.1) < 0.01


def test_model_learns_from_repeats_and_beats_the_base_rate():
    rng = np.random.default_rng(0)
    n = 4000
    x = pd.DataFrame({f: rng.normal(size=n) for f in R.FEATURES})
    x["outlet"] = rng.choice(["The Sun", "The Guardian"], n)
    x["season"] = rng.integers(2010, 2020, n)
    x["joined_6m"] = rng.random(n) < 1 / (1 + np.exp(-(-2 + 1.5 * x.log_repeats)))
    train, test = x[x.season < 2018], x[x.season >= 2018]
    m = R.RumourModel.fit(train, "joined_6m")
    yt = test.joined_6m.to_numpy(float)
    base = np.full(len(test), train.joined_6m.mean())
    assert R.scores(m.predict(test), yt)["brier"] < R.scores(base, yt)["brier"]
    assert m.features == R.FEATURES and "outlet_logit" not in m.features


def test_site_summary_adds_up(tmp_path):
    import json

    rng = np.random.default_rng(1)
    n = 3000
    x = pd.DataFrame({f: rng.normal(size=n) for f in R.FEATURES})
    x["outlet"] = rng.choice(["The Sun", "The Guardian", "Tiny Gazette"], n, p=[0.3, 0.695, 0.005])
    x["season"] = rng.integers(2015, 2024, n)
    x["joined_6m"] = rng.random(n) < 0.15
    m = R.RumourModel.fit(x, "joined_6m")
    doc = R.site_summary(x, m, "joined_6m", pd.Timestamp("2026-10-18", tz="UTC").to_pydatetime())
    assert doc["overall"]["links"] == n and doc["overall"]["came_true"] == int(x.joined_6m.sum())
    assert sum(s["links"] for s in doc["seasons"]) == n
    assert all(o["links"] >= R.MIN_OUTLET_LINKS for o in doc["outlets"])
    assert "Tiny Gazette" not in {o["outlet"] for o in doc["outlets"]}  # too few rumours to show a record
    R.write_site_summary(doc, tmp_path / "rumours.json")
    assert json.loads((tmp_path / "rumours.json").read_text())["horizon_months"] == R.SHIPPED_HORIZON


def test_credibility_labels_need_the_whole_interval_on_one_side():
    lo, hi = R.ratio_interval(30, 15.0)  # twice as many came true as similar reports would have
    assert lo > 1 and R.credibility_label(200, lo, hi) == R.RELIABLE
    lo, hi = R.ratio_interval(5, 15.0)
    assert hi < 1 and R.credibility_label(200, lo, hi) == R.SPECULATIVE
    lo, hi = R.ratio_interval(16, 15.0)
    assert lo < 1 < hi and R.credibility_label(200, lo, hi) == R.AVERAGE
    assert R.credibility_label(R.MIN_OUTLET_LINKS - 1, 2.0, 3.0) == R.UNPROVEN  # too few to judge
    assert R.ratio_interval(0, 2.0)[0] == 0.0


def test_outlet_credibility_compares_with_what_similar_reports_did():
    lab = pd.DataFrame(
        {
            "outlet": ["Sharp"] * 100 + ["Loud"] * 100,
            "joined_6m": [True] * 30 + [False] * 70 + [True] * 2 + [False] * 98,
        }
    )
    # the same expected chance for every report: Sharp beat it, Loud fell short
    cred = R.outlet_credibility(lab, "joined_6m", np.full(200, 0.12))
    assert cred["Sharp"]["label"] == R.RELIABLE and cred["Loud"]["label"] == R.SPECULATIVE
    assert cred["Sharp"]["raw"] == 0.3 and abs(cred["Sharp"]["expected_rate"] - 0.12) < 1e-9


def _brief_links() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "rumour_id": ["c1-p0", "c1-p3", "c2-p1", "old-p0", "c3-p0"],
            "published_at": pd.to_datetime(
                ["2026-08-01", "2026-08-01", "2026-08-20", "2024-01-10", "2026-08-25"]
            ),
            "outlet": ["The Guardian", "The Guardian", "The Sun", "The Guardian", "The Times"],
            "player_name": ["Ann Able"] * 5,
            "tm_player_id": ["1"] * 5,
            "tm_club_id": ["31"] * 5,
        }
    )


def test_heat_counts_columns_and_ownership_groups_since_the_first_report():
    links = _brief_links()
    press = pd.DataFrame(
        {
            "outlet": ["BBC Sport", "BBC Sport", "ESPN"],
            "published_at": pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"]),
            # a transfer sentence naming both; another naming her with a different club; one without her
            "sentences": [
                [({"31"}, " liverpool are in talks to sign ann able ")],
                [({"631"}, " chelsea want able ")],
                [({"31"}, " liverpool sign someone else ")],
            ],
        }
    )
    r = links.iloc[-1]
    h = R.heat(links, press, r, pd.Timestamp("2026-09-01"))
    # columns c1, c2, c3 (two paragraphs of c1 are one report; the 2024 one is too old) + one BBC article;
    # The Sun and The Times are both News UK: one vote
    assert h["reports"] == 4 and h["since"] == "2026-08-01"
    assert h["outlets"] == 3 and h["outlet_names"] == "The Guardian, BBC Sport, The Sun"


def test_track_record_takes_the_same_outlets_latest_reports_on_this_buyer_first():
    lab = pd.DataFrame(
        {
            "outlet": ["The Sun"] * 4 + ["The Mirror"],
            "tm_player_id": ["2", "3", "4", "1", "5"],
            "player_name": ["Ben", "Cy", "Di", "Ann", "Ed"],
            "tm_club_id": ["31", "31", "99", "31", "31"],
            "club_name": ["Liverpool", "Liverpool", "Leeds", "Liverpool", "Liverpool"],
            "from_tm_club": ["7", "8", "10", "10", "9"],
            "published_at": pd.to_datetime(
                ["2025-01-02", "2025-08-01", "2026-01-05", "2024-01-01", "2026-02-01"]
            ),
            "joined_6m": [True, False, True, True, True],
        }
    )
    r = pd.Series({"outlet": "The Sun", "tm_player_id": "1", "tm_club_id": "31", "from_tm_club": "10"})
    past = R.track_record(lab, r)
    # not her own earlier rumour, not another outlet's; this buyer first (newest first), then her club's sales
    assert [p["player"] for p in past] == ["Cy", "Ben"]
    assert past[0]["outcome"] == R.DID_NOT and past[1]["outcome"] == R.CAME_TRUE
    assert R.track_record(lab, r, k=3)[2] == {
        "player": "Di",
        "club": "Leeds",
        "date": "2026-01-05",
        "outcome": R.CAME_TRUE,
        "kind": "same seller",
    }


def test_priority_is_credibility_times_heat():
    assert R.priority(R.RELIABLE, 2, 3) > R.priority(R.AVERAGE, 2, 9) > R.priority(R.AVERAGE, 1, 50)
    assert R.priority(R.SPECULATIVE, 2, 1) < R.priority(R.AVERAGE, 1, 1) + 0.01
