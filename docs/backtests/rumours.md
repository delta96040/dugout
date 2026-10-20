# Transfer rumour track record backtest

Generated 2026-10-19T15:04:10+00:00. Rumours: the Guardian's Rumour Mill archive (Guardian Open Platform), every
column since February 2001. What happened: transfermarkt-datasets (CC0) to 2026-07-06, then Wikipedia's
summer-2026 transfer lists (CC BY-SA) to 2026-09-02. A rumour *came true* when the player
joined the club it linked him with within the horizon; links whose horizon is not over are left out.

## Matching

- Paragraphs naming someone in bold: 32725. Bold names that are clubs (older columns bold clubs too): 21754; other names: 53206.
- Names matched to a Transfermarkt player active at the time: 29850 (56%); 1103 had namesakes (resolved by the club the item mentions, then by peak value). Unmatched: managers, nicknames, surnames only, and players the dataset does not cover (few before 2004).
- Matched players with no other club named near them: 9243.
- Links (rumour x player x club, the player's own club and loan owner excluded): 36796, from 14084 paragraphs (43%).
- Where a player played when: transfermarkt-datasets carries transfer histories only for players active
  in 2023 or later, so clubs on a date come from transfers, appearances (2012 onwards) and the valuations'
  dated club names together (the valuations' club id is today's club, not the club on the day).

## Results

Train on every season from 2004-05 up to the test season; test on the latest season with labels.

### Within 3 months: test 2025-26, 618 links, 17.8% came true

Training 2004-25: 33992 links, 7.3% came true (15.5% from 2020-21).

| Forecast | Brier | Log loss | AUC | Mean forecast |
|---|---|---|---|---|
| Base rate, all training seasons | 0.1573 | 0.5279 | 0.500 | 7.3% |
| Base rate since 2020-21 | 0.1468 | 0.4702 | 0.500 | 15.5% |
| Outlet record only (shrunk) | 0.1571 | 0.5269 | 0.506 | 7.4% |
| **Shipped: logistic** | 0.1311 | 0.4174 | 0.745 | 16.8% |
| with the outlet record | 0.1313 | 0.4179 | 0.745 | 16.3% |
| without the era flag | 0.1397 | 0.4438 | 0.729 | 10.7% |
| without repeats | 0.1397 | 0.4425 | 0.668 | 17.1% |
| trained on 2020-21 onwards only | 0.1328 | 0.4211 | 0.729 | 16.9% |

### Within 6 months: test 2025-26, 618 links, 18.4% came true

Training 2004-25: 33992 links, 9.4% came true (17.0% from 2020-21).

| Forecast | Brier | Log loss | AUC | Mean forecast |
|---|---|---|---|---|
| Base rate, all training seasons | 0.1585 | 0.5162 | 0.500 | 9.4% |
| Base rate since 2020-21 | 0.1507 | 0.4789 | 0.500 | 17.0% |
| Outlet record only (shrunk) | 0.1584 | 0.5156 | 0.508 | 9.5% |
| **Shipped: logistic** | 0.1354 | 0.4317 | 0.720 | 18.4% |
| with the outlet record | 0.1364 | 0.4335 | 0.723 | 16.0% |
| without the era flag | 0.1407 | 0.4472 | 0.706 | 13.0% |
| without repeats | 0.1449 | 0.4577 | 0.644 | 18.7% |
| trained on 2020-21 onwards only | 0.1374 | 0.4342 | 0.719 | 18.5% |

### Within 12 months: test 2025-26, 388 links, 23.5% came true

Training 2004-25: 33992 links, 11.4% came true (18.5% from 2020-21).

| Forecast | Brier | Log loss | AUC | Mean forecast |
|---|---|---|---|---|
| Base rate, all training seasons | 0.1942 | 0.6024 | 0.500 | 11.4% |
| Base rate since 2020-21 | 0.1820 | 0.5523 | 0.500 | 18.5% |
| Outlet record only (shrunk) | 0.1940 | 0.6018 | 0.509 | 11.4% |
| **Shipped: logistic** | 0.1564 | 0.4823 | 0.744 | 21.4% |
| with the outlet record | 0.1576 | 0.4847 | 0.745 | 20.1% |
| without the era flag | 0.1647 | 0.5034 | 0.737 | 15.9% |
| without repeats | 0.1717 | 0.5209 | 0.654 | 21.2% |
| trained on 2020-21 onwards only | 0.1577 | 0.4821 | 0.750 | 21.2% |

## Shipped: 6 months

Calibration on the test season:

| Forecast | Links | Mean forecast | Came true |
|---|---|---|---|
| 0%-5% | 5 | 4.3% | 0.0% |
| 5%-10% | 87 | 8.1% | 3.4% |
| 10%-20% | 345 | 14.9% | 15.7% |
| 20%-30% | 107 | 23.6% | 20.6% |
| 30%-50% | 65 | 37.9% | 43.1% |
| 50%-100% | 9 | 55.7% | 77.8% |

Coefficients (standardised features): age -0.009, age2 -0.044, log_mv -0.356, buyer_value 0.143, seller_value 0.062, wealth_gap 0.043, buyer_unknown 0.002, same_league 0.125, log_days_to_deadline 0.02, in_window 0.405, log_repeats 0.518, n_clubs -0.571, n_players -0.095, modern_column 0.231

Outlet records, shrunk to the overall rate with a Beta(200.0, 2040.0) prior (fitted by maximum marginal likelihood): outlets with 20+ training links.

| Outlet | Links | Came true | Raw rate | Shrunk rate |
|---|---|---|---|---|
| The Guardian | 29448 | 2824 | 9.6% | 9.5% |
| The Mirror | 889 | 74 | 8.3% | 8.8% |
| Daily Mail | 705 | 50 | 7.1% | 8.5% |
| The Sun | 553 | 52 | 9.4% | 9.0% |
| Daily Star | 299 | 19 | 6.4% | 8.6% |
| Daily Express | 172 | 12 | 7.0% | 8.8% |
| L'Equipe | 142 | 13 | 9.2% | 8.9% |
| The Times | 138 | 13 | 9.4% | 9.0% |
| The Telegraph | 134 | 12 | 9.0% | 8.9% |
| La Gazzetta dello Sport | 131 | 13 | 9.9% | 9.0% |
| Tuttosport | 127 | 8 | 6.3% | 8.8% |
| Bild | 117 | 13 | 11.1% | 9.0% |
| a reporter on X | 105 | 13 | 12.4% | 9.1% |
| Sky Sports | 98 | 10 | 10.2% | 9.0% |
| Marca | 86 | 5 | 5.8% | 8.8% |
| AS | 61 | 5 | 8.2% | 8.9% |
| Corriere dello Sport | 57 | 6 | 10.5% | 9.0% |
| The Independent | 57 | 5 | 8.8% | 8.9% |
| talkSPORT | 55 | 4 | 7.3% | 8.9% |
| Evening Standard | 53 | 2 | 3.8% | 8.8% |
| Mundo Deportivo | 49 | 2 | 4.1% | 8.8% |
| Daily Record | 43 | 7 | 16.3% | 9.1% |
| Calciomercato | 37 | 4 | 10.8% | 9.0% |
| Kicker | 29 | 2 | 6.9% | 8.9% |
| Sky Italia | 29 | 3 | 10.3% | 8.9% |
| A Bola | 23 | 6 | 26.1% | 9.1% |
| BBC Sport | 21 | 4 | 19.0% | 9.0% |
| The Athletic | 21 | 5 | 23.8% | 9.1% |

Rate by season (all labelled links):

| Season | Links | Came true |
|---|---|---|
| 2004-05 | 361 | 5.3% |
| 2005-06 | 631 | 4.4% |
| 2006-07 | 1043 | 5.3% |
| 2007-08 | 807 | 6.2% |
| 2008-09 | 954 | 6.9% |
| 2009-10 | 1368 | 5.5% |
| 2010-11 | 1778 | 6.0% |
| 2011-12 | 1961 | 9.2% |
| 2012-13 | 2164 | 7.7% |
| 2013-14 | 3098 | 9.4% |
| 2014-15 | 2852 | 8.8% |
| 2015-16 | 2574 | 9.5% |
| 2016-17 | 2804 | 9.7% |
| 2017-18 | 2772 | 9.8% |
| 2018-19 | 2940 | 9.8% |
| 2019-20 | 1728 | 8.4% |
| 2020-21 | 922 | 13.4% |
| 2021-22 | 648 | 18.7% |
| 2022-23 | 1003 | 18.2% |
| 2023-24 | 890 | 17.4% |
| 2024-25 | 694 | 17.6% |
| 2025-26 | 618 | 18.4% |

## Source credibility labels (the rumour brief)

Each outlet's record is set against what the same reports would have done from anyone: the shipped rumour
model (which never sees the outlet), fitted on the other seasons, gives each report's chance; their sum is
what the outlet *should* have got right. The label comes from the exact Poisson 80% interval of came true /
expected: wholly above 1 = **reliable**, wholly below 1 = **speculative**, otherwise **about average**;
fewer than 20 traced reports = **unproven**. Raw rates mislead: outlets credited mostly in
recent seasons look good because the column's later items come true twice as often.

| Outlet | Links | Came true | Raw rate | Similar reports | Ratio (80% interval) | Label |
|---|---|---|---|---|---|---|
| The Guardian | 30010 | 2929 | 9.8% | 9.7% | 1.01 (0.98-1.03) | about average |
| The Mirror | 890 | 74 | 8.3% | 8.2% | 1.01 (0.86-1.18) | about average |
| Daily Mail | 707 | 50 | 7.1% | 8.0% | 0.88 (0.73-1.06) | about average |
| The Sun | 561 | 54 | 9.6% | 9.7% | 0.99 (0.82-1.18) | about average |
| Daily Star | 302 | 19 | 6.3% | 9.1% | 0.69 (0.50-0.94) | speculative |
| Daily Express | 175 | 13 | 7.4% | 9.2% | 0.80 (0.53-1.17) | about average |
| L'Equipe | 146 | 13 | 8.9% | 10.7% | 0.83 (0.55-1.22) | about average |
| The Times | 144 | 15 | 10.4% | 10.8% | 0.96 (0.66-1.37) | about average |
| The Telegraph | 135 | 13 | 9.6% | 12.3% | 0.78 (0.52-1.14) | about average |
| La Gazzetta dello Sport | 134 | 13 | 9.7% | 8.4% | 1.16 (0.77-1.69) | about average |
| Tuttosport | 128 | 8 | 6.2% | 6.3% | 0.99 (0.57-1.60) | about average |
| Bild | 117 | 13 | 11.1% | 6.9% | 1.62 (1.08-2.36) | reliable |
| a reporter on X | 105 | 13 | 12.4% | 11.1% | 1.12 (0.74-1.63) | about average |
| Sky Sports | 102 | 10 | 9.8% | 11.6% | 0.84 (0.52-1.30) | about average |
| Marca | 86 | 5 | 5.8% | 9.3% | 0.63 (0.31-1.16) | about average |
| AS | 61 | 5 | 8.2% | 8.0% | 1.02 (0.50-1.89) | about average |
| Corriere dello Sport | 58 | 6 | 10.3% | 8.3% | 1.24 (0.65-2.18) | about average |
| The Independent | 58 | 5 | 8.6% | 8.0% | 1.08 (0.52-2.00) | about average |
| talkSPORT | 58 | 5 | 8.6% | 6.7% | 1.30 (0.63-2.40) | about average |
| Evening Standard | 53 | 2 | 3.8% | 9.4% | 0.40 (0.11-1.07) | about average |
| Mundo Deportivo | 49 | 2 | 4.1% | 7.3% | 0.56 (0.15-1.49) | about average |
| Daily Record | 43 | 7 | 16.3% | 7.7% | 2.12 (1.18-3.56) | reliable |
| Calciomercato | 37 | 4 | 10.8% | 8.0% | 1.34 (0.59-2.69) | about average |
| Sky Italia | 33 | 4 | 12.1% | 12.5% | 0.97 (0.42-1.94) | about average |
| Kicker | 29 | 2 | 6.9% | 8.0% | 0.86 (0.23-2.30) | about average |
| The Athletic | 24 | 6 | 25.0% | 15.4% | 1.62 (0.85-2.85) | about average |
| A Bola | 23 | 6 | 26.1% | 7.2% | 3.63 (1.91-6.37) | reliable |
| BBC Sport | 21 | 4 | 19.0% | 11.7% | 1.63 (0.71-3.26) | about average |

Do the labels hold? Labels from the seasons before 2023-24 (out of fold within them), then each label's
later reports (2023-24 onwards) against a model fitted on the earlier seasons:

| Label (earlier seasons) | Outlets | Later links | Came true | Expected | Ratio (80% interval) |
|---|---|---|---|---|---|
| about average | 17 | 2173 | 388 | 363.4 | 1.07 (1.00-1.14) |
| reliable | 1 | 4 | 0 | 0.5 | 0.00 (0.00-4.70) |
| speculative | 1 | 3 | 0 | 0.5 | 0.00 (0.00-4.98) |
| unproven | 6 | 22 | 3 | 3.5 | 0.87 (0.32-1.94) |

## Reading this honestly

- **Outlets barely differ (a negative result).** The fitted prior is strong, so even outlets with hundreds
  of links are shrunk close to the average, and the outlet-only forecast is no better than the base rate.
  Adding the outlet's record to the rumour model made both validation seasons (2023-24, 2024-25) and the
  test season slightly worse, so it is not used to score rumours; the records are published as they are.
  Most items (the column's own summaries) credit no outlet; outlets are known only when the column links
  to or names them. What separates rumours is the rumour itself: how often the same link was reported
  before, how many clubs the item names, the transfer window, and how valuable the player is.
- **Source labels are descriptive, not yet confirmed.** Set against similar reports, a handful of outlets stand
  apart (the table above); the rest, and the column's own round-ups, are about average. Too few later reports
  from the labelled outlets exist to show the labels hold out of sample (the check above), so the site says so.
  Since 2020-21 the column credits an outlet for only a few percent of its items, so most live rumours carry
  the column's own label.
- **The column changed.** From 2020-21 it runs fewer, shorter items and about twice as many come true; a base rate from all years badly underestimates today's rumours. The era flag handles
  it; the base rate since then is the fair baseline to beat.
- **Labels are a lower bound.** A move counts only if it is in transfermarkt-datasets or, after July 2026,
  on one of the Wikipedia lists (13 European leagues: moves to Saudi Arabia, the United States or Portugal
  in summer 2026 are missed). Dates from appearances or valuations are first sightings, so a move can look
  later than it was. Links come from club names near the player's name; some are noise (a club mentioned
  for another reason), which also pulls the rates down. Before 2012-13 there are no appearances, so club
  timelines rest on transfers and valuations alone and the earlier seasons' rates are probably understated.
- **Calibration is rough at the edges:** the lowest forecasts came true less often than forecast and the
  few highest ones more often (table above); the middle, where most rumours sit, is close.
- **Not used:** contract length (only today's contracts exist; for old rumours they leak the outcome).

