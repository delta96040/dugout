# Lineup optimiser backtest

Generated 2026-10-15T15:56:37+00:00. Season 2024-25: for each club and match, the optimiser picks the best XI from players available that day (injury absences removed), using only data before kick-off.

| Method | Mean overlap with the actual XI | Team-matches |
|---|---|---|
| Strongest XI (quality only: ICT/90 x position fit, fatigue) | 50.6% | 658 |
| **Predicted XI** (quality + trust, weight 0.75) | 78.3% | 658 |
| Most minutes in the last 5 matches (heuristic) | 77.0% | 658 |

Trust weight 0.75 was chosen on 2023-24 (validation); this table is the 2024-25 test.
Trust = share of the last five matches' minutes. The strongest XI ignores managers' habits by design.

## Out-of-rotation penalty (2026-10-17)

A player with no minutes in the club's last five matches had the same chance of a slot as a regular, so a natural
defensive midfielder unused all season (Endo, Liverpool) was predicted ahead of regulars playing slightly out of
position. Now his value in any slot is scaled by `UNUSED_FACTOR`. Predicted XI (trust 0.75), mean overlap with the
actual XI, 658 team-matches per season:

| Factor | 2023-24 (choose) | 2024-25 (test) |
|---|---|---|
| 1.0 (before) | 78.0% | 78.25% |
| **0.6 (chosen)** | **78.2%** | **78.3%** |
| 0.3 | 78.2% | 78.3% |
| 0.1 | 78.2% | 78.3% |
| Most minutes in the last 5 matches | 76.7% | 77.0% |

A small gain, the same in both seasons; it removes picks that a reader can see are wrong.
