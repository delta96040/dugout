"""Walk-forward backtest of the match predictor.

For each test season, matches are split into chronological blocks of ~4 gameweeks. Before each block every
model is refit on matches strictly before the block's first kick-off, then predicts the block. Nothing from
the block (or later) is visible at fit time. Compared on RPS, log loss and Brier against:
  - Elo baseline (our Elo -> multinomial logistic), and
  - bookmaker closing odds (Pinnacle where available, margin removed) — the hardest benchmark.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dugout_models import data
from dugout_models.dixon_coles import fit_bayes
from dugout_models.elo import EloOutcomeModel, pre_match_elo
from dugout_models.features import (
    LINEUP,
    PRE_LINEUP,
    blocks,
    dixon_coles_walkforward,
    missing_impact,
    promoted_priors,
    rolling_form,
)
from dugout_models.metrics import implied_probs, score
from dugout_models.predictor import BoostedStack, StackedPredictor, TreePredictor

FIRST_TRAIN_SEASON = "2016-17"  # 2015-16 is burn-in for Elo / Dixon–Coles
# The model we ship, chosen on validation seasons 2022-23 and 2023-24 (see docs/backtests/predictor_validation.md).
PRIMARY = "stack_pre_lineup"


def build_frame(con, league: str = "premier_league") -> pd.DataFrame:
    m = data.matches(con, league)
    m = pre_match_elo(m)
    m = rolling_form(m)
    # player minutes come from FPL: other leagues have no missing-regulars feature (NaN), only the pre-lineup stack
    m = missing_impact(m, data.player_minutes(con) if league == "premier_league" else pd.DataFrame())
    return dixon_coles_walkforward(m, FIRST_TRAIN_SEASON)


def run(
    con,
    seasons: list[str],
    bayes: bool = True,
    out_dir: Path | None = None,
    name: str = "predictor",
    league: str = "premier_league",
) -> dict:
    t_start = time.time()
    m = build_frame(con, league)
    preds: dict[str, list[np.ndarray]] = {}
    seats: dict[str, dict] = {}
    rows_seen: list[int] = []
    for season in seasons:
        for t0, rows in blocks(m[m.season == season]):
            train = m[(m.date < t0) & (m.season >= FIRST_TRAIN_SEASON)]
            test = m.loc[rows]
            elo = EloOutcomeModel().fit(train.elo_diff, train.result.to_numpy())
            out = {
                "elo": elo.predict(test.elo_diff),
                "dixon_coles_map": test[["dc_ph", "dc_pd", "dc_pa"]].to_numpy(),
                "dixon_coles_xg": test[["dcx_ph", "dcx_pd", "dcx_pa"]].to_numpy(),
                "stack_pre_lineup": StackedPredictor().fit(train).predict(test),
                "stack_all_features": StackedPredictor(
                    columns=["elo", "dc_ha", "dc_da", "dcx_ha", "dcx_da", "xgf", "xga", "pts", "rest"]
                )
                .fit(train)
                .predict(test),
                "stack_lineup": StackedPredictor(lineup=True).fit(train).predict(test),
                "boosted_pre_lineup": BoostedStack(PRE_LINEUP).fit(train).predict(test),
                "trees_pre_lineup": TreePredictor(PRE_LINEUP).fit(train).predict(test),
            }
            if bayes:
                hist = m[(m.date < t0) & (m.date >= t0 - pd.Timedelta(days=3 * 365))]
                if season not in seats:
                    seats[season] = promoted_priors(
                        m.assign(home_t=m.home_goals, away_t=m.away_goals),
                        season,
                        hist.assign(home_t=hist.home_goals, away_t=hist.away_goals),
                        t0,
                    )
                params, _ = fit_bayes(hist, t0, prior_mean=seats[season]["goals"])
                out["dixon_coles_bayes"] = np.array([params.probs(r.home, r.away) for r in test.itertuples()])
            for k, v in out.items():
                preds.setdefault(k, []).append(v)
            rows_seen.extend(rows)

    test = m.loc[rows_seen]
    results: dict[str, dict] = {}
    for season in seasons:
        mask = (test.season == season).to_numpy()
        res = test.result.to_numpy()[mask]
        season_scores = {}
        for model_name, parts in preds.items():
            p = np.vstack(parts)
            if len(p) == len(test):
                season_scores[model_name] = score(p[mask], res)
        has_odds = test[["odds_home", "odds_draw", "odds_away"]].notna().all(axis=1).to_numpy() & mask
        if has_odds.any():
            book = implied_probs(test.odds_home[has_odds], test.odds_draw[has_odds], test.odds_away[has_odds])
            season_scores["bookmaker_closing"] = score(book, test.result.to_numpy()[has_odds])
        results[season] = season_scores

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seasons": seasons,
        "protocol": "walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block",
        "training_from": FIRST_TRAIN_SEASON,
        "features_pre_lineup": PRE_LINEUP,
        "features_lineup": LINEUP,
        "results": results,
        "primary_model": PRIMARY,
        "beats_elo": {
            s: {k: r[PRIMARY][k] < r["elo"][k] for k in ("rps", "log_loss", "brier")}
            for s, r in results.items()
        },
        "runtime_seconds": round(time.time() - t_start),
    }
    if out_dir:
        write_report(report, out_dir, name)
    return report


def write_report(report: dict, out_dir: Path, name: str = "predictor") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(report, indent=2))
    names = {
        "elo": "Elo baseline",
        "dixon_coles_bayes": "Dixon–Coles, goals (Bayesian, NUTS)",
        "dixon_coles_map": "Dixon–Coles, goals (posterior mode)",
        "dixon_coles_xg": "Dixon–Coles, goals/xG blend (posterior mode)",
        "stack_pre_lineup": "Stack: Elo + xG Dixon–Coles (pre-lineup)",
        "stack_lineup": "Stack + missing regulars (lineup-informed)",
        "stack_all_features": "Stack with all features",
        "boosted_pre_lineup": "Boosted stack (pre-lineup)",
        "trees_pre_lineup": "XGBoost on raw features",
        "bookmaker_closing": "Bookmaker closing odds",
    }
    names[report["primary_model"]] = f"**{names[report['primary_model']]} — dugout predictor**"
    lines = [
        "# Match predictor backtest",
        "",
        f"Generated {report['generated_at']}. Protocol: {report['protocol']}. Training data from "
        f"{report['training_from']} (football-data.co.uk results; Opta xG via FPL from 2022-23).",
        "",
        "Lower is better. RPS = ranked probability score.",
        "",
    ]
    for season, res in report["results"].items():
        lines += [f"## {season}", "", "| Model | RPS | Log loss | Brier | Matches |", "|---|---|---|---|---|"]
        for key in (
            "bookmaker_closing",
            "stack_lineup",
            "stack_pre_lineup",
            "stack_all_features",
            "boosted_pre_lineup",
            "trees_pre_lineup",
            "dixon_coles_xg",
            "dixon_coles_bayes",
            "dixon_coles_map",
            "elo",
        ):
            if key in res:
                r = res[key]
                lines.append(
                    f"| {names[key]} | {r['rps']:.4f} | {r['log_loss']:.4f} | {r['brier']:.4f} | {r['n']} |"
                )
        beats = report["beats_elo"][season]
        lines += [
            "",
            "Beats Elo on: " + ", ".join(k for k, v in beats.items() if v)
            if any(beats.values())
            else "Does not beat Elo.",
            "",
        ]
    lines += [
        "## Notes",
        "",
        "- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share "
        "of each club's regular starters who did not start (known ~1 h before kick-off).",
        "- Rest days count Premier League matches only (cup and European fixtures are not in the free data).",
        "- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; "
        "they are shown as the ceiling.",
    ]
    (out_dir / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
