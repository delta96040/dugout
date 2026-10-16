"""`dugout-models` command line: backtests and model fitting."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dugout_pipeline.db import connect
from dugout_pipeline.settings import REPO_ROOT, get_settings

BACKTESTS = REPO_ROOT / "docs" / "backtests"


def _run_one(model: str, args, con) -> bool:
    """Runs one backtest, writes docs/backtests/<model>.{md,json}, returns whether its success criterion holds."""
    if model == "predictor":
        from dugout_models.backtest import run

        r = run(con, args.seasons, bayes=not args.no_bayes, out_dir=BACKTESTS, name=args.name)
        print("predictor beats Elo:", r["beats_elo"])
        return all(all(v.values()) for v in r["beats_elo"].values())
    if model == "ratings":
        from dugout_models.ratings import rate_players, write_report

        ratings, rep = rate_players()
        write_report(ratings, rep, BACKTESTS)
        print("VAEP holdout:", rep["vaep_holdout"])
        return rep["vaep_holdout"]["scores"]["brier"] < rep["vaep_holdout"]["scores"]["brier_baseline"]
    if model == "roles":
        from dugout_models.roles import backtest

        r = backtest(con, BACKTESTS)
        last = list(r["self_retrieval"].values())[-1]
        print("roles:", r["self_retrieval"], "stability", r["role_stability_ari"])
        return last["top1"] > r["random_baseline"]["top1"]
    if model == "fingerprint":
        from dugout_models.fingerprint import backtest

        r = backtest(con, BACKTESTS)
        print("fingerprint:", r)
        return True  # descriptive; the fit-vs-minutes correlation is reported, not gated
    if model == "valuation":
        from dugout_models.valuation import backtest

        r = backtest(BACKTESTS)
        print("valuation:", r["model_on_baseline_rows"], "baseline:", r["baseline_market_value"])
        return r["model_on_baseline_rows"]["mae_log"] < r["baseline_market_value"]["mae_log"]
    if model == "realism":
        from dugout_models.realism import backtest

        r = backtest(BACKTESTS)
        print("realism:", {k: r[k]["auc"] for k in ("mobility", "destination")})
        return r["destination"]["top1"] > r["destination"]["random_top1"]
    if model == "injury":
        from dugout_models.injury import backtest

        r = backtest(con, BACKTESTS)
        print("injury:", r["results"])
        return r["results"]["logistic"]["auc"] > 0.5
    if model == "lineup":
        from dugout_models.lineup import PREDICTED_XI_TRUST, run_backtest

        r = run_backtest(con, out_dir=BACKTESTS, trust_weights=(0.0, PREDICTED_XI_TRUST))
        print("lineup:", r)
        return r["optimiser_overlap"][str(PREDICTED_XI_TRUST)] > r["recent_minutes_baseline_overlap"]
    if model == "opportunity":
        from dugout_models.opportunity import backtest

        r = backtest(BACKTESTS)
        print("opportunity:", r["results"])
        res = r["results"]
        return res["opportunity_model"]["r2"] > res["baseline_prev_share_value"]["r2"]
    if model == "potential":
        from dugout_models.potential import backtest

        r = backtest(BACKTESTS)
        print(
            "potential:",
            {h: (v["model"]["mae_log"], v["age_curve"]["mae_log"]) for h, v in r["horizons"].items()},
        )
        return all(v["model"]["mae_log"] < v["age_curve"]["mae_log"] for v in r["horizons"].values())
    if model == "rumours":
        from dugout_models.rumours import SHIPPED_HORIZON, backtest

        r = backtest(con, BACKTESTS)
        h = r["horizons"][str(SHIPPED_HORIZON)]
        print("rumours:", {k: h[k]["brier"] for k in ("recent_rate", "outlet_only", "model")})
        return h["model"]["brier"] < h["recent_rate"]["brier"]
    raise SystemExit(f"unknown model {model}")


MODELS = [
    "predictor",
    "ratings",
    "roles",
    "fingerprint",
    "opportunity",
    "valuation",
    "realism",
    "injury",
    "lineup",
    "potential",
    "rumours",
]


def cmd_backtest(args) -> int:
    con = connect(args.db or get_settings().warehouse_path, read_only=True)
    models = MODELS if args.model == "all" else [args.model]
    ok = {m: _run_one(m, args, con) for m in models}
    print("criteria met:", ok)
    return 0 if all(ok.values()) else 1


def cmd_publish(args) -> int:
    from dugout_models.publish import load_prediction_log, publish, save_prediction_log

    con = connect(args.db or get_settings().warehouse_path)  # writable: fills model_outputs
    log_path = REPO_ROOT / "data" / "prediction_log.csv"
    load_prediction_log(
        con, log_path
    )  # the committed log is the record; the warehouse may be a fresh CI cache
    counts = publish(con, args.models or None)
    print("published:", counts, "prediction log rows:", save_prediction_log(con, log_path))
    return 0


def cmd_scoreboard(args) -> int:
    from dugout_models.scoreboard import build

    con = connect(args.db or get_settings().warehouse_path)
    doc = build(con, args.seasons, REPO_ROOT / "data" / "scoreboard.json")
    print("scoreboard:", doc["summary"])
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":
        # PyTensor's C backend needs a modern g++; NUTS runs through nutpie/numba instead, so skip C compilation.
        os.environ.setdefault("PYTENSOR_FLAGS", "cxx=")
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"))
    logging.getLogger("pymc").setLevel(logging.ERROR)
    p = argparse.ArgumentParser(prog="dugout-models")
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("backtest")
    s.add_argument("model", choices=[*MODELS, "all"])
    s.add_argument("--seasons", nargs="+", default=["2024-25", "2025-26"])
    s.add_argument("--no-bayes", action="store_true", help="skip the (slow) NUTS Dixon–Coles baseline")
    s.add_argument("--name", default="predictor", help="report file name in docs/backtests/")
    s.set_defaults(fn=cmd_backtest)
    s = sub.add_parser(
        "publish", help="fit shipped models on all data; write current outputs to model_outputs"
    )
    s.add_argument(
        "models", nargs="*", help="predictor lineup injury roles fingerprint market rumours (default: all)"
    )
    s.set_defaults(fn=cmd_publish)
    s = sub.add_parser(
        "scoreboard", help="write data/scoreboard.json: predictions vs results (live + walk-forward)"
    )
    s.add_argument("--seasons", nargs="+", default=["2025-26", "2026-27"])
    s.set_defaults(fn=cmd_scoreboard)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
