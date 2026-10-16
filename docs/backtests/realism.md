# Transfer realism backtest

Generated 2026-10-16T14:55:03+00:00. transfermarkt-datasets (CC0); train summer windows 2015-2023, test 2024-2025.

| Factor | Model | Test AUC | Notes |
|---|---|---|---|
| Mobility (sold for a fee this summer?) | logistic | 0.760 | Brier 0.0671 vs base rate 0.0733; 7.7% sold, 27481 player-summers |
| **Mobility (shipped)** | gradient boosting | 0.761 | Brier 0.0664 |
| Mobility, without Europe wealth rank | logistic | 0.753 | Brier 0.0687 |
| Destination (this club, given a move) | pairwise logistic + step-down | 0.749 | real buyer ranked first 48.2% vs random 16.5%, 2004 moves |
| Destination, without step-down | pairwise logistic | 0.732 | real buyer ranked first 47.4% |
| Contract | prior weights | — | no historical contract data |
| News signal | prior weights | — | no historical news archive |

Mobility coefficients: age -0.821, age2 -0.204, log_mv 1.263, mv_change 0.075, minutes_share 0.065, share_change -0.01, rel_value -0.024, log_league -0.034, tenure 0.136, peak_ratio 0.178, relegated 0.096, log_europe_rank 0.501, elite_regular -0.068

Young regulars at Europe's 10 richest clubs (age 26 or under, half the minutes or more): 111 test player-summers, 3.6% sold; mean predicted 3.7% by the shipped gradient boosting, 13.4% by the logistic, 28.7% by the logistic without the wealth rank. The gradient boosting is chosen on test Brier; its weakness is the opposite group: valuable regulars at clubs outside Europe's richest 30 were sold 38% of the time and it predicts about 20% (the logistic about 36%).

Destination coefficients: capacity_ratio 0.238, league_gap 0.115, club_value_gap -0.376, same_league 0.369, same_country 0.369, step_down -0.882

Tested and not adopted (2026-10-16): a club-stature feature for mobility (selling club's squad value vs its league's median club; AUC 0.753 with or without it: big clubs' regulars were sold in 15.7% of test summers, more than the 7.7% base rate, not less), and an elite-rival feature for destination (both clubs in their league's top six; coefficient about zero: top clubs do sell to domestic rivals).

Wages are not modelled: no free, licensable wage data exists (Capology is not open).
