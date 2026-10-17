# Transfer valuation backtest

Generated 2026-10-17T08:41:19+00:00. Data: transfermarkt-datasets (CC0), transfers with a disclosed fee.

Train: 11770 transfers 2005-01-01 .. 2024-06-29. Test: 2024-07-01 .. 2026-09-03.

| Estimate | MAE (log fee) | Median abs % error | Within ±25% | Transfers |
|---|---|---|---|---|
| **dugout valuation** | 0.575 | 41.5% | 31.6% | 4702 |
| Market value at the time | 0.678 | 50.0% | 29.9% | 4702 |

By segment (MAE of log fee; fee / estimate is the median ratio, 1.00 = unbiased):

| Segment | Transfers | dugout | without league offsets | previous model | market value | fee / estimate (previous) |
|---|---|---|---|---|---|---|
| all | 4702 | 0.575 | 0.577 | 0.624 | 0.678 | 1.00 (1.08) |
| Premier League buyer | 371 | 0.412 | 0.413 | 0.516 | 0.525 | 0.96 (1.34) |
| Premier League to Premier League | 111 | 0.351 | 0.350 | 0.605 | 0.488 | 1.07 (1.65) |
| Premier League buyer, age 24 or under | 215 | 0.426 | 0.426 | 0.553 | 0.630 | 1.01 (1.38) |
| La Liga buyer | 175 | 0.531 | 0.529 | 0.608 | 0.641 | 1.07 (0.85) |
| La Liga to La Liga | 63 | 0.697 | 0.694 | 0.804 | 0.787 | 1.09 (0.84) |
| Serie A buyer | 345 | 0.498 | 0.501 | 0.528 | 0.527 | 1.02 (1.04) |
| Serie A to Serie A | 128 | 0.513 | 0.520 | 0.533 | 0.507 | 1.04 (1.07) |
| Bundesliga buyer | 269 | 0.563 | 0.589 | 0.624 | 0.608 | 0.99 (0.78) |
| Bundesliga to Bundesliga | 82 | 0.579 | 0.627 | 0.667 | 0.637 | 0.98 (0.73) |
| Ligue 1 buyer | 213 | 0.485 | 0.482 | 0.502 | 0.594 | 1.19 (1.07) |
| Ligue 1 to Ligue 1 | 53 | 0.508 | 0.504 | 0.493 | 0.639 | 1.21 (1.13) |
| Eredivisie buyer | 141 | 0.522 | 0.536 | 0.573 | 0.626 | 0.84 (1.13) |
| Eredivisie to Eredivisie | 46 | 0.512 | 0.516 | 0.542 | 0.639 | 0.99 (1.12) |

League offsets (log fee, buyers in each configured league; learned on 2022-07..2024-06 from a model fitted before 2022-07, shrunk by n / (n + 50); applied to every configured league, including the two where they made the test slightly worse): ES1 -0.109, FR1 -0.016, GB1 -0.008, IT1 +0.053, L1 -0.209, NL1 -0.070

The previous model regressed log(fee) on the transfer year; trees cannot extrapolate a year they have not seen, so it underpriced recent Premier League deals. Nationality (an English or homegrown premium) was tested as a feature and added nothing once club context is known; it is not used.

Feature importance (split count): log_to_cv 3091, age 2949, log_from_cv 2922, log_mv 2668, log_to_league 1991, log_from_league 1659, minutes_365 712, ga_p90_365 515, apps_365 476, pos_Attack 250, same_league 188, pos_Defender 172, pos_Midfield 137, to_pl 115, pos_Goalkeeper 113, pl_to_pl 42

Limitation: contract months remaining is not available historically, so it is not a feature.

Other leagues' buyers (the fair fee on those clubs' dossiers): read MAE against the market-value column and the median ratio; a ratio below 1.00 means fees came in under the estimate (it runs high there).
