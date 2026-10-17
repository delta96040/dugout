# Match predictor backtest

Generated 2026-10-17T07:50:58+00:00. Protocol: walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block. Training data from 2016-17 (football-data.co.uk results; Opta xG via FPL from 2022-23).

Lower is better. RPS = ranked probability score.

## 2024-25

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1875 | 0.9463 | 0.5593 | 380 |
| Stack + missing regulars (lineup-informed) | 0.1953 | 0.9725 | 0.5764 | 380 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1953 | 0.9725 | 0.5764 | 380 |
| Stack with all features | 0.1964 | 0.9799 | 0.5804 | 380 |
| Boosted stack (pre-lineup) | 0.1954 | 0.9746 | 0.5782 | 380 |
| XGBoost on raw features | 0.1981 | 0.9872 | 0.5880 | 380 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.1963 | 0.9752 | 0.5786 | 380 |
| Dixon–Coles, goals (posterior mode) | 0.1963 | 0.9755 | 0.5788 | 380 |
| Elo baseline | 0.1954 | 0.9731 | 0.5772 | 380 |

Beats Elo on: rps, log_loss, brier

## 2025-26

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1958 | 0.9650 | 0.5716 | 380 |
| Stack + missing regulars (lineup-informed) | 0.1995 | 0.9757 | 0.5779 | 380 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1995 | 0.9757 | 0.5779 | 380 |
| Stack with all features | 0.2000 | 0.9791 | 0.5800 | 380 |
| Boosted stack (pre-lineup) | 0.1989 | 0.9721 | 0.5758 | 380 |
| XGBoost on raw features | 0.2040 | 0.9995 | 0.5911 | 380 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.2009 | 0.9791 | 0.5816 | 380 |
| Dixon–Coles, goals (posterior mode) | 0.2009 | 0.9795 | 0.5818 | 380 |
| Elo baseline | 0.2000 | 0.9794 | 0.5798 | 380 |

Beats Elo on: rps, log_loss, brier

## Notes

- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share of each club's regular starters who did not start (known ~1 h before kick-off).
- Rest days count Premier League matches only (cup and European fixtures are not in the free data).
- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; they are shown as the ceiling.
