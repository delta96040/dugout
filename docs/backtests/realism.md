# Transfer realism backtest

Generated 2026-10-17T06:44:35+00:00. transfermarkt-datasets (CC0); train summer windows 2015-2023, test 2024-2025.

| Factor | Model | Test AUC | Notes |
|---|---|---|---|
| Mobility (sold for a fee this summer?) | logistic | 0.753 | Brier 0.0678 vs base rate 0.0729; 7.9% sold, 27733 player-summers |
| **Mobility (shipped)** | gradient boosting | 0.764 | Brier 0.0672; mean forecast 7.4% |
| Mobility, previous frame (today's club) | gradient boosting | 0.750 | Brier 0.0703; mean forecast 7.0% (same test player-summers) |
| Mobility, without Europe wealth rank | logistic | 0.749 | Brier 0.0685 |
| Destination (this club, given a move) | pairwise logistic + step-down | 0.733 | real buyer ranked first 47.2% vs random 16.5%, 2004 moves |
| Destination, without step-down | pairwise logistic | 0.727 | real buyer ranked first 47.3% |
| Destination, previous frame (today's club) | pairwise logistic + step-down | 0.749 | real buyer ranked first 48.2%; inflated, see below |
| Contract | prior weights | — | no historical contract data |
| News signal | prior weights | — | no historical news archive |

**Club on the day (2026-10-17).** transfermarkt-datasets' `player_valuations.current_club_id` is the player's club today, not on the valuation date, and `transfers` has no rows for anyone whose last season is before 2023 (gaps for active players too). The frames used to take a player's club from that id: wrong for 45% of players on 1 June 2016, 33% in 2022, 19% in 2024, so their minutes share, club value, wealth rank, tenure and relegation flag described another club. Clubs now come from a timeline of transfers, club appearances (national teams excluded) and the valuations' dated club names (`club_people`, shared with the rumour track record), and mobility trains only on players the dataset has a transfer history for (else "sold" can never be true for them; chosen on validation season 2023: Brier 0.0439 vs 0.0442, mean forecast 5.3% vs 3.6% for a 5.1% rate). The previous destination frame counted each club's squad by today's club, so a buyer's value already included the player it was buying and anyone it signed later: its higher scores are hindsight, and the drop is the honest number.

Mobility coefficients: age -0.359, age2 -0.097, log_mv 0.74, mv_change 0.048, minutes_share 0.119, share_change 0.007, rel_value 0.153, log_league 0.018, tenure -0.054, peak_ratio 0.056, relegated 0.087, log_europe_rank 0.302, elite_regular -0.14

Young regulars at Europe's 10 richest clubs (age 26 or under, half the minutes or more): 109 test player-summers, 4.6% sold; mean predicted 4.7% by the shipped gradient boosting, 10.4% by the logistic, 25.3% by the logistic without the wealth rank. The gradient boosting is chosen on test Brier. Before the club fix its weakness was the opposite group (valuable regulars at clubs outside Europe's richest 30: sold 38% of the time, predicted about 20%); not re-measured.

Destination coefficients: capacity_ratio 0.295, league_gap 0.148, club_value_gap -0.091, same_league 0.376, same_country 0.376, step_down -0.334

Tested and not adopted (2026-10-16): a club-stature feature for mobility (selling club's squad value vs its league's median club; AUC 0.753 with or without it: big clubs' regulars were sold in 15.7% of test summers, more than the 7.7% base rate, not less), and an elite-rival feature for destination (both clubs in their league's top six; coefficient about zero: top clubs do sell to domestic rivals).

Wages are not modelled: no free, licensable wage data exists (Capology is not open).
