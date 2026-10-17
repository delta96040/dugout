# Match predictor backtest

Generated 2026-10-17T07:53:41+00:00. Protocol: walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block. Training data from 2016-17 (football-data.co.uk results; Opta xG via FPL from 2022-23).

Lower is better. RPS = ranked probability score.

## 2024-25

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1841 | 0.9513 | 0.5669 | 380 |
| Stack + missing regulars (lineup-informed) | 0.1903 | 0.9705 | 0.5810 | 380 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1903 | 0.9705 | 0.5810 | 380 |
| Stack with all features | 0.1913 | 0.9759 | 0.5846 | 380 |
| Boosted stack (pre-lineup) | 0.1890 | 0.9666 | 0.5781 | 380 |
| XGBoost on raw features | 0.1908 | 0.9778 | 0.5857 | 380 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.1910 | 0.9782 | 0.5832 | 380 |
| Dixon–Coles, goals (posterior mode) | 0.1908 | 0.9752 | 0.5822 | 380 |
| Elo baseline | 0.1906 | 0.9759 | 0.5843 | 380 |

Beats Elo on: rps, log_loss, brier

## 2025-26

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1960 | 0.9777 | 0.5828 | 380 |
| Stack + missing regulars (lineup-informed) | 0.2022 | 0.9987 | 0.5959 | 380 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.2022 | 0.9987 | 0.5959 | 380 |
| Stack with all features | 0.2021 | 0.9997 | 0.5959 | 380 |
| Boosted stack (pre-lineup) | 0.2023 | 1.0007 | 0.5967 | 380 |
| XGBoost on raw features | 0.2076 | 1.0255 | 0.6132 | 380 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.2021 | 0.9976 | 0.5952 | 380 |
| Dixon–Coles, goals (posterior mode) | 0.2020 | 0.9979 | 0.5954 | 380 |
| Elo baseline | 0.2024 | 0.9987 | 0.5958 | 380 |

Beats Elo on: rps

## Notes

- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share of each club's regular starters who did not start (known ~1 h before kick-off).
- Rest days count Premier League matches only (cup and European fixtures are not in the free data).
- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; they are shown as the ceiling.
