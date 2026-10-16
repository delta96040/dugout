"""Player lookup outputs: "how good can he become?" and "how would he fit <club>?" for any player in the pool.

Pool: players valued in the last 200 days who play in a first-tier league (transfermarkt-datasets + today's squad
values), so a question can name almost any professional in a top division.

  potential (potential.md):  projected market value in 2 and 4 years (median), 10-90% band in 4 years
  level:                     where a value sits among current Premier League squad values (words, chosen by code)
  fit to a club:
    expected_minutes_share   playing-time outlook at the club (opportunity.md, backtested)
    rank_at_position         his value rank among the club's current options at his position (1 = most valuable)
    style_fit                0-100 similarity to the per-90 profile the club's head coach uses at that position
                             (Premier League players only; descriptive - it does not predict minutes, fingerprint.md)
    fills_need               whether his position is one of the club's top two needs (market.needs heuristic)
"""

from __future__ import annotations

import logging
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
from dugout_pipeline.settings import get_settings

from dugout_models import potential
from dugout_models.market import GROUPS, PL, SHORTLIST, STARTERS, Market
from dugout_models.opportunity import FEATURES as OPP_FEATURES
from dugout_models.publish import _row

log = logging.getLogger(__name__)
MIN_VALUE = 1_000_000
TRAIN_YEARS = {2: range(2012, 2025), 4: range(2010, 2023)}  # every target dated before today

# Percentile of current Premier League squad values -> words (shown with the number, never instead of it)
LEVELS = [
    (0.95, "among the most valuable players in the Premier League"),
    (0.80, "the level of a first-choice player at a top Premier League club"),
    (0.50, "the level of a regular Premier League starter"),
    (0.20, "the level of a Premier League squad player"),
    (0.0, "below the usual level of a Premier League squad"),
]


def level_words(value: float, pl_values: np.ndarray) -> str:
    q = float(np.searchsorted(np.sort(pl_values), value) / len(pl_values))
    return next(words for cut, words in LEVELS if q >= cut)


def showcase_clubs() -> list[str]:
    from dugout_pipeline.onboard import showcase_clubs as clubs

    return clubs(get_settings().config_dir)


def outlook_many(mk: Market, pool: pd.DataFrame, club: str, fees: pd.Series) -> pd.DataFrame:
    """Vectorised opportunity-model features and prediction for every pool player at `club`."""
    tm_club = str(mk.squads.loc[mk.squads.club_id == club, "tm_club_id"].iloc[0])
    sq = mk.squad(club)
    mins, main, games = mk._last_season()
    ls_year = max(y for c, y in mk.league_value.index if c == PL)
    pl_level = np.log1p(mk.league_value.get((PL, ls_year), np.nan))
    group = pool.player_id.map(mk.players.sub_position).map(
        {s: g for g, subs in GROUPS.items() for s in subs}
    )
    record = mk.capacity.get(tm_club, np.nan)
    rows = []
    for pid, g, mv, age, comp, fee in zip(
        pool.player_id, group, pool.mv, pool.age, pool.comp, fees.reindex(pool.index), strict=True
    ):
        inc = sq.loc[(sq.group == g) & (sq.tm_player_id != pid), "market_value_eur"].astype(float)
        club_games = games.get(main.get(pid), np.nan)
        rows.append(
            {
                "prev_share": mins.get(pid, 0) / (90 * club_games) if club_games == club_games else np.nan,
                "log_mv": np.log1p(mv),
                "age": age,
                "age2": (age - 26) ** 2,
                "league_gap": pl_level - np.log1p(mk.league_value.get((comp, ls_year), np.nan)),
                "rank_at_pos": 1 + int((inc > mv).sum()),
                "value_vs_best": np.log1p(mv) - np.log1p(inc.max()) if len(inc) else 0.0,
                "incumbents": len(inc),
                "fee_vs_record": np.log1p(fee if fee == fee else mv) - np.log1p(record)
                if record == record
                else 0.0,
                "group": g,
            }
        )
    X = pd.DataFrame(rows, index=pool.index)
    ok = X[OPP_FEATURES].notna().all(axis=1)
    X["share"] = np.nan
    X.loc[ok, "share"] = mk.opp.predict(X.loc[ok, OPP_FEATURES])
    return X


def style_fit(con: duckdb.DuckDBPyConnection, club: str, season: str = "2025-26") -> pd.Series:
    """0-100 style similarity of Premier League players' per-90 profiles to the club's current head coach."""
    from sklearn.preprocessing import StandardScaler

    from dugout_models.fingerprint import fit_scores, required_profiles
    from dugout_models.roles import BASE, season_profiles

    row = con.execute(
        "SELECT manager_id FROM managers WHERE club_id = ? AND source = 'wikidata'", [club]
    ).fetchone()
    if not row:
        return pd.Series(dtype=float)
    prof = season_profiles(con)
    prof = prof[prof.season == season].dropna(subset=BASE).reset_index(drop=True)
    scaler = StandardScaler().fit(prof[BASE])
    scores = fit_scores(prof, required_profiles(con), row[0], scaler)
    return pd.Series(scores.to_numpy(), index=prof.player_id)


def lookup(con: duckdb.DuckDBPyConnection, mk: Market, now: datetime, reep: dict[str, str]) -> list[dict]:
    t, top = potential.prepare(mk.t)
    pool = potential.snapshot(t, pd.Timestamp(now.date()), top)
    pool = pool.dropna(subset=["age"])
    pool = pool[((pool.top_tier == 1) | (pool.comp == PL)) & (pool.mv >= MIN_VALUE)].reset_index(drop=True)
    log.info("lookup pool: %d players", len(pool))
    proj = {}
    for h, years in TRAIN_YEARS.items():
        train = mk.frame(
            f"potential_h{h}",
            lambda years=years, h=h: potential.frames(t, years, top, h),
            years=[years.start, years.stop],
        ).dropna(subset=["target", "age"])
        proj[h] = potential.PotentialModel().fit(train).predict(pool)
    pl_values = mk.squads.market_value_eur.dropna().astype(float).to_numpy()
    names = mk.players.name
    club_names = mk.clubs.set_index("club_id").name  # club of the latest valuation (today's for PL squads)
    rows: list[dict] = []
    ids = [reep.get(p, f"transfermarkt:{p}") for p in pool.player_id]
    for i, (pid, r) in enumerate(zip(ids, pool.itertuples(), strict=True)):
        rows += [
            _row("lookup", "player", pid, "name", names.get(r.player_id), now),
            _row("lookup", "player", pid, "club_name", club_names.get(str(r.club_id)), now),
            _row("lookup", "player", pid, "position", mk.players.sub_position.get(r.player_id), now),
            _row("lookup", "player", pid, "age", round(r.age, 1), now),
            _row("lookup", "player", pid, "market_value", r.mv, now),
            _row("lookup", "player", pid, "level_now", level_words(r.mv, pl_values), now),
        ]
        for h in (2, 4):
            q50 = float(np.expm1(proj[h].q50.iloc[i]))
            rows.append(_row("potential", "player", pid, f"proj_value_{h}y", round(q50, -5), now))
        q10, q90 = float(np.expm1(proj[4].q10.iloc[i])), float(np.expm1(proj[4].q90.iloc[i]))
        rows += [
            _row("potential", "player", pid, "floor_value_4y", round(q10, -5), now),
            _row("potential", "player", pid, "ceiling_value_4y", round(q90, -5), now),
            _row(
                "potential",
                "player",
                pid,
                "level_4y",
                level_words(float(np.expm1(proj[4].q50.iloc[i])), pl_values),
                now,
            ),
            _row("potential", "player", pid, "level_ceiling", level_words(q90, pl_values), now),
        ]
    rows += gems(mk, pool, proj, ids, now)
    for club in showcase_clubs():
        fees = mk.fair_fee(pool.player_id.tolist(), pool.comp.tolist()).fair_fee
        X = outlook_many(mk, pool, club, fees)
        needs = set(mk.needs(club).head(2).group)
        sfit = style_fit(con, club)
        for pid, x in zip(ids, X.itertuples(), strict=True):
            if x.share == x.share:
                rows.append(
                    _row("fit", "player", pid, "expected_minutes_share", round(x.share, 3), now, club)
                )
            rows += [
                _row("fit", "player", pid, "rank_at_position", x.rank_at_pos, now, club),
                _row("fit", "player", pid, "position_options", x.incumbents, now, club),
            ]
            if isinstance(x.group, str):
                rows += [
                    _row("fit", "player", pid, "position_group", x.group, now, club),
                    _row("fit", "player", pid, "fills_need", "yes" if x.group in needs else "no", now, club),
                ]
            if pid in sfit.index and sfit[pid] == sfit[pid]:
                rows.append(_row("fit", "player", pid, "style_fit", round(float(sfit[pid]), 1), now, club))
    return rows


GEM_AGES = (17, 23)


def gems(mk: Market, pool: pd.DataFrame, proj: dict, ids: list[str], now: datetime) -> list[dict]:
    """Hidden gems per club need: players aged 17-23 who play regularly (30%+ of minutes) in a top league, cost
    less today than the club's last regular starter at the position, ranked by the potential model's projected
    value in four years (potential.md). Current clubs come from the squad file where known; players who joined a
    club in the last 12 months are left out, as on the other lists."""
    g = pool.assign(pid=ids, proj4=np.expm1(proj[4].q50.to_numpy()))
    g = g[g.age.between(GEM_AGES[0], GEM_AGES[1] + 0.999) & (g.minutes_share >= 0.3) & (g.top_tier == 1)]
    now_sq = mk.squads.drop_duplicates("tm_player_id").set_index("tm_player_id")
    joined = pd.to_datetime(g.player_id.map(now_sq.joined), errors="coerce")
    g = g[~(joined > mk.now - pd.Timedelta(days=365))].copy()
    g["club_now"] = g.player_id.map(now_sq.tm_club_id).fillna(g.club_id).astype(str)
    g["sub"] = g.player_id.map(mk.players.sub_position)
    club_names = mk.clubs.set_index("club_id").name
    rows: list[dict] = []
    for slug in sorted(mk.squads.club_id.unique()):
        tm_club = str(mk.squads.loc[mk.squads.club_id == slug, "tm_club_id"].iloc[0])
        sq = mk.squad(slug)
        for i, n in mk.needs(slug).head(2).iterrows():
            inc = sq.loc[sq.group == n.group, "market_value_eur"].astype(float).sort_values(ascending=False)
            bar = inc.iloc[STARTERS[n.group] - 1] if len(inc) >= STARTERS[n.group] else np.inf
            c = g[g["sub"].isin(GROUPS[n.group]) & (g.club_now != tm_club) & (g.mv < bar)]
            for k, r in enumerate(c.nlargest(SHORTLIST, "proj4").itertuples(), 1):
                ctx = f"{slug}:need:{i + 1}:gem:{k}"
                rows += [
                    _row(
                        "scout", "player", r.pid, "candidate_name", mk.players.name.get(r.player_id), now, ctx
                    ),
                    _row("scout", "player", r.pid, "candidate_club", club_names.get(r.club_now), now, ctx),
                    _row("scout", "player", r.pid, "candidate_position", n.group, now, ctx),
                    _row("scout", "player", r.pid, "age", round(r.age, 1), now, ctx),
                    _row("scout", "player", r.pid, "market_value", r.mv, now, ctx),
                    _row("scout", "player", r.pid, "proj_value_4y", round(r.proj4, -5), now, ctx),
                ]
    return rows
