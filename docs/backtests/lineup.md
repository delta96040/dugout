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

### Threshold for "out of the rotation" (2026-10-17)

A two-minute cameo counted as a place in the side, so Chiesa (2 minutes in 2026-27) was still predicted. Now a player
under `UNUSED_SHARE` of the last five matches' minutes is out of the rotation (factor 0.6):

| Share of the last five matches' minutes | 2023-24 (choose) | 2024-25 (test) |
|---|---|---|
| none at all (before) | 78.2% | 78.3% |
| under 2% | 78.2% | 78.3% |
| under 5% | 78.3% | 78.3% |
| **under 10% (chosen)** | **78.3%** | **78.35%** |
