# Project status (handoff)

Read this first in a new session, then `PLAN.md`, `CLAUDE.md`, `docs/sources.md`, `docs/backtests/README.md`.

## Where we are (2026-10-15)
- **Phase 1 — data foundation: DONE.** Nightly workflow green on GitHub Actions (repo `delta96040/dugout`, public).
  Secrets set (API_FOOTBALL_KEY, FOOTBALL_DATA_ORG_KEY, GUARDIAN_API_KEY, OPENROUTER_API_KEY, HF_TOKEN).
- **Phase 2 — core models: DONE.** Predictor beats Elo on 2024-25 and 2025-26 (all of RPS/log loss/Brier).
  Weak models improved; FootballBERT evaluated and *not adopted* (leakage). See `docs/backtests/README.md`.
- **Phase 3 — agents & verification: DONE (2026-10-16), PR delta96040/dugout#1** (branch
  `phase-3-agents-verification`). Awaiting user review / go-ahead for Phase 4. See docs/agents.md.
  - Verify: Stat Auditor, Fact-Check (DeBERTa-v3-base NLI, docs/backtests/factcheck_nli.md; BGE-small retrieval;
    passages must name every entity in the claim).
  - Models: `dugout-models publish` -> `model_outputs` (predictor, lineup, injury, roles, fingerprint, market,
    potential, fit, lookup). Training frames cached in `.cache/frames/` (warm publish ~2 min).
  - Agents: Sporting Director + 6 club specialists + Player Development + Player Fit; LLM writes with fact codes,
    drafts must pass the gate and state >= 2 facts, else templates. Ollama qwen2.5:3b by default.
  - Club news: `club_news` adapter (official club sitemaps; reproduced press credited to original outlet);
    `dugout setup-club <club> [--build]` (Wikidata P856 -> robots -> sitemap). Liverpool + Arsenal set up.
  - Eval (docs/backtests/agents_eval_*): 0 untraceable numbers everywhere. Held-out 20 questions: routing 94%,
    answer recall 93%, refusals 100%. Tuned 56-question set: 100% / 96% (LLM). The LLM's drafts pass all checks
    for ~20% of sections; templates write the rest.
  - Follow-ups: route future-tense "will ... score" to Match Predictor (held-out miss h05, left unfixed on purpose);
    nightly workflow wiring is Phase 5; LLM quality is the main lever (try a larger open-weight model hosted).
  - User feedback: GK need was a bug (fixed: peer-based needs + announced signings, e.g. Lucca Brughmans from the
    club site); Rio Ngumoha insider claim cannot be a source — sale likelihood labelled as a model estimate.

- **LLM (2026-10-16):** writer benchmark (docs/backtests/writer_llm.md) -> Poolside Laguna-S 2.1 via OpenRouter
  free tier (open weights, OpenMDW licence; uses OPENROUTER_API_KEY), local Ollama qwen2.5:3b as fallback, then
  templates. Laguna's drafts pass the gate 58% vs 17% for qwen. Gemma 4 (Apache) was rate-limited upstream; Inkling
  models are blocked for non-listed apps. Free tier has a small daily quota: LLM for showcase clubs only.
- **Phase 4 — website: DONE (accepted 2026-10-16), merged with PR #1 into main.** See docs/website.md.
  - Done: Next.js static site in apps/web (/, /clubs, /club/[slug], /methods, /scoreboard); R3F scroll story with
    pitch shader, instanced players, camera keyframes, post FX, chalk cursor, SVG fallback; every number is a <Num>
    with provenance; `key_stats` block in dossiers (audited structured facts) feeds the story; prediction_log table
    + `dugout-models scoreboard` -> data/scoreboard.json; banned-pattern script (clean);
    tools/check_site_numbers.py (0 untraceable digits). GSAP dropped (not open source) -> Lenis + own scrub.
  - Lighthouse mobile (2026-10-16, `npx serve` so responses are compressed like a real host; python http.server
    is not and costs ~10-20 points): accessibility, best practices and SEO 100 on every page. Performance in clean
    runs: home ~92, methods ~96, clubs ~95, dossier ~90-92, scoreboard ~90-91 (all >= 90). Local runs are noisy (+/- 10):
    headless Chrome on this laptop often holds the first frame for ~1 s (paints land on whole seconds, like
    occluded-window throttling; `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding` helps
    only sometimes), and then Lighthouse models the LCP as waiting on every script. Measure for real with
    PageSpeed Insights on the deployed site. The story is now server-rendered with a thin client shell (home
    script bootup ~1.5 s -> ~0.8 s at 4x throttle, TBT ~500 -> ~200 ms).
    Scoreboard: one shared provenance card for the table (components/ScoreCard.tsx) instead of ~500 per-number
    cards: ~5,000 -> ~2,200 elements, HTML 912 KB -> 401 KB, TBT ~420 -> ~165 ms; rows use content-visibility.
  - Done 2026-10-16: liverpool + arsenal rebuilt (lineup templated; 0 untraceable, 7/7 published); the other 18
    rebuilt template-only; `names` field (digit-bearing proper names) + `data-name` in the site check; withheld
    counts/reasons marked `data-meta`; `<Num>`/`<MetaNum>` are CSS cards (no per-number hydration); favicon;
    Next static-export prefetch 404s fixed (scripts/flatten-prefetch.mjs); `__pitch` hook removed.
  - **Rebuilding dossiers**: `uv run dugout-agents dossier liverpool arsenal` uses the LLM (Laguna free tier, falls
    back to local qwen2.5:3b on CPU, ~12 min a club). For every other club use
    `uv run dugout-agents --no-llm dossier <clubs...>` (~1.3 min a club).
  - Dev preview: .claude/launch.json "web" (npm --prefix apps/web run dev, port 3000).

- **Phase 5 — ship: DONE (2026-10-16).** PRs #2, #6, #8 merged. Live: https://dugout2.vercel.app (Vercel project
  by the user, Root Directory `apps/web`, deploys main; `vercel.json` skips builds unless the site or its data change).
  - Nightly (02:30 UTC, ~45 min from a cold cache): ingest -> history (Sundays or `history: true`) -> publish ->
    commit `data/prediction_log.csv` -> `dugout-agents dossier --all` -> scoreboard -> site build + checks -> commit
    `data/` to main -> Vercel deploys. First full run 2026-10-16: 20/20 dossiers, 0 untraceable numbers.
  - History job includes `api_football_history` (free-plan injury lists 2022-24, the injury model's training data;
    shares API-Football's cache/budget via `SourceAdapter.client_name`). `data/raw` is in the CI cache (never committed).
  - All workflows share ONE cache path list (`~/.cache/reep`, `~/.cache/huggingface`, `.cache`, `data/warehouse`,
    `data/raw`); changing it means a cold start: run the nightly by hand with `history: true`.
  - setup-uv has no floating major tags since v8: pin exact versions (v10.2.0).
  - OpenRouter free tier: 50 requests/day (resets 00:00 UTC). Local LLM rebuilds the same day use it up and CI then
    templates the showcase clubs too (graceful). Buying 10 credits lifts it to 1000/day (user's call, not free).
  - Lighthouse mobile on the live site: home 91, dossier 90-91, scoreboard 90, clubs 92, methods 93; a11y/BP/SEO 100.
    PageSpeed Insights' keyless API quota was exhausted; re-check there when convenient.
  - README (live badge, GIF in docs/assets/story.gif recorded with puppeteer from the live site), CONTRIBUTING,
    issue/PR templates, starter issues #3 #4 (good first issue) #5 (help wanted).
  - Minor: at 400 px viewport height the story's "scroll" label overlaps chapter five's model note.

- **Post-launch improvements (2026-10-16, user feedback), branch `scouting-and-lineup`:**
  - Lineup: formation family from the manager's observed shape (FPL DEF/FWD counts, last 6 XIs); FPL defenders stay
    in the back line; positions a player starts at 10%+ (transfermarkt-datasets game_lineups) are natural
    (lineup_shape.md: back line right 75% vs 61%). FPL "Has joined ..." players leave their old squad.
  - Realism: destination step-down feature; sale model = gradient boosting with Europe wealth rank (elite young
    regulars 3.7% predicted vs 3.6% actual; logistic said 28.7%). Rejected ideas recorded in realism.md.
  - Shortlists: best (value among regulars), realistic (seller poorer than buyer, sale >= base rate, not an elite
    regular, upgrade on the last regular starter), gem (17-23, projected value). Pool positions from real lineups
    (25%+ of starts); current clubs from the squad file; recent joiners excluded.
  - Rumours: `guardian_rumours` adapter (Rumour Mill via the Guardian API), attributed + fact-checked sentences;
    BBC gossip / FBref not used (terms / robots), see docs/sources.md.
  - Site: general homepage + "Choose your club" (/clubs/), /story/<club>/ for every club with a picker; chapter
    three shows the three lists and rumours.
  - Not done / ideas: (needs on played positions: done 2026-10-19); injury tier not used to filter
    candidates (no injury model for non-PL players); manager formation history (TM games table) unused.

- **Phase 6 (in progress, 2026-10-16), branch `valuation-and-research`:**
  - Merged before this: PR #10 (scouting realism, played positions, rumours, club picker, product voice). Nightly run
    38937666788 was rebuilding all dossiers with it (check it finished green and the site shows "Dream target").
  - Valuation fixed: predicts log(fee / market value) with buyer/seller squad value and PL flags (valuation.md:
    PL buyers MAE 0.516 -> 0.413, PL->PL 0.605 -> 0.350, bias 1.65x -> 1.06x). Lewis Hall fair fee 50.7m -> 60.0m.
    Nationality tested, no effect. `fair_fee(ids, comps, seller_club, buyer_club)`.
  - Research + plan: docs/research/landscape.md (projects, papers, novelty = rumour track record, limitation fixes).
  - **2026-10-17, branch `groq-and-transfers`:**
    - Groq wired in: `LLM_CHAIN` (settings/.env) = groq qwen/qwen3.8-27b -> groq openai/gpt-oss-120b -> OpenRouter
      Laguna -> local qwen2.5:3b; entries without a key are skipped. writer_llm.md: Qwen on Groq 83% accepted (12
      sections), gpt-oss 58%. Groq free: ~1000 req/day per model, 8k tokens/min (a Liverpool LLM dossier ~6.5 min).
      `GROQ_API_KEY` is in local .env; the GitHub secret must be set by the user (`gh secret set GROQ_API_KEY`).
      LLM still writes for showcase clubs only in `--all` (token-per-minute cap); widening it is an option now.
    - Shortlists: 3 picks per list (dream / realistic / gem) for the top need, 1 each for the second; every pick
      has club, age, price (fair fee for this buyer; gems now get one too), contract months, and a "why" built
      from verified numbers (components/Shortlists.tsx, used by the story and the dossier page). Wages: no free
      licensable source (Capology/FBref not open, SoFIFA robots) -> not shown, the site says so.
    - Live site showed "No shortlist passed verification today" because its dossiers predated PR #10's labels
      (nightly 38937666788 rebuilds them).
    - Wikipedia transfer lists (`wikipedia_transfers`, nightly): 14 pages (config/transfer_lists.yaml), 6,568
      moves, 5,329 with a Transfermarkt player id via Wikidata, 181 with a fee (ECB-converted). Uses
      `/wiki/<title>?action=raw` (robots allows; `/w/` API disallowed). Wikidata 502s are tolerated (ids skipped).
      Players who moved this summer are dropped from shortlists and gems (`Market.moved`).
    - Merged as PR #12; nightly 38948656718 (52 min) green, live site shows the three lists.
  - **Faster nightly (branch `faster-nightly`):** (a) LLM fall-through: a chain member told to wait > 10 s
    (`FALLTHROUGH_WAIT`) is skipped until its limit clears; the last member still waits. (b) Fact-Check NLI scores and
    BGE embeddings cached on disk (`.cache/factcheck/*.sqlite`, keyed by pinned model revision + text, entries unused
    30 days dropped; CI keeps `.cache`). Everton dossier: 660 s cold -> 8.4 s warm. Nightly timing before: ingest
    ~21 min (19 of them `club_news` filling its article backlog, max 150 per club per run; shrinks once filled),
    dossiers ~18 min (NLI + embeddings ~95%), publish ~2.5 min.
    - Next: rumour track record (Rumour Mill archive x TM transfers + wiki_transfers as outcome labels), then a
      league picker (needs non-PL leagues: football-data.org free tier has PD/SA/BL1/FL1/DED; FPL is PL-only),
      needs model on played positions, conformal fee ranges, summer-2026 fees as a fresh valuation holdout.
  - **2026-10-17, branch `rumour-track-record` (the novelty from landscape.md):**
    - `rumour_archive` adapter (history job): every Rumour Mill column since 2001-02-16 (5,238 columns, ~105 calls
      once, finished years cached 10 years) -> `rumours` (32,725 paragraphs). Outlets: the column links out for
      ~2% only; `outlet_in_text` adds outlets named in the prose ("says the Mirror") -> ~5,750 credited.
    - `dugout-models rumours` (backtest; publish writes `rumour_links` + model_outputs model='rumours'): club
      gazetteer from TM names + aliases (capitalised runs only), players by normalised name, own club and loan owner
      excluded, labels "joined that club within 3/6/12 months" from TM + wiki_transfers (coverage to 2026-09-02).
    - Data traps found (do not re-learn): transfermarkt-datasets `transfers` is very partial (none for players
      inactive since 2023, gaps for active ones, e.g. Marmoush to Man City missing); `player_valuations.current_club_id`
      is TODAY's club (only `current_club_name` is dated); appearances include national-team games (World Cup).
      Club timelines therefore join transfers + appearances (club games only) + dated valuation names.
      realism.py had the same bug (fixed on branch `realism-club-on-date`, below).
    - Results (rumours.md): 6 months, test 2025-26: Brier 0.135 vs 0.151 (base rate since 2020-21), AUC 0.72,
      calibrated. The column changed in 2020 (fewer items, ~9% -> ~17% came true): era flag. Outlets' shrunk
      records barely differ and hurt on validation -> published as records, not used to score (negative result).
      A leave-one-out outlet encoding leaked labels via the scaler (fixed: out-of-fold by season, capped prior).
    - Site: "Reports like this came true within half a year about one time in N" next to each incoming
      "Linked in the press" item (story chapter three; dossier scout section), Methods row. No number words in
      templates ("six" is a stray number to the Stat Auditor; "one"/"half" are allowed).
    - Live after merge: needs the history job once (nightly with `history: true`, or Sunday) to fill `rumours`.
    - **/rumours/ page (2026-10-19, branch `rumour-page`):** `publish rumours` also writes `data/rumours.json`
      (overall and since-2020-21 record, by season, outlets with 20+ rumours raw and shrunk, the backtest headline);
      the nightly commits it. The page lists the clubs' Fact-Checked rumours with their "one time in N", likeliest
      first (read from the dossiers, so nothing unverified), the outlet table and the season trend. Linked from the
      nav ("Rumours"), story chapter three and Methods. Every number a <Num> citing rumours.md.
  - **2026-10-17, branch `realism-club-on-date` (on top of rumour-track-record):** realism's frames took the club
    from `player_valuations.current_club_id` (today's club): wrong for 45% of players on 1 June 2016, 19% in 2024.
    Now `realism.club_people` (the rumour timeline) gives the club on the day; mobility trains only on players with a
    transfer history (`COVERED_ONLY`, chosen on validation 2023). Mobility AUC 0.764 / Brier 0.0672 vs 0.750 /
    0.0703 on the same test rows; destination 0.733 vs 0.749, the old figure was hindsight (buyer squads counted by
    today's club). Market frames are re-cached (`clubs="dated"`). Live scoring still uses today's club (correct).
  - **Next, in order:** (1) needs model on played positions (done 2026-10-19, below); (2) API-Football injury history for other big

  - **Next, in order:** (1) needs model on played positions; (2) API-Football injury history for other big (done 2026-10-19, below)
    leagues; (3) Championship squads via local refresh; (4) conformal ranges on fair fees; (5) rumour news signal

  - **Next, in order:** (1) needs model on played positions; (2) API-Football injury history for other big
    leagues; (3) Championship squads via local refresh; (4) conformal ranges on fair fees (done 2026-10-19, below); (5) rumour news signal

  - **Next, in order:** (1) needs model on played positions; (2) API-Football injury history for other big
    leagues; (3) Championship squads via local refresh; (4) conformal ranges on fair fees; (5) rumour news signal (done 2026-10-18, below)
    for realism (PRIORS["news_weight_per_point"] is a prior; the archive can now fit it).
  - User wants: product voice everywhere (no "model" wording on the site), realistic targets, all major leagues,
    and STATUS updated before the context fills so a new chat can continue.

- **Leagues (2026-10-17, branch `leagues`):** La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie as blocks in
  config/leagues.yaml; the site asks for the league first (/clubs/ -> /clubs/<league>/), the story picker groups
  clubs by league. 114 clubs (20 + 94), slugs unique across leagues (`slug_suffix` on collision).
  - Pipeline: adapters declare `scope` (league | global) and `requires` (a provider_ids key); `dugout ingest` runs
    every league (`runner.run_leagues`; `--league X` for one). The first league runs the whole job; others run
    their `sources` list (only `football_data_org`). A non-default league failing does not fail the job.
  - football-data.org (free tier: PD, SA, BL1, FL1, DED) makes clubs, squads and fixtures where there is no FPL:
    clubs -> transfermarkt-datasets clubs of the competition, then Reep teams of the country (matching.py; two pins
    in `transfermarkt_clubs`: Inter, Racing Santander); players -> TM id by date of birth + name. 94/94 clubs,
    2,248/2,588 players matched (rest: lower-division/youth players the dataset lacks; own ids, no values).
    Wikidata then gives their head coaches, tenures and venues (fixed: "unknown value" dates).
  - Models: predictor and lineup publish only for leagues listing them under `backtested` (PL). The market model
    takes the buyer's league everywhere (fair fee "if sold within its league", opportunity league gap, typical
    club); needs compare with the six closest squads in the same league; non-PL squads valued at the latest
    transfermarkt-datasets valuation (June 2026, shown with its date); no contracts shown for them (stale).
    valuation.md now has a segment per league buyer (MAE log fee, dugout vs market value): La Liga 0.529 vs
    0.641, Serie A 0.501 vs 0.527, Bundesliga 0.589 vs 0.608, Ligue 1 0.482 vs 0.594, Eredivisie 0.536 vs 0.626.
    Weak spots: Serie A to Serie A 0.520 vs 0.507 (no better than market value); estimates run high for
    Bundesliga and Eredivisie buyers (fee / estimate 0.80 and 0.78). Not tuned per league (yet).
  - Agents: league name in prose, table words from the league's `table` block (PL only; others get plain "top
    three / top half"), next match = fixture date where there is no forecast, league names allowed as names
    ("Ligue 1"), second spellings of a player become aliases (football-data.org "Ndiaye" vs TM "N'Diaye": this
    also unblocked PL shortlist picks from other leagues), "Jr." names match.
  - New-league dossiers (templates): club, next fixture, transfers & values, recruitment (dream / realistic /
    gem), summary; no lineup or team news (no free availability source). 94/94 written, 0 untraceable.
  - Runtime (laptop; CI is roughly 2x faster): football-data.org 6 leagues 3.3 min; publish all leagues 19 min
    warm; 94 template dossiers 40 min (~25 s a club). Nightly: `dossier --all --budget-minutes 60`: PL every
    night, then other clubs oldest dossier first until the budget is spent; the rest go first next night.
    Check the first nightly's step times (2026-10-16 PL-only: ingest 32, publish 5, dossiers 14 min).
  - Gaps filled 2026-10-17 (branch `league-gaps`), see below.

- **Other leagues' gaps (2026-10-17, branch `league-gaps`):**
  - Forecasts: football-data.co.uk SP1/I1/D1/F1/N1 (12 seasons, closing odds; names resolve within the league
    first, then `club_aliases`). Same walk-forward backtest per league (`dugout-models backtest predictor --league
    X`, docs/backtests/predictor_<league>.md). No xG outside the PL, so the stack and Elo are near level: the stack
    beat Elo on every score in both seasons only in La Liga. Config `backtested`: la_liga `predictor`, the other four
    `elo_forecast` (Elo, the simpler model). All far behind closing odds. Each forecast links its league's report.
    prediction_log now logs every league (model `predictor@`/`elo@`); the scoreboard stays PL-only.
  - Predicted XI (`lineup_last_xi`): API-Football lineups of yesterday's finished matches (one call each; free plan
    has no `ids` batching and football-data.org's free tier has no lineups). lineup_recent.md on TM lineups
    2023-26: recency-weighted starts never beat "the last league XI" (76.9% / 76.6% of starters; PL optimiser with
    FPL 78%), so the rule is the last XI with API-Football's listed absentees replaced by the most recent other
    starter. No backfill (date window): a club gets an XI once one league match this season has a lineup.
    API-Football club/player ids missing from Reep are matched by name within the league / the club's squad
    (unique match only) and remembered. Budget: other leagues stop with 30 requests left (`OTHER_LEAGUES_RESERVE`).
  - Team news: API-Football absentee lists (fetched for every league by date anyway), attributed ("According to
    API-Football, X is listed as missing ...", Fact-Check against the same list); BBC Sport European football RSS.
    Not used: Guardian pages for these leagues (theguardian.com robots.txt bars ML/AI uses of its content; the PL
    already uses the Guardian API and RSS: the user should review that). Club tagging: longest names first, short
    one-word names ("Nice", "Lens", "Paris") must be capitalised.
  - Valuation: per-buyer-league offsets learned out of sample (fit before 2022-07, scored 2022-07..2024-06,
    shrunk n/(n+50)), applied to all six leagues: all 0.577 -> 0.575; Bundesliga buyers 0.589 -> 0.563 (fee/estimate
    0.80 -> 0.99); Eredivisie 0.536 -> 0.522; Serie A 0.501 -> 0.498; La Liga 0.529 -> 0.531 and Ligue 1 0.482 ->
    0.485 slightly worse (kept: no picking on the test set). Serie A to Serie A still no better than market value.
  - Next: LLM prose for one showcase club per league if Groq's token cap allows; refresh-values for other leagues
    (local only); xG for other leagues would need a source (API-Football statistics cost a call per match).

- **Site fixes from user review (2026-10-17, branch `site-fixes`):**
  - Chapter four and provenance cards use readable labels (`metricName`, key-stat labels), not metric codes.
  - Provenance cards: no longer inherit the giant position figure's tracking/no-wrap; the dossier header clips
    only sideways; long paths wrap; `Tips.place` nudges cards on screen and opens them upwards near the bottom.
    Swept every card on dossier/clubs/methods/home at desktop and phone width: 0 off-screen or clipped.
  - News: a headline must name the club or one of its players (an Arbeloa/Fulham piece was in Liverpool's
    dossier); question headlines skipped.
  - Chapter two shows the top two needs, each with its reason (depth vs what a squad carries, best option's
    value vs similar squads, veterans), published by `market.needs` (`need_*` outputs).
  - Why him: names the last regular starter he would beat (and his value), his minutes on record, contract
    leverage when short, and how his move odds rank among the candidates.
  - Lineup: `UNUSED_FACTOR = 0.6` for players with no minutes in the club's last five matches (lineup.md: 78.0 ->
    78.2% on 2023-24, 78.25 -> 78.3% on 2024-25). Liverpool: Endo and Chiesa out, Nyoni in; Barcola at CF
    (more starts than Koumas).
  - Merged as PR #17. Nightly fixes after it: PR #18 keeps the cached Reep catalogue when an upstream release is
    broken (2026-10-17 "latest.json has no valid stamp"); `UNUSED_SHARE = 0.1` (a cameo is not a place in the
    side; lineup.md); secrets are stripped of whitespace and a skipped LLM provider is logged with its HTTP status
    (CI used Laguna, not Groq, on 2026-10-17: check the next nightly's log for "LLM ... skipped").
  - Nightly with warm caches: 39 min (club_news backlog filled, Fact-Check cache warm).

- **Needs on played positions (2026-10-19, branch `claude/needs-played-positions-326485`, backlog item 1):**
  - `market.needs` counts depth where players start (transfermarkt-datasets starting XIs): a player counts in each
    group with 25%+ of his starts, weighted by share and renormalised to one player (`positions.group_weights`); under
    10 recorded starts (`GROUP_STARTS`) he counts at his listed position. Best option, last regular starter, veterans'
    share and the six peer squads use the same grouping; `Market.at(club, group)` = everyone who starts there.
    `squad().group` is now the group he starts in most. Shortlist incumbents and the gem bar (lookup.py) follow.
  - `need_depth` is starts-weighted, one decimal ("options add up to 2.6 players"); site copy and agent templates say
    "counted by where they start".
  - needs.md (`dugout-models backtest needs`): top two change at 11 of 20 PL clubs. June 2026 needs vs summer
    signings: per need 72% (played) vs 75% (listed), random 74%: no measurable gain (negative result, reported).
    The 1 June squads are rebuilt from today's squad file + wiki_transfers.
  - `data/raw` in the main checkout was found empty on 2026-10-18 ~22:00 and only `transfermarkt/` came back
    (re-downloaded 22:05); other raw sources need a re-fetch before jobs that use them.

- **Championship squads (2026-10-19, branch `claude/championship-squads-4b1942`):** a shortlist candidate pool,
  not a league with pages.
  - Why no pages: football-data.org's free tier has no Championship (ELC is paid), FPL is PL-only and the
    API-Football free plan has no current season, so there are no free fixtures, standings or lineups to build a
    dossier from. Config `candidate_pools.championship` (GB2) sits outside the league blocks: no ingest, no pages.
  - `uv run dugout refresh-values --pool championship` (local only, refuses when `CI` is set; `transfermarkt_pool`
    shares the Transfermarkt cache and 6 s rate limit; robots.txt re-checked 2026-10-18: `*` allowed, only `wget`
    barred): 1 competition page for the season's clubs, then 1 squad page per club. Run 2026-10-18: 24 clubs, 666
    players, 652 values (EUR 2.40bn), 606 contract ends, 666 positions, 663 Reep ids. Written to
    `data/reference/transfermarkt_squads.csv` with new columns `club_name`, `position`, `comp` (PL rows: comp GB1,
    position blank until the next PL refresh).
  - Models: `Market` adds pool players the dataset lacks (143) to the players table, gives pool players the squad
    file's club, league and contract, and builds live club leagues from the squads (relegated West Ham, Wolves and
    Burnley are GB2 today; training frames keep the dataset's clubs). Shortlists and gems accept pool players;
    pool clubs get no needs, lists or per-player outputs (`Market.buying_clubs`). Values: the squad file's, dated
    by the scrape (ledger rows via `transfermarkt_file`).
  - Side fix: promoted Coventry and Hull were missing from the dataset's clubs table, so the realism model never
    saw them as buyers (no realistic list; Coventry no dream list either). Coventry now gets both for its top need.
  - Minutes (approved by the parent session): the same refresh also fetches 1 performance page per club
    (`/-/leistungsdaten/verein/<id>/reldata/GB2%262025/plus/1`, robots re-checked for that path): 2025-26 league
    apps, goals, assists and minutes -> `data/reference/transfermarkt_minutes.csv` (592 rows from the 18 clubs that
    were in the Championship then; source URL and fetch date per row; `transfermarkt_file` writes a
    `league_minutes` ledger row each). 353 of the 666 pool players have them (227 with 1,200+); the relegated
    clubs' players already had 2025-26 PL minutes in the dataset; promoted clubs' League One minutes are missing.
  - `market.pool_appearances` spreads each player's league games evenly over the season's matchdays
    (`stats_season` in config) as appearance rows, added after the valuation fit so no training frame changes;
    minutes in the past year, share of the club's games, goals/assists per 90 (ranked within the Championship) and
    last season's minutes for the playing-time model then work as for other leagues.
  - Shortlists cite the figure: the scout section adds "league minutes last season (Transfermarkt)" from the
    ledger, shown on the shortlist card (components/Shortlists.tsx) with the page and date in its provenance card.
  - `publish market` (PL clubs, local warehouse): 12 of 558 list slots are Championship players (7 players), 5 in
    the top three the site shows: Fer Lopez (Wolves) Leeds dream list, Mateus Mane (Wolves) gem for Brighton, Isaac
    Price (West Brom) Leeds realistic list, Viktor Johansson (Stoke) realistic goalkeeper for Everton and Ipswich.
    Before the minutes: 5 slots (3 players). A dtype bug of this branch (club ids as "1234.0") had briefly inflated
    an earlier count to 11 by knocking first-tier sellers out; fixed and tested.
  - Note: the main checkout's `data/raw` and `.cache` were found empty on 2026-10-18 (re-downloaded the
    transfermarkt-datasets tables; other raw data and caches are still cold).

- **Injury risk for the other leagues (2026-10-19, branch `claude/injury-other-leagues-df66b0`, backlog item 2):**
  - `api_football_history` now runs per league (listed under the five leagues' `sources`): one call per
    league-season (2022-24), cached for a year, files `data/raw/api_football/injuries_<league>_<season>.parquet` (PL
    keeps `injuries_<season>.parquet`). It reads the account's real count from `status` (not counted by API-Football;
    `DailyBudget.observe`), stops while 40 of the 100 requests are left (`HISTORY_RESERVE`), resumes from the cache
    and logs seasons pending + requests left. The whole backfill is 18 calls: done in one run on 2026-10-18.
  - Players -> Transfermarkt: Reep bridge, else the name within the club's squad that season (TM appearances; the
    lists carry no date of birth, so no DOB match). Absentees matched: PL 97-99%, La Liga 99%, Serie A 98-99%,
    Bundesliga 99%, Ligue 1 92-93%, Eredivisie 95-97% (per season); 25 of 25 name matches checked by eye were right.
  - injury.md: same label/split per league, at-risk rows = TM appearances outside the PL. History-only pooled model
    (age, position, record; no current minutes exist outside the PL) test AUC / lift: La Liga 0.631 / 2.09x, Serie A
    0.633 / 1.93x, Bundesliga 0.585 / 1.40x, Ligue 1 0.623 / 1.82x, Eredivisie 0.608 / 1.93x. Scored when the
    validation AUC (train 22-23, rank 23-24) >= 0.55 with 50+ absences: Eredivisie failed (0.538) and stays unrated
    though its test came out fine (rule set before the test; revisit with more seasons). PL numbers unchanged.
  - Live: `injury_risk_other` (publish `injury`) rates current squad players of scored leagues by percentile within
    their league, context `record`; record = 2022-24 lists + API-Football's nightly absentee lists. Caveat: the
    free plan has no 2025-26 lists, so it says who has been injury-prone, not who is hurt now.
  - Shortlists: every pick with a tier shows "injury risk <tier>" (PL: load + record; others: record), with a
    footnote naming rated / unrated leagues (from injury.json). Flag only, no filter: AUC ~0.6 is too weak to drop
    players on. Methods page lists the per-league AUCs.
  - Data loss found 2026-10-18: the main checkout's `data/raw` and `.cache` were empty (a worktree removal deleting
    through a junction?). Refilled `data/raw/transfermarkt` and `data/raw/api_football`; HTTP caches for other
    sources are cold. Remove junctions with `cmd /c rmdir` before deleting a worktree.

- **Fee ranges (2026-10-19, branch `claude/conformal-fee-ranges-372499`; backlog item 4):**
  - Every fair fee has an 80% likely range: split conformal, calibrated on the two years before the training cutoff
    (the same early model and window as the league offsets), equal tails, separately per market-value band
    (`valuation.BANDS`: under €2m, €2-5m, €5-15m, €15m+). Method chosen on validation 2023-24 (smallest worst
    band gap): pooled, by buyer league, three and five bands were worse. The old range (quantiles of training
    errors) held only 76% of test fees.
  - valuation.md: test 2024-07..2026-09 82.9% inside (slightly conservative: the calibration model is older), each
    band 81-90%; PL buyers 86%; too narrow for La Liga to La Liga (73%) and Ligue 1 to Ligue 1 (74%, misses above).
    Fresh holdout (summer-2026 `wiki_transfers` fees the dataset lacks, 118 rows, mostly English pages): 76% inside,
    misses mostly above; MAE 0.433 vs market value 0.482.
  - Published as `fair_fee_low` / `fair_fee_high` (model `valuation` for squads, `scout` with the pick's context for
    shortlists and gems), rounded outwards to €0.1m, skipped when the low end rounds to zero. Facts carry the range
    in `{lo}`/`{hi}` slots (LLM codes `[F3lo]`/`[F3hi]`); key stats label them "likely low/high end of the ...".
    Site: shortlist "Price €47.0m (likely €38.0m–€58.0m)", dossier prose "(likely between ... and ...)", Methods row.
  - `calibrate` now drops rows with no market value from the calibration window (they gave infinite errors).

- **Realism news signal (2026-10-18, branch `claude/realism-news-signal-654aee`, backlog item 5):**
  - The hand-set news prior (`PRIORS["news_weight_per_point"]`, never used live) is gone. `realism.player_news` /
    `pair_news` count Rumour Mill reports (`rumour_links`) strictly before the snapshot date (1 June in training,
    today live): reports naming the player, clubs named, reports naming this club and their share, 90 days / a year.
  - Chosen on validation 2023 (realism.md): destination gets `news_club_365` + `news_club_share_365` (test AUC
    0.733 -> 0.737, top-1 47.2% -> 47.5%, log loss 0.381 -> 0.378; moves whose buyer was named: top-1 67% -> 87%,
    but only 30 of 2,004 test moves). Mobility: every news set slightly worse on validation -> not used (negative).
  - Since 2020 the Rumour Mill runs only in windows (nothing March-May), so the 90-day windows are empty: a year.
  - Live: `market()` loads `rumour_links` (publish now runs `rumours` before `market`); news columns are joined
    after the cached frames, so the frame cache keys did not change. Shortlist rows `press_reports` (best and
    realistic) and `press_lift` (realistic, shown when >= 1.1): "The Guardian's Rumour Mill has linked him with X
    in N reports over the past year, which makes this move N times as likely as it would be without them."
  - Trap: the main checkout's warehouse had an empty `rumours` table (history job never ran locally) and `.cache`
    was empty; `dugout ingest --job history --only rumour_archive` (~105 Guardian calls, 2.5 min) fills it.

- **Fine-tuned writer (2026-10-20, branch `writer-finetune`):** docs/backtests/writer_finetune.md (résumé summary on top).
  - `dugout-agents finetune-data`: production writer prompts (5 written sections, director summary, player
    questions; 2,448 for 102 train/val clubs, 12 test clubs held out by league) -> teacher notes kept only if they
    pass the writer checks and `graph.gate` whole; targets keep fact codes, no digits. Online teacher (Groq chain) or
    offline: `--export-prompts` (with fact cards) -> `tools/finetune/teach.py` on Kaggle -> `--import-replies`.
  - Teacher: Qwen3.8-27B. Groq caps it at 200k tokens/day (~80 requests), so 658 of the 745 notes came from the same
    Apache-2.0 weights (ggml-org Q4_K_M, pinned revision) on llama.cpp b11330 across Kaggle's 2x T4 (7 h, 2,998
    samples, best of two that passes). 705 train / 40 val.
  - LoRA r16 on Qwen2.5-1.5B-Instruct, 3 epochs, one T4, 55 min (`tools/finetune/train.py`, pinned, seed 7); val loss
    0.761 -> 0.235; q4_K_M GGUF built in the kernel. Public notebooks dhruv9604/dugout-writer-lora-1-5b and
    dhruv9604/dugout-writer-teacher; datasets private (headlines). Model: huggingface.co/dhrxvie/dugout-writer-qwen2.5-1.5b.
  - bench-writer on the 12 held-out clubs (50 notes): fine-tune 96% accepted / 82% first try / 0 stray-number drafts /
    19 s on CPU; Groq Qwen 66% / 40% / 1 / 14 s (`--patient`, 12 h of rate-limit waits excluded); qwen2.5:3b 44% / 20%
    / 10 / 62 s; base 1.5B 32% / 24% / 24 / 33 s. bench-writer now applies scout's writer_facts cap and skips the
    lineup (as production).
  - `LLM_CHAIN` default: `local:dugout-writer-1.5b` before `local:qwen2.5:3b` (a model not pulled -> 404 -> next).
    Install: download the GGUF from the Hub, `uv run python tools/finetune/ollama_create.py <gguf>`.
  - Site: How it works has a "Fine-tuned writer" row (numbers from docs/backtests/writer_finetune.json).
  - Found, not fixed (follow-ups): (a) the gate passes notes that copy the fact-card layout ("Facts: - … (code …)"),
    4 of the 3B's 22 accepted notes; (b) `squad_market`'s key players outside FPL leagues come from the evidence
    ledger, so a prompt depends on what was built before it in the run; (c) the warehouse copy used had stale
    ledger values for some standings (`rerun` audit failures).

## Key facts discovered (do not re-research)
- API-Football free plan: seasons 2022–2024 only + rolling yesterday→tomorrow date window; 100 req/day.
- Guardian dev key: 500/day, 60/min. football-data.org: 10/min.
- Understat robots.txt disallows all; Sofascore/SoFIFA/ESPN API/FotMob fail robots → not used.
- ClubElo often 502 → we compute our own Elo. Wikidata needs a User-Agent with contact URL (set in settings).
- FPL `code` = Opta id → Reep crosswalk. FPL xG starts GW16 of 2022-23 (earlier zeros = missing).
- Historical xG: vaastav/Fantasy-Premier-League (MIT) via `fpl_history` adapter.
- socceraction can't run on numpy 2 → isolated in `tools/spadl`. Do not add `silly-kicks` (supply-chain risk).
- Windows: PyTensor C compiler broken → NUTS via nutpie (`PYTENSOR_FLAGS=cxx=` set by CLI). No `make`: use `uv run dugout ...`.
- When scripting edits in Python on Windows use `PYTHONUTF8=1` (cp1252 mangles text).
- Groq free tier per model: 1,000 requests/day, 8k tokens/min and **200k tokens/day (rolling)**; the 429 says "tokens
  per day (TPD)". qwen3.8-27b is ~80 writer requests a day.
- Kaggle (CLI 2.2.4, `KAGGLE_API_TOKEN`): "T4 x2" machine, 30 GPU h/week, at most 2 batch GPU sessions (queued ones
  count; no cancel in the CLI). A queued kernel sometimes starts at once when pushed again. Kaggle image: Python
  3.13, torch 2.11 cu128; llama.cpp's ubuntu-cuda-12.8 release binaries run there.
- Ollama 0.40 quantizes only safetensors imports (`--quantize` on a GGUF fails): quantize with llama-quantize first.

## Commands
- `uv run dugout ingest [--job nightly|injuries|history] [--only a,b]`, `uv run dugout status`
- `uv run dugout load-open statsbomb|wyscout|metrica|skillcorner|pff`, `uv run dugout refresh-values` (local only)
- `uv run dugout-models backtest <model|all>`; `uv run pytest`; `uv run ruff check . && uv run ruff format .`

## Open items / ideas
- Lineups appear 20–40 min before kick-off; 4-hourly injuries job misses them → issue #5.
- Brentford venue stale in Wikidata and football-data.org (Griffin Park) — issue #4.
- API-Football 2022–24 fixture-stats backfill (`dugout backfill-api-football`) not scheduled; optional.
