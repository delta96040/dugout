# Predicted XI from recent starts (leagues without FPL data)

Generated 2026-10-17T08:29:02+00:00. Data: transfermarkt-datasets starting XIs (CC0), league matches in La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie. A prediction needs three earlier league XIs that season. Overlap = share of the actual XI predicted.

Best on 2023-24: last 3 XIs, recency weight 0.5 per match back, which reproduces the last XI. No weighting of older XIs beat it, so the shipped rule is the last league XI (absentees replaced).

| Season | Best recency weighting | Last league XI (shipped) | Matches |
|---|---|---|---|
| 2024-25 | 76.9% | 76.9% | 3074 |
| 2025-26 | 76.6% | 76.6% | 3070 |

By league (2024-25 and 2025-26):

| League | Best recency weighting | Last league XI (shipped) | Matches |
|---|---|---|---|
| ES1 | 72.3% | 72.3% | 1400 |
| IT1 | 76.1% | 76.1% | 1398 |
| L1 | 77.9% | 77.9% | 1116 |
| FR1 | 75.7% | 75.7% | 1114 |
| NL1 | 83.1% | 83.1% | 1116 |

Limitation: injuries and suspensions are not in the data, so neither method can drop absentees here; live, players API-Football lists as missing for the match are left out first.
