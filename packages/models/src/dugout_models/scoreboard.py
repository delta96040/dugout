"""`data/scoreboard.json`: every prediction next to the real result.

Two kinds of rows, always labelled:
  live      the forecast as published before kick-off (prediction_log), from the day logging started
  backtest  walk-forward predictions for finished matches before logging began: the model refit every ~4 gameweeks on
            matches strictly before each block (docs/backtests/predictor.md), so no row saw its own result
Results carry their own provenance: the source that reported the score and whether a second source agrees.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from dugout_models.metrics import onehot, rps

LABELS = ("H", "D", "A")


def walk_forward(con: duckdb.DuckDBPyConnection, seasons: list[str]) -> pd.DataFrame:
    from dugout_models.backtest import FIRST_TRAIN_SEASON, build_frame
    from dugout_models.features import blocks
    from dugout_models.predictor import StackedPredictor

    m = build_frame(con)
    out = []
    for season in seasons:
        for t0, rows in blocks(m[m.season == season]):
            train = m[(m.date < t0) & (m.season >= FIRST_TRAIN_SEASON)]
            test = m.loc[rows]
            p = StackedPredictor().fit(train).predict(test)
            out.append(
                test[["match_id", "date", "home", "away", "home_goals", "away_goals"]].assign(
                    p_home=p[:, 0], p_draw=p[:, 1], p_away=p[:, 2], fitted_before=t0
                )
            )
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def result_provenance(con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    rows = con.execute(
        """SELECT match_id, source, home_goals, away_goals, fetched_at FROM match_observations
           WHERE home_goals IS NOT NULL"""
    ).fetchall()
    by: dict[str, list] = {}
    for mid, src, hg, ag, at in rows:
        by.setdefault(mid, []).append((src, hg, ag, at))
    out = {}
    for mid, obs in by.items():
        scores = {(hg, ag) for _, hg, ag, _ in obs}
        src, hg, ag, at = obs[0]
        out[mid] = {
            "sources": sorted({o[0] for o in obs}),
            "fetched_at": max(o[3] for o in obs).isoformat(),
            "status": "verified"
            if len(obs) > 1 and len(scores) == 1
            else ("disputed" if len(scores) > 1 else "single_source"),
        }
    return out


def build(con: duckdb.DuckDBPyConnection, seasons: list[str], out: Path) -> dict:
    names = dict(con.execute("SELECT club_id, name FROM clubs").fetchall())
    prov = result_provenance(con)
    wf = walk_forward(con, seasons)
    live = con.execute(
        """SELECT l.match_id, l.as_of, l.model, l.p_home, l.p_draw, l.p_away, m.home_goals, m.away_goals,
                  m.home_club_id, m.away_club_id, CAST(m.kickoff_utc AS DATE) AS date
           FROM prediction_log l JOIN matches m USING (match_id)
           WHERE m.status = 'finished' AND l.as_of < m.kickoff_utc AND m.league = 'premier_league'
           QUALIFY row_number() OVER (PARTITION BY l.match_id ORDER BY l.as_of DESC) = 1"""
    ).df()
    rows = []
    live_ids = set(live.match_id)
    for r in live.itertuples():
        rows.append(
            _row(
                r.match_id,
                r.date,
                r.home_club_id,
                r.away_club_id,
                (r.p_home, r.p_draw, r.p_away),
                r.home_goals,
                r.away_goals,
                "live",
                r.as_of.isoformat(),
                names,
                prov,
            )
        )
    for r in wf.itertuples():
        if r.match_id in live_ids:
            continue
        rows.append(
            _row(
                r.match_id,
                r.date,
                r.home,
                r.away,
                (r.p_home, r.p_draw, r.p_away),
                r.home_goals,
                r.away_goals,
                "backtest",
                pd.Timestamp(r.fitted_before).isoformat(),
                names,
                prov,
            )
        )
    rows.sort(key=lambda x: x["date"], reverse=True)
    p = np.array([x["probs"] for x in rows])
    y = np.array([x["outcome"] for x in rows])
    summary = {
        "matches": len(rows),
        "live": sum(x["kind"] == "live" for x in rows),
        "hit_rate": float(np.mean([x["hit"] for x in rows])) if rows else None,
        "rps": float(rps(p, onehot(y))) if rows else None,
    }
    doc = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": "Elo + xG Dixon–Coles stack (pre-lineup)",
        "seasons": seasons,
        "summary": summary,
        "rows": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return doc


def _row(mid, date, home, away, probs, hg, ag, kind, made_at, names, prov) -> dict:
    hg, ag = int(hg), int(ag)
    outcome = "H" if hg > ag else "D" if hg == ag else "A"
    probs = [round(float(x), 4) for x in probs]
    return {
        "match_id": mid,
        "date": str(pd.Timestamp(date).date()),
        "home": names.get(home, home),
        "away": names.get(away, away),
        "probs": probs,
        "pick": LABELS[int(np.argmax(probs))],
        "home_goals": hg,
        "away_goals": ag,
        "outcome": outcome,
        "hit": LABELS[int(np.argmax(probs))] == outcome,
        "kind": kind,
        "predicted_at": made_at,
        "result": prov.get(
            mid, {"sources": ["football-data.co.uk"], "fetched_at": None, "status": "single_source"}
        ),
    }
