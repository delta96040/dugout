# Playing style: roles, percentiles and similar players (API-Football season totals)

Generated 2026-10-19T15:16:10+00:00 by `uv run dugout-models backtest style`. Players with >= 900 league minutes in a season; percentiles within the style group, the season and the league tier (Europe's biggest five leagues / the rest). Model: dugout_models/style.py.

## Data on hand

The free plan allows 100 requests a day and three pages of 20 players per query, so the backfill (`api_football_stats`, nightly) takes a club at a time: ~3 requests a club, at most 70 a day. Coverage when this report was generated:

| Season | League | Clubs | Player rows | With a Transfermarkt id | 900+ minutes |
|---|---|---|---|---|---|
| 2024-25 | Premier League | 18 | 444 | 443 (100%) | 257 |

## Roles

Two ways to name a role, on the same standardised per-90 features (within season, tier and group):

- **Archetype scores (shipped):** each player projected on transparent archetype weights (`ARCHETYPES`); the top archetype is his role when it scores >= 0.5 and leads the next by >= 0.25 (else no role is claimed).
- **Gaussian mixture (tried first):** k by BIC (2 to the number of archetypes), components named by the archetype their centre matches best.

Silhouette: how well the roles separate (-1..1; the same labels shuffled give ~0). Bootstrap: mean adjusted Rand index between the roles and roles refitted on 20 resamples (1 = identical, 0 = chance; unnamed players count as one class).

| Group | Players | Method | Roles (players) | Silhouette (shuffled) | Bootstrap ARI |
|---|---|---|---|---|---|
| centre-back | 53 | archetypes | no clear role 19; ball-playing centre-back 15; covering centre-back 11; front-foot defender 8 | 0.11 (-0.05) | 0.64 |
| centre-back | 53 | mixture, k=2 | front-foot defender 35; covering centre-back 18 | 0.16 (-0.01) | 0.21 |
| full-back | 37 | archetypes | no clear role 9; defensive full-back 9; overlapping dribbler 7; possession full-back 7; creative full-back 5 | 0.17 (-0.08) | 0.69 |
| full-back | 37 | mixture, k=2 | creative full-back 20; defensive full-back 17 | 0.13 (0.02) | 0.21 |
| midfielder | 66 | archetypes | no clear role 22; deep-lying playmaker 17; creative midfielder 13; ball-winning midfielder 9; box-to-box midfielder 5 | 0.13 (-0.06) | 0.70 |
| midfielder | 66 | mixture, k=2 | ball-winning midfielder 39; creative midfielder 27 | 0.22 (-0.01) | 0.44 |
| winger | 48 | archetypes | inside forward 12; no clear role 11; creative winger 10; hard-working winger 9; direct dribbler 6 | 0.20 (-0.07) | 0.79 |
| winger | 48 | mixture, k=4 | creative winger 32; direct dribbler 8; hard-working winger 7; inside forward 1 | 0.22 (-0.07) | 0.43 |
| centre-forward | 27 | not fitted (under 30 players) | | | |

## Across seasons

Not measurable yet: only one season is in for any group. The backfill takes 2023-24 after the 2024-25 clubs; re-run this report then.

## Sanity check: well-known players (newest season on hand)

Percentiles of his three strongest features against his group; similar = nearest neighbours that season.

| Player | Season | Group | Role (score) | Strongest features | Similar players |
|---|---|---|---|---|---|
| W. Saliba | 2024-25 | centre-back | ball-playing centre-back (1.18) | passes 90%, share of duels won 84%, dribbles completed 78% | C. Bassey, T. Adarabioyo, E. Agbadou |
| J. Gvardiol | 2024-25 | full-back | possession full-back (2.02) | passes 96%, shots 93%, share of duels won 91% | R. James, R. Lewis, K. Walker |
| T. Alexander-Arnold | 2024-25 | full-back | creative full-back (3.32) | chances created 99%, shots 99%, passes 88% | Pedro Porro, M. Gusto, A. Robertson |
| A. Robertson | 2024-25 | full-back | creative full-back (1.29) | chances created 88%, passes 80%, shots 72% | Y. Sugawara, K. Walker, B. White |
| D. Udogie | 2024-25 | full-back | defensive full-back (1.56) | tackles 96%, duels contested 91%, share of duels won 85% | N. Mazraoui, J. Veltman, V. Kristiansen |
| D. Rice | 2024-25 | midfielder | no clear role (0.47) | chances created 86%, shots 75%, passes 61% | C. Eriksen, D. Szoboszlai, J. Hinshelwood |
| M. Caicedo | 2024-25 | midfielder | no clear role (1.77) | share of duels won 96%, passes 92%, tackles 86% | André, T. Partey, R. Gravenberch |
| R. Gravenberch | 2024-25 | midfielder | deep-lying playmaker (1.47) | interceptions 95%, share of duels won 90%, dribbles completed 83% | M. Caicedo, B. Soumaré, S. Tonali |
| M. Ødegaard | 2024-25 | midfielder | creative midfielder (1.86) | chances created 93%, shots 81%, passes 81% | D. Szoboszlai, K. De Bruyne, Andreas Pereira |
| C. Palmer | 2024-25 | midfielder | creative midfielder (2.79) | shots 99%, chances created 98%, dribbles completed 92% | E. Eze, M. O&apos;Riley, J. Kluivert |
| Mohamed Salah | 2024-25 | winger | inside forward (3.61) | shots 99%, goals 99%, chances created 93% | C. Gakpo, L. Díaz, H. Barnes |
| B. Saka | 2024-25 | winger | creative winger (2.74) | chances created 99%, fouls won 91%, shots 84% | D. Kulusevski, D. Brooks, L. Díaz |
| J. Doku | 2024-25 | winger | direct dribbler (5.21) | dribbles completed 99%, duels contested 97%, passes 95% | M. Kudus, E. Nwaneri, Sávio |
| E. Haaland | 2024-25 | centre-forward | penalty-box striker (2.99) |  | A. Isak, J. Mateta, N. Jackson |
| A. Isak | 2024-25 | centre-forward | penalty-box striker (1.89) |  | E. Haaland, Omar Marmoush, J. Mateta |
| J. Mateta | 2024-25 | centre-forward | no clear role (0.3) |  | J. Strand Larsen, R. Højlund, E. Haaland |

## Limitations

- Box-score counts: no positions on the pitch, passing networks or pressing data; the free plan's duels are not split into aerial and ground duels, so "target forward" leans on duels contested and fouls won.
- Defensive counts depend on how much of the ball a team has; they are not possession-adjusted.
- The newest season the free plan has is 2024-25: the site says which season a figure is from.
- Season totals: no in-season split, so season-to-season agreement is the stability test.
