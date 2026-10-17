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
