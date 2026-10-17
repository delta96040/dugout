# Data sources

Every source dugout uses, its terms, the free limits **as observed on 2026-10-15**, how we cache it, and what it feeds.
Limits change; re-check before relying on them. Response headers we observed are quoted where available.

Rules that apply to all sources (enforced in `packages/pipeline/src/dugout_pipeline/http.py`):

- One HTTP client for everything: on-disk cache with a per-source TTL, token-bucket rate limit, retries with
  exponential backoff + jitter (429/5xx/timeouts only), and a persistent daily budget where a source has a hard quota.
- **robots.txt is enforced for every web page we fetch.** A 401/403/5xx on `robots.txt` means "disallow all".
  Keyed, documented APIs (Guardian, football-data.org, API-Football) and open-data APIs/buckets are governed by
  their own terms instead; this is declared per adapter (`respect_robots = False`) with a comment saying why.
- Every network fetch writes a `provenance` row (redacted URL, fetch time, status, size, body SHA-256).
  Key numbers also get `evidence_ledger` rows with a re-runnable query, so the Stat Auditor can recompute them.
- API keys travel in headers where the API allows it; any key in a URL is redacted before it is stored or logged.
- Raw responses stay in `.cache/` and `data/warehouse/` (git-ignored). We never redistribute raw data.
  Test fixtures are **trimmed excerpts** (two clubs, a dozen players, a few rows).

## Live and current-season sources

| Source | Key | Terms / licence | Free limit (observed) | Cache TTL | Feeds | Tier |
|---|---|---|---|---|---|---|
| Fantasy Premier League API | no | Unofficial, run by the Premier League; no published terms for the API; be polite | none published; we send ≤ 1 req / 2 s | 2 h (finished gameweeks 30 d) | clubs, squads, availability (status, news, chance of playing), set-piece takers, fixtures, per-gameweek player stats incl. Opta-derived xG/xA | 1 (official) |
| football-data.org v4 | `FOOTBALL_DATA_ORG_KEY` | Free tier "free forever" for 12 competitions incl. PL; attribution appreciated | **10 calls/min** (`x-requests-available-minute`), scores slightly delayed, no player stats | 3 h (teams 24 h) | results, fixtures, standings, head coach, venue/address — second source for cross-checks | 3 |
| API-Football v3 | `API_FOOTBALL_KEY` | api-sports.io terms; free plan | **100 req/day**. Free plan **only allows seasons 2022–2024**; for the current season only a rolling **yesterday→tomorrow** date window (`"Free plans do not have access to this season, try from 2022 to 2024"`) | 1 h (finished fixtures 30 d) | match-day fixtures, confirmed lineups, injuries (date window), per-fixture statistics incl. **xG**; slow backfill of 2022–2024 for training | 3 |
| Guardian Open Platform | `GUARDIAN_API_KEY` | Developer key: non-commercial use, link back to the article; do not republish full text | **500/day, 60/min** (`x-ratelimit-limit-day`, `ratelimit-limit`) | 6 h | news + match reports (body text kept locally as Fact-Check evidence only) | 2 |
| Guardian Rumour Mill (same key) | `GUARDIAN_API_KEY` | As above; the column credits each rumour's original outlet. Shown only as "linked", attributed, in our own words, after Fact-Check | 1 call per run, same budget | 6 h | transfer rumours (incoming and outgoing), tier 5; runs during transfer windows | 5 |
| Rumour Mill archive (`rumour_archive`, same key) | `GUARDIAN_API_KEY` | As above. Every column since 16 February 2001 (5,238), one row per paragraph naming a player; text kept locally only, to find the clubs and label what happened. Never republished | ~105 calls once (50 columns a page), then the current year's pages daily; same budget, stops and resumes when it runs out | finished years: 10 years; current year: 1 day | the rumour track record (`dugout-models rumours`, docs/backtests/rumours.md); weekly `history` job | 5 |
| RSS: BBC Sport, Guardian, Sky Sports, ESPN | no | Publisher feeds for personal/non-commercial reading; we store headline, link, summary only | n/a; ≤ 1 req / 2 s | 1 h | news & rumours for News and Fact-Check agents | 2 |
| Official club websites (`club_news`) | no | Club sites' terms; robots.txt checked per site (liverpoolfc.com: allows all, 2026-10-16); we store text locally as Fact-Check evidence and link out | sitemap + ≤ 1 page / 3 s, ≤ 150 articles per club per run | 6 h (articles 30 d) | the club's own announcements (signings, contracts, injuries). Stories the site *reproduces* from the press are credited to the original outlet | 1 (own announcements); original outlet's tier for reproduced stories |
| SearXNG (self-hosted, optional) | `SEARXNG_URL` | AGPL software you run yourself | your instance | 6 h | on-demand evidence retrieval for Fact-Check | 2 |
| Trafilatura | no | Apache-2.0 library | robots.txt + ≤ 1 page / 3 s | 7 d | article text extraction for Fact-Check | — |
| ClubElo (`api.clubelo.com`) | no | Free; credit clubelo.com | none published; 1 call/day | 12 h | club Elo (baseline + predictor feature) | 3 |
| Open-Meteo | no | **CC-BY 4.0, non-commercial**; attribution required | 600/min, 5 000/h, 10 000/day, 300 000/month | 3 h (archive 30 d) | match-day weather at the stadium | 3 |
| postcodes.io | no | Open source; data under Open Government Licence | none published; we call ≤ 20 times/year | 365 d | stadium coordinates fallback (from the club's postcode) | 3 |
| Wikidata SPARQL | no | **CC0**; descriptive User-Agent required | 60 s query time / min per IP | 7 d | venue + coordinates, current head coach, birth date, nationality | 3 |
| Wikipedia transfer lists (`wikipedia_transfers`) | no | **CC BY-SA 4.0**; credit Wikipedia contributors. Read as wikitext from `/wiki/<title>?action=raw`, which robots.txt allows (the `/w/` API path is disallowed and not used). We store facts (who moved where, when, reported fee), not prose | ≤ 1 page / 2 s, ~15 pages a night (config/transfer_lists.yaml) | 24 h | moves since the transfermarkt-datasets snapshot (summer 2026 onward) in England, Scotland, Spain, Italy, Germany, France, the Netherlands, Belgium, Turkey …; ids via Wikidata (P2446 / P7223) | 3 |
| ECB euro reference rates | no | ECB statistical data: free reuse with attribution ("Source: ECB") | 1 call a night | 24 h | daily GBP/USD/CHF/… per euro, to convert reported fees | 3 |
| Reep Register (`reep`) | no | **CC0** data, MIT SDK | catalogue ~210 MB, synced daily (SHA-256 verified by the SDK) | 24 h | ID crosswalk: Opta ↔ API-Football ↔ Transfermarkt ↔ FBref ↔ Understat ↔ StatsBomb ↔ Wyscout ↔ SkillCorner ↔ ClubElo … | — |
| football-data.co.uk | no | Free CSVs; credit football-data.co.uk | none published; ≤ 1 req / 3 s | 12 h (past seasons 365 d) | historical results + closing odds (Pinnacle / market average) — the benchmark the predictor must beat | 3 |
| Transfermarkt (local refresher) | no | Website; `robots.txt` allows `*` | **local only**, weekly, 1 page / 6 s, 20 pages | 6 d | current market values, contract expiry → cleaned CSV committed to `data/reference/` | 3 |

### ID mapping

FPL's `code` fields are Opta numeric ids (`opta/team_numeric`, `opta/person_numeric`), which the Reep Register
bridges to every other provider. On 2026-10-15: **20/20 clubs** and **656/667 FPL players** resolved to Reep ids.
football-data.org is not in Reep. Premier League clubs are matched by name through `config/leagues.yaml` aliases.
For the other leagues (La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie: no FPL) football-data.org is the source of
clubs, squads and fixtures: a club is matched by name to transfermarkt-datasets' clubs of that competition, then to
Reep's men's teams of the league's country (reserve and youth sides excluded; config `transfermarkt_clubs` pins a
pick), which gives the Transfermarkt and Reep ids; a player is matched to transfermarkt-datasets by date of birth and
name. On 2026-10-17: **94/94 clubs** (two pinned in config) and **2,248/2,588 squad players** matched; the rest
(mostly second-division and youth players the dataset does not cover) keep a `football_data_org:<id>` and get no
value. Values for these leagues are transfermarkt-datasets' latest (June 2026), shown with that date; contracts
are not shown (no current free source; June's would miss summer renewals).
Players without a Reep id keep a provider-scoped id (`opta:<id>`) and are re-resolved on every run.

### Two sources per key stat (for the Stat Auditor)

| Stat | Primary | Independent second source |
|---|---|---|
| Match result / goals | FPL fixtures | football-data.org (+ API-Football on match days) |
| Standings | football-data.org | derived from FPL results |
| Minutes, goals, assists | FPL per-gameweek stats | API-Football lineups/events (date window); FBref basic stats (historical) |
| Team xG | FPL (sum of player xG, Opta) | API-Football fixture statistics |
| Injuries / availability | FPL status + news | API-Football injuries (date window) + news (Guardian/RSS) |
| Transfer fees, values | transfermarkt-datasets / Transfermarkt refresher | news reports (Fact-Check), API-Football transfers (historical) |

## Open datasets (training & backtests, loaded on demand)

| Dataset | Licence / terms | How we load it | Notes |
|---|---|---|---|
| StatsBomb Open Data | StatsBomb open-data licence: free for research, **credit StatsBomb** | `dugout load-open statsbomb` (socceraction → SPADL) | default scope: Premier League 2015/16 |
| Wyscout public dataset (Pappalardo et al., 2019) | CC BY 4.0; cite *Scientific Data* 6:236 | `dugout load-open wyscout` (socceraction `PublicWyscoutLoader`, figshare download) | 2017/18 top-5 leagues, WC 2018, Euro 2016 |
| SkillCorner Open Data | free with attribution; **credit SkillCorner** | `dugout load-open skillcorner` (kloppy) | broadcast tracking |
| Metrica Sports sample data | free sample data | `dugout load-open metrica` (kloppy) | 3 anonymised games |
| PFF FC World Cup 2022 | free with sign-up; **credit PFF**; not redistributable | set `PFF_DATA_DIR`, `dugout load-open pff` | cannot be downloaded in CI |
| transfermarkt-datasets (dcaribou) | **CC0** | `dugout ingest --job history` (public R2 bucket) | **frozen at 2026-07-06** — historical only |
| football-data.co.uk | free | nightly + history job | 2015-16 onward |

## Sources we do **not** use, and why

| Source | Finding (2026-10-15) | Closest free alternative we use instead |
|---|---|---|
| **Understat** | `robots.txt`: `User-agent: * / Disallow: /` | Team/player xG from FPL (Opta-derived) + API-Football fixture xG; shot-level xG from open event data (StatsBomb/Wyscout) for model training |
| Sofascore | `robots.txt` returns **403** to non-browsers | FPL + API-Football |
| SoFIFA | `robots.txt` returns **403** | — (not needed for MVP) |
| ESPN (`site.api.espn.com`) | `robots.txt` returns **403** | football-data.org for results |
| FotMob | `robots.txt` disallows `/api/*` | — |
| WhoScored | robots.txt allows match pages, but the site blocks automated clients and its terms forbid scraping | — |
| FBref advanced stats | removed January 2026 (basic stats only) | — |
| Nominatim (OpenStreetMap) search | `robots.txt` disallows `/search` | Wikidata coordinates, postcodes.io fallback |
| premierleague.com / club sites RSS | no RSS feed (404) | official info via FPL (run by the Premier League) |

## Daily request budget (nightly + 6× injuries runs)

| Source | Nightly | Injuries job (each, ×6/day) | Daily total | Limit |
|---|---|---|---|---|
| API-Football | 3 fixture-date calls + ≤ 10 fixture stats + 2 injuries + lineups on match days ≈ 5–25 | ≈ 5 (cached) | ≤ 60 | 90 (100 minus 10 reserve) |
| football-data.org | 18 (3 per league, 6 leagues; ~3.5 min at 8/min) | 0 | 18 | 10/min |
| Guardian | 20 | 0 | 20 | 500/day |
| FPL | 3 + finished gameweeks (cached 30 d) | 3 | ≈ 25 | none published |
| Others | ≤ 15 each | 0 | small | — |

## Considered and not used (2026-10-16)

- **BBC Sport gossip column**: robots.txt allows it, but the BBC's terms could not be confirmed to allow automated
  extraction or reuse (the terms page renders client-side; the BBC has pursued scrapers). The Guardian's Rumour Mill,
  through its API, covers the same need.
- **FBref** (and soccerdata's other sources: FotMob, Sofascore, WhoScored, Understat): robots.txt behind a Cloudflare
  challenge (403) or disallowing; soccerdata's own releases describe CAPTCHA and anti-bot workarounds.
- **Lineups and positions**: transfermarkt-datasets `game_lineups` (CC0) gives every starter's position up to July
  2026; API-Football's free daily window covers lineups (with formation) from this season on.
