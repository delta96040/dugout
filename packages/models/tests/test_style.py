"""Playing-style model: percentiles, top shares, archetype roles, fit against the starter, similar players."""

from __future__ import annotations

import numpy as np
import pandas as pd
from dugout_models import style


def _stats(n: int = 40, seed: int = 3) -> pd.DataFrame:
    """n synthetic Premier League midfielders with 1,800 minutes; player 0 creates the most, player 1 tackles the
    most and player 2 is player 0's near twin."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "tm_player_id": [str(i) for i in range(n)],
            "season": 2024,
            "league_id": 39,
            "player_name": [f"P{i}" for i in range(n)],
            "team_name": "X",
            "minutes": 1800,
            **{f: rng.integers(10, 40, n).astype(float) for f in style.LABELS if f != "duel_win"},
        }
    )
    df["duels_won"] = df.duels * 0.5
    df["duels"] = df.duels + 40  # enough duels for a share of duels won
    df.loc[0, ["key_passes", "dribbles", "fouls_drawn"]] = [120, 80, 70]
    df.loc[2, ["key_passes", "dribbles", "fouls_drawn"]] = [115, 78, 66]
    df.loc[1, ["tackles", "interceptions", "duels"]] = [150, 90, 200]
    df["comp"], df["tier"] = "GB1", 1
    return df


def _model() -> style.StyleModel:
    df = _stats()
    m = style.StyleModel()
    m.fit(df, pd.Series("central midfield", index=df.tm_player_id))
    return m


def test_percentiles_top_shares_and_roles():
    m = _model()
    p0 = m.latest("0")
    pct = m.percentiles(p0, "midfielder")
    assert pct["key_passes"] > 0.95
    assert style.top_share(pct["key_passes"]) == 0.05  # the best in the pool is in the top 5%, not the top 0%
    assert style.top_share(0.91) == 0.10 and style.top_share(0.75) == 0.25
    assert p0.role == "creative midfielder"
    assert m.latest("1").role == "ball-winning midfielder"
    assert m.similar(p0, 1)[0][0] == "2"  # his near twin
    assert style.standouts(pct)[0][0] == "key_passes"


def test_no_role_without_a_clear_lead():
    z = pd.DataFrame({f"z_{f}": [0.1] for f in style.FEATURES["midfielder"]})
    labels, _ = style.archetype_roles(z, "midfielder")
    assert labels[0] is None


def test_fit_against_the_starter():
    m = _model()
    cand = style.describe(m, "0", "defensive midfield")
    starter = style.describe(m, "1", "central midfield")
    assert cand["pool"] == "midfielders in Europe's biggest leagues" and cand["season"] == "2024-25"
    feat, mine, theirs = style.fit_vs(cand, starter)
    assert feat in ("key_passes", "dribbles", "fouls_drawn") and mine - theirs >= style.ADDS_GAP
    assert style.fit_vs(cand, cand) is None  # nothing to add over himself
    assert all(v <= style.LACKS_MAX for _, v in style.lacks(starter))
    assert style.describe(m, "0", "goalkeeper") is None and style.describe(m, "unknown", "winger") is None


def test_small_pools_get_no_percentiles():
    df = _stats(n=10)
    m = style.StyleModel()
    m.fit(df, pd.Series("central midfield", index=df.tm_player_id))
    assert m.percentiles(m.latest("0"), "midfielder") == {}
