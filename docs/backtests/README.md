# Backtests

Every model ships with a reproducible backtest. Regenerate all of them with:

```bash
uv run dugout-models backtest all
```

| Model | Report | Protocol | Headline result |
|---|---|---|---|
| Match predictor | [predictor.md](predictor.md) · [validation](predictor_validation.md) | Walk-forward, refit every ~4 gameweeks; design chosen on 2019-20..2023-24, tested once on 2024-25 and 2025-26 | Beats Elo on RPS, log loss and Brier in both test seasons; bookmaker closing odds remain better |
| Player quality (VAEP, xT) | [player_ratings.md](player_ratings.md) | VAEP probability models on 380 held-out games (StatsBomb PL 2015/16 + Wyscout PL 2017/18) | AUC 0.75 (scores), 0.78 (concedes); both beat base-rate Brier |
| Roles & similarity | [roles.md](roles.md) | Own-previous-season retrieval; bootstrap role stability | ~3x random retrieval; stability ARI ~0.7 |
| Manager fingerprint & style similarity | [fingerprint.md](fingerprint.md) | Style similarity vs minutes share for players who changed club | Descriptive only: style similarity does **not** predict minutes (ρ ≈ 0.07) |
| Tactical fit: playing-time outlook | [opportunity.md](opportunity.md) | Summer transfers, train 2016-22, test 2023-25 | R² 0.26 vs 0.03 baseline; Spearman 0.55; top vs bottom quartile 47% vs 7% of minutes |
| FootballBERT embeddings | [embeddings.md](embeddings.md) | Leakage-guarded fit test on 4,966 transfers | Not adopted: apparent signal was leakage |
| Valuation | [valuation.md](valuation.md) | Train < July 2024, test after | Beats "fee = market value" on MAE and median error |
| Transfer realism | [realism.md](realism.md) | Train summer windows 2015-23, test 2024-25 | Mobility (sold for a fee) AUC 0.76 (gradient boosting, with Europe wealth rank), calibrated for elite clubs' young regulars; destination AUC 0.75 (0.73 before the step-down feature) |
| Injury risk | [injury.md](injury.md) | Train 2022-24, test 2024-25 | AUC 0.64; top-decile lift 2.3x; shown as risk tiers (calibration limited by season-to-season reporting shifts) |
| Player potential | [potential.md](potential.md) | Time split: train snapshots to 2021 (2 y) / 2019 (4 y), test 2023-24 / 2021-22 | Beats age-curve and persistence baselines at both horizons; median error 42% (2 y), 54% (4 y); wide bands shown |
| Fact-Check NLI | [factcheck_nli.md](factcheck_nli.md) | 48 hand-labelled football premise/claim pairs | DeBERTa-v3 base and large: no false verifications; base 3x faster (used) |
| Agents (end to end) | [templates](agents_eval_templates.md) · [LLM](agents_eval_llm.md) | 56 hand-labelled questions | See reports: 0 untraceable numbers is the hard target |
| Lineup optimiser | [lineup.md](lineup.md) | Trust weight chosen on 2023-24, test 2024-25 | Predicted XI 78% overlap vs 77% heuristic |
| Lineup shape (formation) | [lineup_shape.md](lineup_shape.md) | Formation from the manager's last 6 XIs, scored on 2025-27 | Back line right 75% vs 61% for the previous rule (= base rate) |

Negative and weak results are published on purpose: the site shows each model's evidence next to its output.
