"""`dugout-models publish`: fit the shipped models on everything known today and write their current outputs to
the warehouse table `model_outputs`. Agents cite these rows through the evidence ledger (`sql` queries), so every
model number on the site can be re-read by the Stat Auditor.

Each block uses exactly the model configuration that was backtested (docs/backtests/); nothing is re-tuned here.
"""

from __future__ import annotations

import json
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
def forecast_leagues() -> dict[str, str]:
    """League -> forecast it publishes: "predictor" (the stack) where it beat Elo on every score in both test
    seasons, "elo" (the Elo baseline, the simpler of the two) where the stack did not (docs/backtests/predictor_*)."""
    out = {lg: "elo" for lg in backtested("elo_forecast")}
    out.update({lg: "predictor" for lg in backtested("predictor")})
    return out


def predictions(con: duckdb.DuckDBPyConnection, now: datetime, days: int = 21) -> list[dict]:
    rows: list[dict] = []
    for league, kind in forecast_leagues().items():
        rows += _league_predictions(con, now, days, league, kind)
    return rows


def _league_predictions(
    con: duckdb.DuckDBPyConnection, now: datetime, days: int, league: str, kind: str
) -> list[dict]:
    from dugout_models.backtest import FIRST_TRAIN_SEASON, PRIMARY, build_frame
    from dugout_models.dixon_coles import fit_map
    from dugout_models.elo import EloOutcomeModel, elo_state
    from dugout_models.features import XG_WEIGHT, promoted_priors
    from dugout_models.predictor import StackedPredictor

    assert PRIMARY == "stack_pre_lineup"
    up = con.execute(
        """SELECT match_id, season, kickoff_utc, home_club_id AS home, away_club_id AS away FROM matches
           WHERE status = 'scheduled' AND kickoff_utc >= ? AND kickoff_utc < ? AND league = ?
           ORDER BY kickoff_utc""",
        [now, now + pd.Timedelta(days=days), league],
    ).df()
    if up.empty:
        return []
    m = build_frame(con, league)
    train = m[m.season >= FIRST_TRAIN_SEASON]
    elo = elo_state(m)
    f = up.copy()
    f["elo_home"] = [elo.ratings.get(h, elo.initial) for h in f.home]
    f["elo_away"] = [elo.ratings.get(a, elo.initial) for a in f.away]
    f["elo_diff"] = f.elo_home + elo.home_adv - f.elo_away
    dcx = None
    if kind == "elo":
        probs = EloOutcomeModel().fit(train.elo_diff, train.result.to_numpy()).predict(f.elo_diff)
    else:
        model = StackedPredictor().fit(train)
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
        metrics = [("p_home_win", p[0]), ("p_draw", p[1]), ("p_away_win", p[2])]
        if dcx is not None:  # expected goals come from the stack's Dixon-Coles part
            lam, mu = dcx.rates(r.home, r.away)
            metrics += [("exp_home_goals", lam), ("exp_away_goals", mu)]
        for metric, v in metrics:
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
                f"{kind}@{VERSION}",
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


LAST_XI_MIN_KNOWN = (
    9  # starters of the last XI that must match the squad before the rest is filled by position
)


def last_xi_lineups(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    """Leagues without FPL minutes (config `backtested: lineup_last_xi`): the club's last league XI from
    API-Football lineups, players API-Football lists as missing replaced by the most recent other starter
    (lineup_recent.md: about 77% of starters right). Slots from Transfermarkt positions in the formation the
    club last used. A club appears once one of its league matches this season has a recorded lineup."""
    from dugout_models.lineup import FORMATIONS, best_formation, optimise
    from dugout_models.lineup_recent import predict

    leagues = backtested("lineup_last_xi")
    if not leagues:
        return []
    nxt = con.execute(
        """SELECT club, arg_min(match_id, kickoff_utc) AS match_id FROM (
               SELECT home_club_id AS club, match_id, kickoff_utc, league FROM matches
               WHERE status='scheduled' AND kickoff_utc >= ?
               UNION ALL SELECT away_club_id, match_id, kickoff_utc, league FROM matches
               WHERE status='scheduled' AND kickoff_utc >= ?)
           WHERE list_contains(?, league) GROUP BY club""",
        [now, now, leagues],
    ).df()
    lu = con.execute(
        """SELECT l.club_id, l.player_id, l.formation, m.kickoff_utc FROM lineups l JOIN matches m USING (match_id)
           WHERE l.source = 'api_football' AND l.starter AND list_contains(?, m.league)
             AND m.season = (SELECT max(season) FROM matches) AND m.kickoff_utc < ?""",
        [leagues, now],
    ).df()
    if lu.empty or nxt.empty:
        return []
    absent = set(
        con.execute(
            """SELECT player_id FROM injuries WHERE source = 'api_football'
               AND snapshot_date = (SELECT max(snapshot_date) FROM injuries WHERE source = 'api_football')"""
        )
        .df()
        .player_id
    )
    squads = con.execute("SELECT player_id, club_id, position FROM players WHERE club_id IS NOT NULL").df()
    squads = squads.merge(tm_positions(con), on="player_id", how="left")
    squads["slot"] = squads.slot.fillna(squads.position.map(FPL_SLOT))
    rows = []
    for r in nxt.itertuples():
        mine = squads[squads.club_id == r.club]
        g = lu[(lu.club_id == r.club) & lu.player_id.isin(mine.player_id)].copy()
        if g.empty:
            # say why, so the next run's log tells a club-name mismatch from a player-id one
            here = lu[lu.club_id == r.club]
            stem = r.club.split("-")[0][:5]
            similar = sorted({c for c in lu.club_id.unique() if stem and stem in c and c != r.club})[:3]
            fam = lambda ids: sorted({str(i).split(":")[0] if ":" in str(i) else "reep" for i in ids})  # noqa: E731
            log.warning(
                "last-XI lineup %s: no recorded starter in the squad list (lineup rows %d, squad %d, lineup ids %s, "
                "squad ids %s, similar lineup clubs %s)",
                r.club,
                len(here),
                len(mine),
                fam(here.player_id),
                fam(mine.player_id),
                similar,
            )
            continue
        order = {k: i for i, k in enumerate(sorted(g.kickoff_utc.unique(), reverse=True))}
        g["back"] = g.kickoff_utc.map(order)
        g["gk"] = g.player_id.map(dict(zip(mine.player_id, mine.position == "GK", strict=True)))
        xi = predict(g[["back", "player_id", "gk"]], absent)
        if len(xi) < LAST_XI_MIN_KNOWN:
            log.warning("last-XI lineup %s: only %d of 11 starters matched to the squad", r.club, len(xi))
            continue
        if len(xi) < 11:
            # a starter or two the id register cannot match (or newly injured): fill the gap with the squad's best
            # fit for the open slots, preferring players who started most recently for the club
            starts = g.groupby("player_id").back.min()
            pool = mine[~mine.player_id.isin(absent) | mine.player_id.isin(xi)].copy()
            pool["rating"] = np.where(
                pool.player_id.isin(xi), 10.0, 5.0 - pool.player_id.map(starts).fillna(5.0).clip(upper=5.0)
            )
            pool = pool.assign(available=True, fatigue=0.0, fpl_pos=pool.position).reset_index(drop=True)
            last = g.loc[g.back == 0, "formation"].dropna()
            try:
                fill = (
                    optimise(pool, last.iloc[0])
                    if len(last) and last.iloc[0] in FORMATIONS
                    else best_formation(pool)
                )
            except ValueError:
                log.warning("last-XI lineup %s: could not fill %d open slots", r.club, 11 - len(xi))
                continue
            xi = [pid for pid, _ in fill.xi]
        sq = mine[mine.player_id.isin(xi)].assign(available=True, rating=0.0, fatigue=0.0)
        sq = sq.assign(fpl_pos=sq.position).reset_index(drop=True)
        last = g.loc[g.back == 0, "formation"].dropna()
        try:
            sel = (
                optimise(sq, last.iloc[0]) if len(last) and last.iloc[0] in FORMATIONS else best_formation(sq)
            )
        except ValueError:
            try:
                sel = best_formation(sq)
            except ValueError:
                log.warning("last-XI lineup %s: no formation fits", r.club)
                continue
        rows.append(_row("lineup", "club", r.club, "predicted_formation", sel.formation, now, r.match_id))
        for pid, slot in sel.xi:
            rows.append(_row("lineup", "player", pid, "predicted_xi_slot", slot, now, r.match_id))
        for pid in mine.player_id[mine.player_id.isin(absent)]:
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
    return out + injury_risk_other(con, now)


def injury_risk_other(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    """Leagues without FPL: no current minutes, so the history-only model (age, position, injury record) pooled
    over the six leagues, for the leagues its validation scored (docs/backtests/injury.md). The record is the free
    plan's 2022-24 lists plus the absences API-Football lists each night. Tier = percentile among the league's
    current squad players. Published under the id the shortlists use (Reep, else transfermarkt:<id>)."""
    from dugout_pipeline.adapters.api_football import injury_file

    from dugout_models.injury import (
        BACKTEST_JSON,
        PL,
        _injury_rows,
        attach_tm_facts,
        build,
        episodes,
        history_features,
        history_model,
        injury_episodes,
        other_leagues,
    )
    from dugout_models.market import _tm_to_reep

    try:
        report = json.loads(BACKTEST_JSON.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("injury backtest report missing: other leagues not scored")
        return []
    scored = [lg for lg in other_leagues() if report.get("leagues", {}).get(lg, {}).get("scored")]
    if not scored or not any(injury_file(lg, 2024).is_file() for lg in scored):
        log.warning("injury lists for the other leagues missing (history job): not scored")
        return []
    names = [PL, *other_leagues()]
    frames = pd.concat([build(con, lg) for lg in names], ignore_index=True)
    model, cols = history_model(frames, names)
    squads = con.execute(
        """SELECT p.player_id, c.league, e.external_id AS tm_id FROM players p JOIN clubs c USING (club_id)
           JOIN entity_ids e ON e.entity_type = 'player' AND e.dugout_id = p.player_id
                AND e.provider = 'transfermarkt' AND e.namespace = 'spieler'
           WHERE c.league IN (SELECT unnest(?))""",
        [scored],
    ).df()
    tm_of = squads.drop_duplicates("player_id").set_index("player_id").tm_id
    live = con.execute(
        """SELECT player_id, CAST(snapshot_date AS DATE) AS date, reason, news AS type FROM injuries
           WHERE source = 'api_football' AND status = 'unavailable'"""
    ).df()
    live = live.assign(type="Missing Fixture", player_id=live.player_id.map(tm_of)).dropna(
        subset=["player_id"]
    )
    ep = injury_episodes(scored[0])  # every league's lists, keyed by Transfermarkt id
    if len(live):
        live = _injury_rows(live.assign(date=pd.to_datetime(live.date)))
        ep = pd.concat([ep, episodes(live[["player_id", "date", "reason"]])], ignore_index=True)
        ep = ep.sort_values(["player_id", "date"]).reset_index(drop=True)
    today = pd.Timestamp(now.date())
    p = squads.drop_duplicates("tm_id")[["league", "tm_id"]].rename(columns={"tm_id": "player_id"})
    p = p.assign(date=today)
    p = attach_tm_facts(history_features(p.reset_index(drop=True), ep, label=False)).dropna(subset=["age"])
    # e.g. a warehouse without the other leagues' squads yet: nothing to score, not a failed publish
    if p.empty:
        log.warning("injury: no squad players with Transfermarkt ids in %s: other leagues not scored", scored)
        return []
    p["age2"] = (p.age - 27) ** 2
    p = p.assign(**{f"lg_{lg}": (p.league == lg).astype(float) for lg in names})
    p["risk"] = model.predict_proba(p[cols])[:, 1]
    reep = _tm_to_reep(con)
    out = []
    for _, g in p.groupby("league"):
        pct = g.risk.rank(pct=True, method="max").to_numpy()
        for tm, q in zip(g.player_id, pct, strict=True):
            pid = reep.get(tm, f"transfermarkt:{tm}")
            tier = next(name for cut, name in TIERS if q < cut)
            out.append(_row("injury", "player", pid, "injury_risk_tier", tier, now, "record"))
            out.append(
                _row("injury", "player", pid, "injury_risk_percentile", round(float(q), 3), now, "record")
            )
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


# ---- playing style (API-Football season totals) -----------------------------------------------------------
def style(con: duckdb.DuckDBPyConnection, now: datetime) -> list[dict]:
    """Every player with a style profile (dugout_models.style), his newest season: role (when one clearly leads),
    the season and pool he is measured in, his percentile for each of his group's features and up to two similar
    players. Context = the season ("2024-25")."""
    from dugout_models.market import _tm_to_reep
    from dugout_models.style import GROUP_PLURAL, TIER_NAMES, StyleModel, season_label, tm_groups

    m = StyleModel.from_warehouse(con, tm_groups())
    if m.profiles.empty:
        return []
    reep = _tm_to_reep(con)
    rows: list[dict] = []
    newest = m.profiles.sort_values(["season", "minutes"]).drop_duplicates("tm_player_id", keep="last")
    for r in newest.itertuples(index=False):
        row = pd.Series(r._asdict())
        pid, ctx = reep.get(r.tm_player_id, f"transfermarkt:{r.tm_player_id}"), season_label(int(r.season))
        rows.append(_row("style", "player", pid, "style_group", r.group, now, ctx))
        rows.append(
            _row(
                "style",
                "player",
                pid,
                "style_pool",
                f"{GROUP_PLURAL[r.group]} in {TIER_NAMES[r.tier]}",
                now,
                ctx,
            )
        )
        rows.append(_row("style", "player", pid, "league_minutes", int(r.minutes), now, ctx))
        if isinstance(r.role, str):
            rows.append(_row("style", "player", pid, "style_role", r.role, now, ctx))
        for f, v in m.percentiles(row, r.group).items():
            rows.append(_row("style", "player", pid, f"pct:{f}", round(v, 2), now, ctx))
        for i, (_, name, cos) in enumerate(m.similar(row, 2), 1):
            if cos >= 0.8:
                rows.append(_row("style", "player", pid, f"similar_{i}", name, now, ctx))
    return rows


MODELS = {
    "predictor": predictions,
    "lineup": lambda con, now: lineups(con, now) + last_xi_lineups(con, now),
    "injury": injury_risk,
    "roles": roles,
    "style": style,
    "fingerprint": fingerprints,
}


def publish(
    con: duckdb.DuckDBPyConnection, models: list[str] | None = None, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    from dugout_models.market import market
    from dugout_models.rumours import publish as rumours

    # rumours first: it rewrites rumour_links, which the market's realism reads (press reports)
    registry = {**MODELS, "rumours": rumours, "market": market}
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
