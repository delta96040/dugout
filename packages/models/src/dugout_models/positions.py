"""Where players actually start: transfermarkt-datasets `game_lineups` (CC0), starting XIs since 2023-24.

Transfermarkt's profile names one primary position, so versatile players were filed in one box: a midfielder who
often plays the holding role ("Central Midfield", e.g. Alex Scott, Lamine Camara) never reached a defensive-midfield
shortlist, and a centre-back who also plays right-back only fitted right-back "partly". The starting positions in
real lineups say where a player is actually used.
"""

from __future__ import annotations

import pandas as pd

from dugout_models.valuation import TM_DIR

POOL_SHARE = 0.25  # share of starts at a position to count him for that position on a shortlist
SLOT_SHARE, SLOT_STARTS = 0.10, 5  # to count a position as natural in the lineup model


def played() -> pd.DataFrame:
    """One row per (Transfermarkt player id, position): starts there and share of his starts."""
    path = TM_DIR / "game_lineups.parquet"
    if not path.exists():
        return pd.DataFrame(columns=["player_id", "position", "starts", "share"])
    gl = pd.read_parquet(path, columns=["player_id", "position"])
    gl["player_id"] = gl.player_id.astype(str)
    out = gl.groupby(["player_id", "position"]).size().rename("starts").reset_index()
    out["share"] = out.starts / out.groupby("player_id").starts.transform("sum")
    return out


def positions_played(min_share: float = POOL_SHARE, min_starts: int = 1) -> pd.Series:
    """Transfermarkt player id -> set of Transfermarkt position labels he starts at often enough."""
    p = played()
    p = p[(p.share >= min_share) & (p.starts >= min_starts)]
    return p.groupby("player_id").position.agg(set)
