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
  - Not done / ideas: needs model still groups the squad by primary position only; injury tier not used to filter
    candidates (no injury model for non-PL players); manager formation history (TM games table) unused.

- **Phase 6 (in progress, 2026-10-16), branch `valuation-and-research`:**
  - Merged before this: PR #10 (scouting realism, played positions, rumours, club picker, product voice). Nightly run
    38937666788 was rebuilding all dossiers with it (check it finished green and the site shows "Dream target").
  - Valuation fixed: predicts log(fee / market value) with buyer/seller squad value and PL flags (valuation.md:
    PL buyers MAE 0.516 -> 0.413, PL->PL 0.605 -> 0.350, bias 1.65x -> 1.06x). Lewis Hall fair fee 50.7m -> 60.0m.
    Nationality tested, no effect. `fair_fee(ids, comps, seller_club, buyer_club)`.
  - Research + plan: docs/research/landscape.md (projects, papers, novelty = rumour track record, limitation fixes).
  - **Next, in order:** (1) Wikipedia summer-2026 transfers (MediaWiki API, CC BY-SA) for fees + outcome labels;
    (2) rumour track record from the Guardian Rumour Mill archive (outlet hit rates, per-rumour probability,
    backtested); (3) Groq as first LLM (user must create the account and add GROQ_API_KEY; then `gh secret set`);
    (4) needs model on played positions; (5) API-Football injury history for other big leagues; (6) Championship
    squads via local refresh; (7) conformal ranges on fair fees.
  - User wants: product voice everywhere (no "model" wording on the site), realistic targets, all major leagues,
    and STATUS updated before the context fills so a new chat can continue.

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

## Commands
- `uv run dugout ingest [--job nightly|injuries|history] [--only a,b]`, `uv run dugout status`
- `uv run dugout load-open statsbomb|wyscout|metrica|skillcorner|pff`, `uv run dugout refresh-values` (local only)
- `uv run dugout-models backtest <model|all>`; `uv run pytest`; `uv run ruff check . && uv run ruff format .`

## Open items / ideas
- Lineups appear 20–40 min before kick-off; 4-hourly injuries job misses them → issue #5.
- Brentford venue stale in Wikidata and football-data.org (Griffin Park) — issue #4.
- API-Football 2022–24 fixture-stats backfill (`dugout backfill-api-football`) not scheduled; optional.
