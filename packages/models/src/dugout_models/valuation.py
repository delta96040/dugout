"""Transfer valuation: gradient-boosted regression of the fee's premium over market value, log(fee / market value),
on historical transfers (transfermarkt-datasets, CC0). Fair fee = today's market value x the predicted premium, so the
estimate moves with the market (a tree model of log(fee) on the transfer year cannot extrapolate past its last
training year, and underpriced Premier League deals by a third).
Features known at the time of each transfer:
  age; position; market value at the time ("quality" as the market saw it); minutes, goals and assists in the
  previous 365 days; league strength of the selling and buying clubs (median player value in that league
  that year); squad value of the selling and buying clubs; whether the buyer is a Premier League club and whether
  the move is between two Premier League clubs. Nationality was tested and adds nothing (valuation.md).
Contract months remaining is *not* available historically (the dataset only has current contract expiry), so it
cannot be learnt here; Transfer Realism uses it from current data with a documented prior weight instead.

Backtest: train on transfers before 2024-07-01, test on 2024-07-01 .. 2026-07-06; baseline = the market value.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import lightgbm as lgb
import numpy as np
import pandas as pd
from dugout_pipeline.settings import REPO_ROOT

TM_DIR = REPO_ROOT / "data" / "raw" / "transfermarkt"
SPLIT = pd.Timestamp("2024-07-01")
FEATURES = [
    "age",
    "log_mv",
    "minutes_365",
    "ga_p90_365",
    "apps_365",
    "log_from_league",
    "log_to_league",
    "same_league",
    "pos_Attack",
    "pos_Midfield",
    "pos_Defender",
    "pos_Goalkeeper",
    "to_pl",
    "pl_to_pl",
    "log_from_cv",
    "log_to_cv",
]
# the previous model (log fee on the transfer year), kept for the backtest comparison
FEATURES_V1 = [*FEATURES[:8], "year", *FEATURES[8:12]]
PL = "GB1"


def load(tm_dir: Path = TM_DIR) -> dict[str, pd.DataFrame]:
    return {
        n: pd.read_parquet(tm_dir / f"{n}.parquet")
        for n in ("players", "transfers", "player_valuations", "appearances", "clubs")
    }


def league_strength(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Median player market value per (domestic competition, year)."""
    v = t["player_valuations"].copy()
    v["year"] = pd.to_datetime(v["date"]).dt.year
    return (
        v.groupby(["player_club_domestic_competition_id", "year"])["market_value_in_eur"]
        .median()
        .rename("league_value")
        .reset_index()
        .rename(columns={"player_club_domestic_competition_id": "comp"})
    )


def build(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    tr = t["transfers"].copy()
    tr["date"] = pd.to_datetime(tr["transfer_date"])
    tr = tr[pd.to_numeric(tr["transfer_fee"], errors="coerce") > 0].copy()
    tr["fee"] = tr["transfer_fee"].astype(float)
    p = t["players"][["player_id", "date_of_birth", "position"]]
    tr = tr.merge(p, on="player_id", how="left")
    tr["age"] = (tr.date - pd.to_datetime(tr.date_of_birth)).dt.days / 365.25

    # market value at transfer: the transfer row's own value, else latest valuation before the date
    vals = t["player_valuations"][["player_id", "date", "market_value_in_eur"]].copy()
    vals["date"] = pd.to_datetime(vals["date"])
    tr = tr.sort_values("date")
    asof = pd.merge_asof(
        tr[["player_id", "date"]].reset_index(),
        vals.sort_values("date"),
        on="date",
        by="player_id",
        direction="backward",
    ).set_index("index")["market_value_in_eur"]
    tr["mv"] = pd.to_numeric(tr.get("market_value_in_eur"), errors="coerce").fillna(asof)
    tr["log_mv"] = np.log1p(tr.mv)

    # form in the previous 365 days
    ap = t["appearances"][["player_id", "date", "minutes_played", "goals", "assists"]].copy()
    ap["date"] = pd.to_datetime(ap["date"])
    form = _window_sums(tr[["player_id", "date"]], ap)
    tr = tr.join(form)
    tr["ga_p90_365"] = (tr.goals_365 + tr.assists_365) / tr.minutes_365.clip(lower=90) * 90

    clubs = t["clubs"][["club_id", "domestic_competition_id"]]
    ls = league_strength(t)
    tr["year"] = tr.date.dt.year
    for side in ("from", "to"):
        tr = tr.merge(
            clubs.rename(columns={"club_id": f"{side}_club_id", "domestic_competition_id": f"{side}_comp"}),
            on=f"{side}_club_id",
            how="left",
        )
        tr = tr.merge(
            ls.rename(columns={"comp": f"{side}_comp", "league_value": f"{side}_league"}),
            on=[f"{side}_comp", "year"],
            how="left",
        )
        tr[f"log_{side}_league"] = np.log1p(tr[f"{side}_league"])
    tr["same_league"] = (tr.from_comp == tr.to_comp).astype(float)
    tr["to_pl"] = (tr.to_comp == PL).astype(float)
    tr["pl_to_pl"] = ((tr.to_comp == PL) & (tr.from_comp == PL)).astype(float)
    cv = club_values(t)
    tr["season"] = tr.date.dt.year - (tr.date.dt.month < 6)
    for side in ("from", "to"):
        key = pd.MultiIndex.from_arrays([tr[f"{side}_club_id"].astype(str), tr.season])
        tr[f"log_{side}_cv"] = np.log1p(cv.reindex(key).fillna(0).to_numpy())
    for pos in ("Attack", "Midfield", "Defender", "Goalkeeper"):
        tr[f"pos_{pos}"] = (tr.position == pos).astype(float)
    tr["log_fee"] = np.log(tr.fee)
    return tr.dropna(subset=["age", "log_mv"]).reset_index(drop=True)


def club_values(t: dict[str, pd.DataFrame]) -> pd.Series:
    """Squad value by (club id, season starting that June): the sum of each player's latest valuation in the year
    before 1 June."""
    v = t["player_valuations"][["player_id", "date", "market_value_in_eur", "current_club_id"]].copy()
    v["date"] = pd.to_datetime(v.date)
    out = []
    for y in range(int(v.date.dt.year.min()) + 1, int(v.date.dt.year.max()) + 2):
        w = v[(v.date < f"{y}-06-01") & (v.date >= f"{y - 1}-06-01")].sort_values("date")
        s = w.groupby("player_id").tail(1).groupby("current_club_id").market_value_in_eur.sum()
        out.append(
            pd.Series(s.to_numpy(), index=pd.MultiIndex.from_arrays([s.index.astype(str), [y] * len(s)]))
        )
    return pd.concat(out)


def _window_sums(keys: pd.DataFrame, ap: pd.DataFrame, days: int = 365) -> pd.DataFrame:
    """Sum of minutes/goals/assists/appearances in (date - days, date) per key row, via cumulative sums."""
    ap = ap.sort_values(["player_id", "date"])
    out = pd.DataFrame(
        index=keys.index, columns=["minutes_365", "goals_365", "assists_365", "apps_365"], dtype=float
    )
    groups = dict(tuple(ap.groupby("player_id")))
    for pid, rows in keys.groupby("player_id"):
        g = groups.get(pid)
        if g is None:
            out.loc[rows.index] = 0.0
            continue
        d = g.date.to_numpy()
        cs = np.vstack(
            [
                np.zeros(4),
                np.cumsum(g[["minutes_played", "goals", "assists"]].assign(n=1).to_numpy(float), axis=0),
            ]
        )
        hi = np.searchsorted(d, rows.date.to_numpy(), side="left")
        lo = np.searchsorted(d, (rows.date - pd.Timedelta(days=days)).to_numpy(), side="left")
        out.loc[rows.index] = cs[hi] - cs[lo]
    return out


class ValuationModel:
    params: ClassVar[dict] = dict(
        n_estimators=600,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=30,
        subsample=0.8,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        verbose=-1,
    )

    def fit(self, df: pd.DataFrame) -> ValuationModel:
        df = df[df.mv > 0]
        premium = df.log_fee - np.log(df.mv)
        self.model = lgb.LGBMRegressor(**self.params).fit(df[FEATURES], premium)
        # residual spread for a fair-price range (10th-90th percentile)
        res = premium - self.model.predict(df[FEATURES])
        self.q10, self.q90 = float(np.quantile(res, 0.1)), float(np.quantile(res, 0.9))
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """df needs FEATURES and `mv` (market value today, > 0)."""
        mu = np.log(df.mv.to_numpy(float)) + self.model.predict(df[FEATURES])
        return pd.DataFrame(
            {"fair_fee": np.exp(mu), "low": np.exp(mu + self.q10), "high": np.exp(mu + self.q90)},
            index=df.index,
        )


def _metrics(y_log: np.ndarray, pred_log: np.ndarray) -> dict:
    err = pred_log - y_log
    ape = np.abs(np.exp(pred_log) - np.exp(y_log)) / np.exp(y_log)
    return {
        "mae_log": float(np.mean(np.abs(err))),
        "median_ape": float(np.median(ape)),
        "within_25pct": float(np.mean(ape <= 0.25)),
        "n": len(y_log),
    }


def backtest(out_dir: Path | None = None) -> dict:
    df = build(load())
    today = pd.Timestamp(datetime.now(UTC).date())  # some rows are pre-agreed future moves; exclude them
    df = df[df.mv > 0]
    train, test = df[df.date < SPLIT], df[(df.date >= SPLIT) & (df.date <= today)]
    model = ValuationModel().fit(train)
    pred = np.log(model.predict(test).fair_fee.to_numpy())
    has_mv = test.mv.to_numpy() > 0
    prev = lgb.LGBMRegressor(**ValuationModel.params).fit(train[FEATURES_V1], train.log_fee)
    pred_prev = prev.predict(test[FEATURES_V1])
    y = test.log_fee.to_numpy()
    segments = {
        "all": np.ones(len(test), bool),
        "Premier League buyer": (test.to_pl == 1).to_numpy(),
        "Premier League to Premier League": (test.pl_to_pl == 1).to_numpy(),
        "Premier League buyer, age 24 or under": ((test.to_pl == 1) & (test.age <= 24)).to_numpy(),
    }
    # every other configured league as a buyer: the fair fee on those clubs' dossiers is "if sold within the league"
    from dugout_pipeline.clubs import league_config, leagues

    for lg in leagues():
        cfg = league_config(lg)
        comp = cfg["provider_ids"].get("transfermarkt")
        if comp and comp != PL:
            segments[f"{cfg['name']} buyer"] = (test.to_comp == comp).to_numpy()
            segments[f"{cfg['name']} to {cfg['name']}"] = (
                (test.to_comp == comp) & (test.from_comp == comp)
            ).to_numpy()
    seg = {
        name: {
            "n": int(m.sum()),
            "model": _metrics(y[m], pred[m]),
            "previous_model": _metrics(y[m], pred_prev[m]),
            "market_value": _metrics(y[m], np.log(test.mv.to_numpy()[m])),
            "median_fee_over_estimate": float(np.exp(np.median(y[m] - pred[m]))),
            "median_fee_over_previous": float(np.exp(np.median(y[m] - pred_prev[m]))),
        }
        for name, m in segments.items()
    }
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "train": {"n": len(train), "from": str(train.date.min().date()), "to": str(train.date.max().date())},
        "test": {"from": str(test.date.min().date()), "to": str(test.date.max().date())},
        "model": _metrics(test.log_fee.to_numpy(), pred),
        "baseline_market_value": _metrics(
            test.log_fee.to_numpy()[has_mv], np.log(test.mv.to_numpy()[has_mv])
        ),
        "model_on_baseline_rows": _metrics(test.log_fee.to_numpy()[has_mv], pred[has_mv]),
        "segments": seg,
        "importance": dict(
            sorted(
                zip(FEATURES, model.model.feature_importances_.tolist(), strict=True), key=lambda kv: -kv[1]
            )
        ),
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "valuation.json").write_text(json.dumps(report, indent=2))
        m, b = report["model_on_baseline_rows"], report["baseline_market_value"]
        lines = [
            "# Transfer valuation backtest",
            "",
            f"Generated {report['generated_at']}. Data: transfermarkt-datasets "
            "(CC0), transfers with a disclosed fee.",
            "",
            f"Train: {report['train']['n']} transfers {report['train']['from']} .. {report['train']['to']}. "
            f"Test: {report['test']['from']} .. {report['test']['to']}.",
            "",
            "| Estimate | MAE (log fee) | Median abs % error | Within ±25% | Transfers |",
            "|---|---|---|---|---|",
            f"| **dugout valuation** | {m['mae_log']:.3f} | {m['median_ape']:.1%} | {m['within_25pct']:.1%} | {m['n']} |",
            f"| Market value at the time | {b['mae_log']:.3f} | {b['median_ape']:.1%} | {b['within_25pct']:.1%} | {b['n']} |",
            "",
            "By segment (MAE of log fee; fee / estimate is the median ratio, 1.00 = unbiased):",
            "",
            "| Segment | Transfers | dugout | previous model | market value | fee / estimate (previous) |",
            "|---|---|---|---|---|---|",
            *[
                f"| {k} | {v['n']} | {v['model']['mae_log']:.3f} | {v['previous_model']['mae_log']:.3f} | "
                f"{v['market_value']['mae_log']:.3f} | {v['median_fee_over_estimate']:.2f} "
                f"({v['median_fee_over_previous']:.2f}) |"
                for k, v in report["segments"].items()
            ],
            "",
            "The previous model regressed log(fee) on the transfer year; trees cannot extrapolate a year they have not "
            "seen, so it underpriced recent Premier League deals. Nationality (an English or homegrown premium) was "
            "tested as a feature and added nothing once club context is known; it is not used.",
            "",
            "Feature importance (split count): "
            + ", ".join(f"{k} {v}" for k, v in report["importance"].items()),
            "",
            "Limitation: contract months remaining is not available historically, so it is not a feature.",
            "",
            "Other leagues' buyers (the fair fee on those clubs' dossiers): read MAE against the market-value column and "
            "the median ratio; a ratio below 1.00 means fees came in under the estimate (it runs high there).",
        ]
        (out_dir / "valuation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
