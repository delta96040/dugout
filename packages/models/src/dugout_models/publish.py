"""`dugout-models publish`: fit the shipped models on everything known today and write their current outputs to
the warehouse table `model_outputs`. Agents cite these rows through the evidence ledger (`sql` queries), so every
model number on the site can be re-read by the Stat Auditor.

Each block uses exactly the model configuration that was backtested (docs/backtests/); nothing is re-tuned here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from dugout_pipeline.db import upsert

log = logging.getLogger(__name__)

VERSION = "0.3"


def _row(
    model: str, etype: str, eid: str, metric: str, value: Any, as_of: datetime, context: str = ""
) -> dict:
    num = (
        float(value)
        if isinstance(value, int | float | np.floating | np.integer) and not isinstance(value, bool)
        else None
    )
    text = None if num is not None or value is None else str(value)
    return {
        "model": model,
        "version": f"{model}@{VERSION}",
        "entity_type": etype,
        "entity_id": eid,
        "metric": metric,
        "context": context,
        "value_num": num,
        "value_text": text,
        "as_of": as_of,
    }


def write(con: duckdb.DuckDBPyConnection, model: str, rows: Iterable[dict]) -> int:
    rows = list(rows)
    con.execute(
        "DELETE FROM model_outputs WHERE model = ?", [model]
    )  # a publish replaces the model's outputs
    return upsert(con, "model_outputs", rows)


def backtested(model: str) -> list[str]:
    """Leagues whose config lists `model` under `backtested`: a model publishes only where it was backtested
    (the predictor and lineup models train on Premier League data and FPL minutes)."""
    from dugout_pipeline.clubs import league_config, leagues

    return [lg for lg in leagues() if model in league_config(lg).get("backtested", [])]


# ---- match predictor ---------------------------------------------------------------------------------------
def predictions(con: duckdb.DuckDBPyConnection, now: datetime, days: int = 21) -> list[dict]:
    from dugout_models.backtest import FIRST_TRAIN_SEASON, PRIMARY, build_frame
    from dugout_models.dixon_coles import fit_map
    from dugout_models.elo import elo_state
    from dugout_models.features import XG_WEIGHT, promoted_priors
    from dugout_models.predictor import StackedPredictor

    assert PRIMARY == "stack_pre_lineup"
    m = build_frame(con)
    model = StackedPredictor().fit(m[m.season >= FIRST_TRAIN_SEASON])
    up = con.execute(
        """SELECT match_id, season, kickoff_utc, home_club_id AS home, away_club_id AS away FROM matches
           WHERE status = 'scheduled' AND kickoff_utc >= ? AND kickoff_utc < ? AND list_contains(?, league)
           ORDER BY kickoff_utc""",
        [now, now + pd.Timedelta(days=days), backtested("predictor")],
    ).df()
    if up.empty:
        return []
    elo = elo_state(m)
    t0 = pd.Timestamp(now.date())
    mm = m.copy()
    hx, ax = mm.home_xg.notna(), mm.away_xg.notna()
    mm["home_t"] = np.where(
        hx, (1 - XG_WEIGHT) * mm.home_goals + XG_WEIGHT * mm.home_xg.fillna(0), mm.home_goals
    )
    mm["away_t"] = np.where(
        ax, (1 - XG_WEIGHT) * mm.away_goals + XG_WEIGHT * mm.away_xg.fillna(0), mm.away_goals
    )
    season = up.season.iloc[0]
    start = mm.loc[mm.season == season, "date"].min()
    hist_start = mm[(mm.date < start) & (mm.date >= start - pd.Timedelta(days=3 * 365))]
    priors = (
        promoted_priors(mm, season, hist_start, start)
        if season in set(mm.season)
        else {"goals": {}, "blend": {}}
    )
    hist = mm[(mm.date < t0) & (mm.date >= t0 - pd.Timedelta(days=3 * 365))]
    dc = fit_map(hist, t0, prior_mean=priors["goals"])
    dcx = fit_map(hist, t0, target=("home_t", "away_t"), prior_mean=priors["blend"])
    f = up.copy()
    f["elo_home"] = [elo.ratings.get(h, elo.initial) for h in f.home]
    f["elo_away"] = [elo.ratings.get(a, elo.initial) for a in f.away]
    f["elo_diff"] = f.elo_home + elo.home_adv - f.elo_away
    for prefix, params in (("dc", dc), ("dcx", dcx)):
        p = np.array([params.probs(h, a) for h, a in zip(f.home, f.away, strict=True)])
        f[f"{prefix}_ph"], f[f"{prefix}_pd"], f[f"{prefix}_pa"] = p.T
    for c in (
        "home_xgf_l10",
        "away_xgf_l10",
        "home_gf_l10",
        "away_gf_l10",
        "home_xga_l10",
        "away_xga_l10",
        "home_ga_l10",
        "away_ga_l10",
        "home_pts_l5",
        "away_pts_l5",
        "home_rest_days",
        "away_rest_days",
    ):
        f[c] = np.nan
    probs = model.predict(f)
    rows = []
    for r, p in zip(f.itertuples(), probs, strict=True):
        lam, mu = dcx.rates(r.home, r.away)
        for metric, v in (
            ("p_home_win", p[0]),
            ("p_draw", p[1]),
            ("p_away_win", p[2]),
            ("exp_home_goals", lam),
            ("exp_away_goals", mu),
        ):
            rows.append(_row("predictor", "match", r.match_id, metric, round(float(v), 4), now))
        rows.append(_row("predictor", "match", r.match_id, "kickoff_utc", r.kickoff_utc.isoformat(), now))
    for club, rating in elo.ratings.items():
        if club in set(up.home) | set(up.away):
            rows.append(_row("predictor", "club", club, "elo", round(rating, 1), now))
    # append-only log of what was predicted before kick-off: the scoreboard's live record
    con.executemany(
        "INSERT OR IGNORE INTO prediction_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            [
                r.match_id,
                now,
                r.kickoff_utc,
                f"predictor@{VERSION}",
                round(float(p[0]), 4),
                round(float(p[1]), 4),
                round(float(p[2]), 4),
            ]
            for r, p in zip(f.itertuples(), probs, strict=True)
        ],
    )
    return rows


# ---- shared: squads, positions, availability ---------------------------------------------------------------
def tm_positions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """player_id (reep) -> lineup slot code, from transfermarkt-datasets sub-positions via the Reep crosswalk."""
    from dugout_pipeline.ids import attach_catalogue

    from dugout_models.injury import TM_DIR
    from dugout_models.lineup import SUB

    tm = pd.read_parquet(TM_DIR / "players.parquet", columns=["player_id", "sub_position"])
    tm["player_id"] = tm.player_id.astype(str)
    if not attach_catalogue(con):
        return pd.DataFrame(columns=["player_id", "slot"])
    bridge = con.execute(
        "SELECT reep_id, external_id FROM reep.bridges WHERE provider='transfermarkt' AND namespace='spieler'"
    ).df()
    from dugout_models.positions import SLOT_SHARE, SLOT_STARTS, positions_played

    # positions he starts at often enough count as natural too (a centre-back who also plays right-back)
    alt = positions_played(SLOT_SHARE, SLOT_STARTS).map(lambda ps: {SUB[x] for x in ps if x in SUB})
    out = (
        bridge.merge(tm, left_on="external_id", right_on="player_id")
        .assign(slot=lambda d: d.sub_position.map(SUB), alt=lambda d: d.external_id.map(alt))[
            ["reep_id", "slot", "alt"]
        ]
        .rename(columns={"reep_id": "player_id"})
        .dropna(subset=["slot"])
        .drop_duplicates("player_id")
    )
    return out


FPL_SLOT = {"GK": "GK", "DEF": "CB", "MID": "CM", "FWD": "CF"}


def unavailable(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        """SELECT player_id FROM injuries WHERE source = 'fpl'
           AND snapshot_date = (SELECT max(snapshot_date) FROM injuries WHERE source = 'fpl')
           AND (status IN ('injured', 'suspended', 'unavailable') OR coalesce(chance_next, 100) < 50)"""
    ).fetchall()
    return {r[0] for r in rows}


# ---- lineup optimiser --------------------------------------------------------------------------------------
def lineups(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    from dugout_models.lineup import (
        LINE,
        PREDICTED_XI_TRUST,
        UNUSED_FACTOR,
        UNUSED_SHARE,
        best_formation,
        ratings_from_history,
        recent_shape,
        shape_formations,
    )

    nxt = con.execute(
        """SELECT club, arg_min(match_id, kickoff_utc) AS match_id FROM (
               SELECT home_club_id AS club, match_id, kickoff_utc, league FROM matches
               WHERE status='scheduled' AND kickoff_utc >= ?
               UNION ALL SELECT away_club_id, match_id, kickoff_utc, league FROM matches
               WHERE status='scheduled' AND kickoff_utc >= ?)
           WHERE list_contains(?, league) GROUP BY club""",
        [now, now, backtested("lineup")],
    ).df()
    stats = con.execute(
        """SELECT s.match_id, CAST(m.kickoff_utc AS DATE) AS date, s.club_id, s.player_id, s.minutes, s.starts,
                  coalesce(s.influence, 0) + coalesce(s.creativity, 0) + coalesce(s.threat, 0) AS ict
           FROM player_match_stats s JOIN matches m USING (match_id)
           WHERE s.source IN ('fpl', 'fpl_history') AND m.kickoff_utc >= ? AND m.kickoff_utc < ?
             AND s.starts IS NOT NULL""",
        [now - pd.Timedelta(days=400), now],
    ).df()
    stats["date"] = pd.to_datetime(stats.date)
    stats["fpl_pos"] = stats.player_id.map(
        dict(con.execute("SELECT player_id, position FROM players").fetchall())
    )
    squads = con.execute("SELECT player_id, club_id, position FROM players WHERE club_id IS NOT NULL").df()
    pos = tm_positions(con)
    squads = squads.merge(pos, on="player_id", how="left")
    squads["slot"] = squads.slot.fillna(squads.position.map(FPL_SLOT))
    squads["fpl_pos"] = squads.position  # FPL defenders stay in the back line (lineup.player_fit)
    out_ids = unavailable(con)
    rows = []
    for r in nxt.itertuples():
        squad = squads[squads.club_id == r.club].copy()
        # quality from the player's last ~38 league matches (any club), trust from this club's last five
        hist = stats[stats.player_id.isin(squad.player_id)].merge(
            squad[["player_id", "slot"]], on="player_id"
        )
        hist["line"] = hist.slot.map(LINE)
        quality = squad.player_id.map(ratings_from_history(hist)).fillna(-1.0)
        club_games = stats[stats.club_id == r.club].drop_duplicates("match_id").nlargest(5, "date").match_id
        last5 = stats[stats.match_id.isin(club_games) & (stats.club_id == r.club)]
        share = squad.player_id.map(last5.groupby("player_id").minutes.sum() / (5 * 90)).fillna(0)
        trust = (share - share.mean()) / (share.std() or 1)
        last7 = (
            stats[stats.date >= pd.Timestamp(now.date()) - pd.Timedelta(days=7)]
            .groupby("player_id")
            .minutes.sum()
        )
        squad["available"] = ~squad.player_id.isin(out_ids)
        squad["fatigue"] = (squad.player_id.map(last7).fillna(0) - 135).clip(lower=0) / 90 * 0.5
        squad["rating"] = (1 - PREDICTED_XI_TRUST) * quality + PREDICTED_XI_TRUST * trust
        squad["weight"] = np.where(share.to_numpy() > UNUSED_SHARE, 1.0, UNUSED_FACTOR)  # out of the rotation
        squad = squad.reset_index(drop=True)
        # the shape the manager actually starts (docs/backtests/lineup_shape.md), then the best XI within it
        club_xis = stats[(stats.club_id == r.club) & (stats.starts == 1)]
        try:
            sel = best_formation(squad, shape_formations(recent_shape(club_xis)))
        except ValueError as exc:
            log.warning("lineup %s: %s", r.club, exc)
            continue
        rows.append(_row("lineup", "club", r.club, "predicted_formation", sel.formation, now, r.match_id))
        for pid, slot in sel.xi:
            rows.append(_row("lineup", "player", pid, "predicted_xi_slot", slot, now, r.match_id))
        for pid in squad.loc[~squad.available, "player_id"]:
            rows.append(_row("lineup", "player", pid, "unavailable", "unavailable", now, r.match_id))
    return rows


# ---- injury risk -------------------------------------------------------------------------------------------
TIERS = [(0.5, "low"), (0.8, "moderate"), (0.95, "elevated"), (1.01, "high")]


def injury_risk(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    """Shown as tiers (percentile of predicted hazard among training rows): calibration is limited by
    season-to-season shifts in injury reporting (docs/backtests/injury.md)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    from dugout_models.injury import FEATURES, _time_window_sum, attach_player_facts, build, injury_episodes

    train = build(con)
    model = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)).fit(
        train[FEATURES], train.label
    )
    ref = np.sort(model.predict_proba(train[FEATURES])[:, 1])
    rows = con.execute(
        """SELECT s.player_id, CAST(m.kickoff_utc AS DATE) AS date, m.season, s.minutes
           FROM player_match_stats s JOIN matches m USING (match_id)
           WHERE s.source IN ('fpl', 'fpl_history') AND s.minutes > 0 AND m.kickoff_utc >= ? AND m.kickoff_utc < ?""",
        [now - pd.Timedelta(days=60), now],
    ).df()
    current = set(con.execute("SELECT player_id FROM players WHERE club_id IS NOT NULL").df().player_id)
    rows = rows[rows.player_id.isin(current)]
    if rows.empty:
        return []
    rows["date"] = pd.to_datetime(rows.date)
    rows = rows.sort_values(["player_id", "date"]).reset_index(drop=True)
    g = rows.groupby("player_id")
    rows["min_last3"] = g.minutes.transform(lambda s: s.rolling(3, min_periods=1).sum())
    rows["min_28d"] = _time_window_sum(rows, "minutes", 28)
    rows["min_7d"] = _time_window_sum(rows, "minutes", 7)
    rows["matches_14d"] = _time_window_sum(rows.assign(one=1), "one", 14)
    rows["season_week"] = (rows.date - rows.groupby("season").date.transform("min")).dt.days // 7
    last = rows.groupby("player_id").tail(1).reset_index(drop=True)
    ep = injury_episodes()
    by_player = dict(tuple(ep.groupby("player_id")))
    feats = {
        "prev_episodes": [],
        "days_since_episode": [],
        "episodes_365": [],
        "days_lost_365": [],
        "last_muscle": [],
    }
    for pid, d in zip(last.player_id, last.date, strict=True):
        e = by_player.get(pid)
        if e is None:
            for k, v in (
                ("prev_episodes", 0),
                ("days_since_episode", 999),
                ("episodes_365", 0),
                ("days_lost_365", 0),
                ("last_muscle", 0),
            ):
                feats[k].append(v)
            continue
        past = e[e.date <= d]
        recent = past[past.date > d - pd.Timedelta(days=365)]
        feats["prev_episodes"].append(len(past))
        feats["days_since_episode"].append(min((d - past.date.max()).days, 999) if len(past) else 999)
        feats["episodes_365"].append(len(recent))
        feats["days_lost_365"].append(recent.days_out.sum())
        feats["last_muscle"].append(past.muscle.iloc[-1] if len(past) else 0)
    last = last.assign(**feats)
    last = attach_player_facts(con, last).dropna(subset=["age"])
    last["age2"] = (last.age - 27) ** 2
    p = model.predict_proba(last[FEATURES])[:, 1]
    pct = np.searchsorted(ref, p) / len(ref)
    out = []
    for pid, q in zip(last.player_id, pct, strict=True):
        tier = next(name for cut, name in TIERS if q < cut)
        out.append(_row("injury", "player", pid, "injury_risk_tier", tier, now))
        out.append(_row("injury", "player", pid, "injury_risk_percentile", round(float(q), 3), now))
    return out


# ---- roles & similarity ------------------------------------------------------------------------------------
def roles(con: duckdb.DuckDBPyConnection, now: datetime, season: str = "2025-26") -> list[dict]:
    from dugout_models.roles import feature_set, fit_roles, season_profiles, similarity

    prof = season_profiles(con)
    prof = prof[prof.season == season].reset_index(drop=True)
    fitted = fit_roles(prof)
    rows = []
    for r in fitted.itertuples():
        rows.append(_row("roles", "player", r.player_id, "role", r.role, now, season))
        rows.append(_row("roles", "player", r.player_id, "umap_x", round(float(r.umap_x), 4), now, season))
        rows.append(_row("roles", "player", r.player_id, "umap_y", round(float(r.umap_y), 4), now, season))
    for pos, grp in fitted.groupby("position"):
        S, p = similarity(grp, feature_set(pos, grp))
        for i, pid in enumerate(p.player_id):
            order = [j for j in np.argsort(-S[i]) if j != i][:3]
            for k, j in enumerate(order, 1):
                ctx = f"{season}:{k}"
                rows.append(_row("roles", "player", pid, "similar_player", p["name"].iloc[j], now, ctx))
                rows.append(_row("roles", "player", pid, "similar_player_id", p.player_id.iloc[j], now, ctx))
                rows.append(_row("roles", "player", pid, "similarity", round(float(S[i, j]), 3), now, ctx))
    return rows


# ---- manager fingerprint -----------------------------------------------------------------------------------
def fingerprints(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    from dugout_models.fingerprint import STYLE, manager_fingerprints

    fp = manager_fingerprints(con)
    current = con.execute(
        "SELECT manager_id, club_id FROM managers WHERE source = 'wikidata' AND club_id IS NOT NULL"
    ).df()
    fp = fp.merge(current, on="manager_id")
    rows = []
    for r in fp.itertuples():
        rows.append(_row("fingerprint", "manager", r.manager_id, "club", r.club_id, now))
        rows.append(_row("fingerprint", "manager", r.manager_id, "pl_matches", int(r.matches), now))
        for c in STYLE:
            v, z = getattr(r, c), getattr(r, f"z_{c}")
            if pd.notna(v):
                rows.append(_row("fingerprint", "manager", r.manager_id, c, round(float(v), 4), now))
                rows.append(_row("fingerprint", "manager", r.manager_id, f"z_{c}", round(float(z), 3), now))
    return rows


MODELS = {
    "predictor": predictions,
    "lineup": lineups,
    "injury": injury_risk,
    "roles": roles,
    "fingerprint": fingerprints,
}


def publish(
    con: duckdb.DuckDBPyConnection, models: list[str] | None = None, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    from dugout_models.market import market
    from dugout_models.rumours import publish as rumours

    registry = {**MODELS, "market": market, "rumours": rumours}
    counts = {}
    for name in models or list(registry):
        rows = registry[name](con, now)
        model_names = {r["model"] for r in rows} or {name}
        for mn in model_names:
            counts[mn] = write(con, mn, [r for r in rows if r["model"] == mn])
        log.info("published %s: %s", name, counts)
    return counts


# The live prediction log is append-only and committed to git (data/prediction_log.csv): in CI the warehouse is
# only an Actions cache that can be evicted, and the commit history shows each forecast existed before kick-off.
LOG_COLUMNS = {
    "match_id": "VARCHAR",
    "as_of": "TIMESTAMPTZ",
    "kickoff_utc": "TIMESTAMPTZ",
    "model": "VARCHAR",
    "p_home": "DOUBLE",
    "p_draw": "DOUBLE",
    "p_away": "DOUBLE",
}


def load_prediction_log(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Add the committed log's rows to the warehouse (rows already there are kept as they are)."""
    if not path.exists():
        return 0
    before = con.execute("SELECT count(*) FROM prediction_log").fetchone()[0]
    con.execute(
        "INSERT OR IGNORE INTO prediction_log SELECT * FROM read_csv(?, header = true, columns = ?)",
        [str(path), LOG_COLUMNS],
    )
    return con.execute("SELECT count(*) FROM prediction_log").fetchone()[0] - before


def save_prediction_log(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    """Write the whole log back, oldest first, so new forecasts show up as appended lines in git."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM prediction_log ORDER BY as_of, match_id, model) TO '{path.as_posix()}' (HEADER)"
    )
    return con.execute("SELECT count(*) FROM prediction_log").fetchone()[0]
