import pandas as pd
from dugout_models.lineup import best_formation, player_fit, recent_shape, shape_formations


def _xis(shapes):
    """Starting XIs (newest first) with the given (DEF, MID, FWD) counts by FPL position."""
    rows = []
    for k, (d, m, f) in enumerate(shapes):
        date = pd.Timestamp("2026-10-11") - pd.Timedelta(days=7 * k)
        for pos in ["GK"] + ["DEF"] * d + ["MID"] * m + ["FWD"] * f:
            rows.append({"match_id": f"m{k}", "date": date, "fpl_pos": pos})
    return pd.DataFrame(rows)


def test_recent_shape_is_the_most_common_with_ties_to_the_newest():
    assert recent_shape(_xis([(4, 5, 1), (4, 5, 1), (3, 5, 2)])) == (4, 1)
    assert recent_shape(_xis([(5, 4, 1), (4, 5, 1)])) == (5, 1)  # tie: the newest XI wins
    assert recent_shape(_xis([])) is None


def test_shape_formations_follow_the_back_line_and_strikers():
    assert set(shape_formations((4, 1))) == {"4-3-3", "4-2-3-1"}
    assert shape_formations((4, 2)) == ["4-4-2"]
    assert shape_formations((5, 1)) == ["3-4-2-1"]
    assert shape_formations((3, 1)) == ["3-4-2-1"]


def test_fpl_defenders_stay_in_the_back_line():
    assert player_fit("CB", "DEF", "DM") == 0.0
    assert player_fit("CB", "DEF", "RB") == 0.75
    assert player_fit("RM", "MID", "RWB") == 0.75  # a wing-back listed MID may still drop into the back line


def test_three_centre_backs_play_a_back_four_when_the_manager_does():
    # three good Transfermarkt centre-backs used to pull the optimiser into a back three
    slots = ["GK", "CB", "CB", "CB", "LB", "RB", "DM", "CM", "CM", "AM", "RW", "LW", "CF", "RWB", "LWB"]
    fpl = [
        "GK",
        "DEF",
        "DEF",
        "DEF",
        "DEF",
        "DEF",
        "MID",
        "MID",
        "MID",
        "MID",
        "MID",
        "MID",
        "FWD",
        "DEF",
        "DEF",
    ]
    squad = pd.DataFrame({"player_id": [f"p{i}" for i in range(len(slots))], "slot": slots, "fpl_pos": fpl})
    squad["rating"] = [1.0, 2.0, 2.0, 2.0, 1.0, -1.0, 1, 1, 1, 1, 1, 1, 1, -2.0, -2.0]
    squad["available"], squad["fatigue"] = True, 0.0
    sel = best_formation(squad, shape_formations((4, 1)))
    assert sel.formation in {"4-3-3", "4-2-3-1"}
    assert sorted(s for _, s in sel.xi if s in {"CB", "RB", "LB"}) == ["CB", "CB", "LB", "RB"]
