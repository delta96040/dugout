# Match predictor backtest

Generated 2026-10-17T07:55:25+00:00. Protocol: walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block. Training data from 2016-17 (football-data.co.uk results; Opta xG via FPL from 2022-23).

Lower is better. RPS = ranked probability score.

## 2024-25

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.2019 | 0.9882 | 0.5901 | 306 |
| Stack + missing regulars (lineup-informed) | 0.2099 | 1.0192 | 0.6088 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.2099 | 1.0192 | 0.6088 | 306 |
| Stack with all features | 0.2115 | 1.0227 | 0.6116 | 306 |
| Boosted stack (pre-lineup) | 0.2093 | 1.0167 | 0.6063 | 306 |
| XGBoost on raw features | 0.2132 | 1.0356 | 0.6191 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.2105 | 1.0200 | 0.6097 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.2101 | 1.0185 | 0.6085 | 306 |
| Elo baseline | 0.2101 | 1.0191 | 0.6092 | 306 |

Beats Elo on: rps, brier

## 2025-26

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1899 | 0.9503 | 0.5614 | 306 |
| Stack + missing regulars (lineup-informed) | 0.1952 | 0.9692 | 0.5745 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1952 | 0.9692 | 0.5745 | 306 |
| Stack with all features | 0.1955 | 0.9686 | 0.5743 | 306 |
| Boosted stack (pre-lineup) | 0.1958 | 0.9756 | 0.5781 | 306 |
| XGBoost on raw features | 0.2044 | 1.0111 | 0.6010 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.1952 | 0.9719 | 0.5743 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.1952 | 0.9704 | 0.5741 | 306 |
| Elo baseline | 0.1952 | 0.9690 | 0.5746 | 306 |

Beats Elo on: rps, brier

## Notes

- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share of each club's regular starters who did not start (known ~1 h before kick-off).
- Rest days count Premier League matches only (cup and European fixtures are not in the free data).
- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; they are shown as the ceiling.
