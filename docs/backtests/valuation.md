# Transfer valuation backtest

Generated 2026-10-16T17:19:22+00:00. Data: transfermarkt-datasets (CC0), transfers with a disclosed fee.

Train: 11770 transfers 2005-01-01 .. 2024-06-29. Test: 2024-07-01 .. 2026-09-03.

| Estimate | MAE (log fee) | Median abs % error | Within ±25% | Transfers |
|---|---|---|---|---|
| **dugout valuation** | 0.577 | 41.8% | 31.5% | 4702 |
| Market value at the time | 0.678 | 50.0% | 29.9% | 4702 |

By segment (MAE of log fee; fee / estimate is the median ratio, 1.00 = unbiased):

| Segment | Transfers | dugout | previous model | market value | fee / estimate (previous) |
|---|---|---|---|---|---|
| all | 4702 | 0.577 | 0.624 | 0.678 | 0.99 (1.08) |
| Premier League buyer | 371 | 0.413 | 0.516 | 0.525 | 0.95 (1.34) |
| Premier League to Premier League | 111 | 0.350 | 0.605 | 0.488 | 1.06 (1.65) |
| Premier League buyer, age 24 or under | 215 | 0.426 | 0.553 | 0.630 | 1.00 (1.38) |

The previous model regressed log(fee) on the transfer year; trees cannot extrapolate a year they have not seen, so it underpriced recent Premier League deals. Nationality (an English or homegrown premium) was tested as a feature and added nothing once club context is known; it is not used.

Feature importance (split count): log_to_cv 3091, age 2949, log_from_cv 2922, log_mv 2668, log_to_league 1991, log_from_league 1659, minutes_365 712, ga_p90_365 515, apps_365 476, pos_Attack 250, same_league 188, pos_Defender 172, pos_Midfield 137, to_pl 115, pos_Goalkeeper 113, pl_to_pl 42

Limitation: contract months remaining is not available historically, so it is not a feature.
