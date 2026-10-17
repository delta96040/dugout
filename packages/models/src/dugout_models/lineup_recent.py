"""Predicted XI for leagues without FPL data: the club's last league XI, absentees replaced.

There are no minutes or ratings for these leagues, only who started (API-Football lineups, one call per finished
match, from the night after the match). Backtest (`dugout-models backtest lineup_recent`,
docs/backtests/lineup_recent.md): transfermarkt-datasets starting XIs (CC0) in La Liga, Serie A, Bundesliga, Ligue 1
and the Eredivisie. Recency-weighted start counts over the last `k` XIs were tuned on 2023-24; the best setting
reproduces the last XI, and nothing beat it, so the shipped rule is the last XI (about 77% of starters right in
2024-25 and 2025-26; the Premier League optimiser with FPL data: 78%). Live, a player API-Football lists as
missing for the match is replaced by the most recent other starter of the same kind (goalkeeper or outfield).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dugout_models.valuation import TM_DIR

COMPS = ("ES1", "IT1", "L1", "FR1", "NL1")
GRID_K = (3, 5, 8)
GRID_DECAY = (0.5, 0.7, 0.85, 1.0)
MIN_PRIOR = 3  # a club needs this many league XIs this season before a prediction is made


def pick_xi(history: pd.DataFrame, k: int, decay: float) -> list[str]:
    """history: rows (game order 0 = most recent, player_id, gk) for the club's previous league XIs."""
    h = history[history.back < k]
    if h.empty:
        return []
    score = (
        h.assign(w=decay ** h.back.astype(float)).groupby("player_id").agg(w=("w", "sum"), gk=("gk", "max"))
    )
    keepers = score[score.gk].sort_values("w", ascending=False).head(1)
    outfield = score[~score.gk].sort_values("w", ascending=False).head(11 - len(keepers))
    return [*keepers.index, *outfield.index]


def _league_xis(tm_dir: Path = TM_DIR) -> pd.DataFrame:
    lu = pd.read_parquet(tm_dir / "game_lineups.parquet")
    ap = pd.read_parquet(tm_dir / "appearances.parquet", columns=["game_id", "competition_id"])
    comp = ap.drop_duplicates("game_id").set_index("game_id").competition_id
    lu = lu[lu.game_id.map(comp).isin(COMPS)].copy()
    lu["date"] = pd.to_datetime(lu.date)
    lu["season"] = np.where(lu.date.dt.month >= 7, lu.date.dt.year, lu.date.dt.year - 1)
    lu["gk"] = lu.position == "Goalkeeper"
    lu["player_id"] = lu.player_id.astype(str)
    return lu


def evaluate(lu: pd.DataFrame, seasons: list[int], k: int, decay: float) -> dict:
    overlap, last_overlap = [], []
    for (_club, _season), g in lu[lu.season.isin(seasons)].groupby(["club_id", "season"]):
        games = g[["game_id", "date"]].drop_duplicates().sort_values("date").game_id.tolist()
        xis = {gid: set(g.loc[g.game_id == gid, "player_id"]) for gid in games}
        for i in range(MIN_PRIOR, len(games)):
            prior = games[:i][::-1]  # most recent first
            order = {x: j for j, x in enumerate(prior)}
            hist = g[g.game_id.isin(prior)].assign(back=lambda d, order=order: d.game_id.map(order))
            actual = xis[games[i]]
            if len(actual) != 11:
                continue
            overlap.append(len(set(pick_xi(hist, k, decay)) & actual) / 11)
            last_overlap.append(len(xis[prior[0]] & actual) / 11)
    return {
        "overlap": float(np.mean(overlap)),
        "last_xi_overlap": float(np.mean(last_overlap)),
        "n": len(overlap),
    }


def backtest(out_dir: Path | None = None, tm_dir: Path = TM_DIR) -> dict:
    lu = _league_xis(tm_dir)
    grid = {f"k={k}, decay={d}": evaluate(lu, [2023], k, d) for k in GRID_K for d in GRID_DECAY}
    best = max(grid, key=lambda c: grid[c]["overlap"])
    k, decay = int(best.split(",")[0][2:]), float(best.split("=")[2])
    test = {str(s): evaluate(lu, [s], k, decay) for s in (2024, 2025)}
    by_comp = {c: evaluate(lu[lu.club_id.isin(_clubs_in(c, tm_dir))], [2024, 2025], k, decay) for c in COMPS}
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "chosen": {"k": k, "decay": decay},
        "validation_2023_24": grid,
        "test": test,
        "test_by_competition": by_comp,
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "lineup_recent.json").write_text(json.dumps(report, indent=2))
        lines = [
            "# Predicted XI from recent starts (leagues without FPL data)",
            "",
            f"Generated {report['generated_at']}. Data: transfermarkt-datasets starting XIs (CC0), league matches in "
            "La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie. A prediction needs three earlier league XIs that "
            "season. Overlap = share of the actual XI predicted.",
            "",
            f"Best on 2023-24: last {k} XIs, recency weight {decay} per match back, which reproduces the last XI. "
            "No weighting of older XIs beat it, so the shipped rule is the last league XI (absentees replaced).",
            "",
            "| Season | Best recency weighting | Last league XI (shipped) | Matches |",
            "|---|---|---|---|",
            *[
                f"| {s}-{(int(s) + 1) % 100:02d} | {v['overlap']:.1%} | {v['last_xi_overlap']:.1%} | {v['n']} |"
                for s, v in test.items()
            ],
            "",
            "By league (2024-25 and 2025-26):",
            "",
            "| League | Best recency weighting | Last league XI (shipped) | Matches |",
            "|---|---|---|---|",
            *[
                f"| {c} | {v['overlap']:.1%} | {v['last_xi_overlap']:.1%} | {v['n']} |"
                for c, v in by_comp.items()
            ],
            "",
            "Limitation: injuries and suspensions are not in the data, so neither method can drop absentees here; "
            "live, players API-Football lists as missing for the match are left out first.",
        ]
        (out_dir / "lineup_recent.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _clubs_in(comp: str, tm_dir: Path) -> set:
    clubs = pd.read_parquet(tm_dir / "clubs.parquet", columns=["club_id", "domestic_competition_id"])
    return set(clubs.loc[clubs.domestic_competition_id == comp, "club_id"])


def predict(history: pd.DataFrame, absent: set[str]) -> list[str]:
    """The last XI with absentees replaced by the most recent other starter of the same kind.
    history: (back, player_id, gk) rows for the club's previous league XIs, back 0 = the last match."""
    last = history[history.back == 0]
    xi = [p for p in last.player_id if p not in absent]
    gk = dict(zip(history.player_id, history.gk, strict=False))
    for kind in (True, False):
        need = (1 if kind else 10) - sum(1 for p in xi if gk.get(p) == kind)
        if need <= 0:
            continue
        bench = (
            history[(history.gk == kind) & ~history.player_id.isin([*xi, *absent])]
            .sort_values("back")
            .drop_duplicates("player_id")
            .player_id.tolist()
        )
        xi += bench[:need]
    return xi
