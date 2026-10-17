# Match predictor backtest

Generated 2026-10-17T07:59:21+00:00. Protocol: walk-forward, refit every ~4 gameweeks (40 matches) on data strictly before the block. Training data from 2016-17 (football-data.co.uk results; Opta xG via FPL from 2022-23).

Lower is better. RPS = ranked probability score.

## 2024-25

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1847 | 0.9376 | 0.5561 | 306 |
| Stack + missing regulars (lineup-informed) | 0.1937 | 0.9630 | 0.5742 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1937 | 0.9630 | 0.5742 | 306 |
| Stack with all features | 0.1936 | 0.9621 | 0.5740 | 306 |
| Boosted stack (pre-lineup) | 0.1928 | 0.9619 | 0.5739 | 306 |
| XGBoost on raw features | 0.1974 | 0.9844 | 0.5890 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.1980 | 0.9760 | 0.5831 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.1978 | 0.9741 | 0.5822 | 306 |
| Elo baseline | 0.1934 | 0.9645 | 0.5741 | 306 |

Beats Elo on: log_loss

## 2025-26

| Model | RPS | Log loss | Brier | Matches |
|---|---|---|---|---|
| Bookmaker closing odds | 0.1954 | 0.9776 | 0.5812 | 306 |
| Stack + missing regulars (lineup-informed) | 0.1965 | 0.9834 | 0.5837 | 306 |
| **Stack: Elo + xG Dixon–Coles (pre-lineup) — dugout predictor** | 0.1965 | 0.9834 | 0.5837 | 306 |
| Stack with all features | 0.1979 | 0.9889 | 0.5874 | 306 |
| Boosted stack (pre-lineup) | 0.1965 | 0.9815 | 0.5828 | 306 |
| XGBoost on raw features | 0.1993 | 0.9986 | 0.5908 | 306 |
| Dixon–Coles, goals/xG blend (posterior mode) | 0.1952 | 0.9807 | 0.5835 | 306 |
| Dixon–Coles, goals (posterior mode) | 0.1951 | 0.9791 | 0.5830 | 306 |
| Elo baseline | 0.1970 | 0.9848 | 0.5844 | 306 |

Beats Elo on: rps, log_loss, brier

## Notes

- *Pre-lineup* uses only information available days before kick-off. *Lineup-informed* adds the share of each club's regular starters who did not start (known ~1 h before kick-off).
- Rest days count Premier League matches only (cup and European fixtures are not in the free data).
- Bookmaker probabilities remove the margin proportionally. Beating closing odds is not expected; they are shown as the ceiling.
