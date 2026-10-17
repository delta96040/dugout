# Match predictor backtest

Generated 2026-10-17T07:57:37+00:00. Protocol: walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block. Training data from 2016-17 (football-data.co.uk results; Opta xG via FPL from 2022-23).

Lower is better. RPS = ranked probability score.

## 2024-25

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.2009 | 0.9550 | 0.5643 | 306 |
| Stack + missing regulars (lineup-informed) | 0.2060 | 0.9694 | 0.5744 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.2060 | 0.9694 | 0.5744 | 306 |
| Stack with all features | 0.2071 | 0.9774 | 0.5793 | 306 |
| Boosted stack (pre-lineup) | 0.2072 | 0.9758 | 0.5777 | 306 |
| XGBoost on raw features | 0.2131 | 0.9994 | 0.5920 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.2080 | 0.9722 | 0.5771 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.2084 | 0.9765 | 0.5795 | 306 |
| Elo baseline | 0.2054 | 0.9695 | 0.5745 | 306 |

Beats Elo on: log_loss, brier

## 2025-26

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.2010 | 0.9774 | 0.5833 | 306 |
| Stack + missing regulars (lineup-informed) | 0.2062 | 0.9970 | 0.5963 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.2062 | 0.9970 | 0.5963 | 306 |
| Stack with all features | 0.2069 | 0.9996 | 0.5975 | 306 |
| Boosted stack (pre-lineup) | 0.2051 | 0.9938 | 0.5935 | 306 |
| XGBoost on raw features | 0.2070 | 1.0105 | 0.6030 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.2064 | 0.9985 | 0.5971 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.2066 | 1.0007 | 0.5983 | 306 |
| Elo baseline | 0.2059 | 0.9971 | 0.5957 | 306 |

Beats Elo on: log_loss

## Notes

- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share of each club's regular starters who did not start (known ~1 h before kick-off).
- Rest days count Premier League matches only (cup and European fixtures are not in the free data).
- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; they are shown as the ceiling.
