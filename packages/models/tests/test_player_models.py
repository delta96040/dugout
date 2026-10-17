import numpy as np
import pandas as pd
from dugout_models.vaep import labels, score_diff
from dugout_models.xthreat import ExpectedThreat


def _actions(rows):
    a = pd.DataFrame(
        rows,
        columns=["game_key", "team_id", "type_name", "result_name", "start_x", "start_y", "end_x", "end_y"],
    )
    a["period_id"], a["time_seconds"], a["bodypart_name"] = 1, np.arange(len(a), dtype=float), "foot"
    return a


def test_labels_score_and_concede_within_horizon():
    rows = [("g", 1, "pass", "success", 50, 34, 80, 34)] * 3 + [("g", 1, "shot", "success", 95, 34, 105, 34)]
    rows += [("g", 2, "pass", "success", 50, 34, 60, 30), ("g", 2, "shot", "owngoal", 10, 34, 0, 34)]
    y = labels(_actions(rows), horizon=10)
    assert y.scores.iloc[:4].all()  # team 1 scores within 10 actions of each of its actions
    assert y.concedes.iloc[4]  # team 2 concedes (its own goal follows)
    assert not y.scores.iloc[4]


def test_score_diff_credits_own_goals_to_opponent():
    rows = [
        ("g", 1, "shot", "success", 95, 34, 105, 34),
        ("g", 2, "pass", "success", 50, 34, 60, 34),
        ("g", 2, "clearance", "owngoal", 5, 34, 0, 34),
        ("g", 1, "pass", "success", 50, 34, 60, 34),
    ]
    d = score_diff(_actions(rows))
    assert list(d) == [0, -1, -1, 2]


def test_xt_values_rise_towards_goal_and_converge():
    rng = np.random.default_rng(0)
    rows = []
    for _ in range(4000):  # moves towards goal; shots near goal score more often
        x = rng.uniform(0, 100)
        y = rng.uniform(10, 58)
        if x > 85 and rng.random() < 0.3:
            rows.append(
                ("g", 1, "shot", "success" if rng.random() < (x - 80) / 40 else "fail", x, y, 105, 34)
            )
        else:
            rows.append(
                ("g", 1, "pass", "success" if rng.random() < 0.8 else "fail", x, y, min(x + 15, 104), y)
            )
    xt = ExpectedThreat().fit(_actions(rows))
    grid = xt.grid
    assert xt.iterations < xt.max_iter
    assert grid[:, -1].mean() > grid[:, 0].mean()  # final third worth more than own third
    vals = xt.rate(_actions(rows[:50]))
    assert np.isnan(vals[[r[2] == "shot" for r in rows[:50]]]).all()


def test_last_xi_replaces_listed_absentees_with_recent_starters():
    import pandas as pd
    from dugout_models.lineup_recent import predict

    last = [("gk1", True)] + [(f"o{i}", False) for i in range(10)]
    older = [("gk2", True)] + [(f"o{i}", False) for i in range(9)] + [("o10", False)]
    rows = [(0, p, g) for p, g in last] + [(1, p, g) for p, g in older]
    hist = pd.DataFrame(rows, columns=["back", "player_id", "gk"])
    xi = predict(hist, absent={"gk1", "o3"})
    assert len(xi) == 11 and "gk2" in xi and "o10" in xi and "gk1" not in xi and "o3" not in xi
    assert predict(hist, absent=set()) == [p for p, _ in last]
