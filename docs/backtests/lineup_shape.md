# Lineup shape backtest

Generated 2026-10-16. Seasons 2025-26 and 2026-27 (to date). For each club and match, the formation is predicted
from data before kick-off and compared with the back line the manager actually started: **four defenders or not**,
counted by FPL position (GK/DEF/MID/FWD). Only matches where all eleven starters have a known FPL position count
(FPL positions exist for current players only): 324 team-matches.

| Rule | Back line right | XI overlap with the actual XI |
|---|---|---|
| **Observed shape** (current): the (defenders, forwards) count the manager started most often in the last 6 matches picks the formation family; the best XI is chosen within it, and FPL defenders only fill back-line slots | **75.3%** | 73.8% |
| Strongest fit (previous): the formation that fits the strongest XI best | 61.4% | 73.5% |
| Always a back four (base rate) | 61.1% | — |

The previous rule was no better than always guessing a back four: it chose a back three whenever a squad had
three good Transfermarkt centre-backs, even when one of them plays full-back (Liverpool 2026-27: Araújo).
The window of 6 matches was set before this test and not tuned.

Limits: FPL lists some wing-backs as MID, so only "back four or not" is scored. Who plays which slot cannot be
scored (FPL does not record it); the "defenders stay in the back line" rule changes slots, not who starts, which
is why the XI overlap barely moves. A defender listed as a centre-back still fits right-back less well than a
natural right-back, so the model can prefer the natural full-back even when the manager does not.
