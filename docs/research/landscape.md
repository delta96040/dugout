# Landscape and novelty (2026-10-16)

What similar open-source projects and recent papers do, where dugout differs, and what new idea to build next.

## Similar open-source projects

| Project | What it does | Data | Numbers checked? |
|---|---|---|---|
| [football-intelligence-ai](https://github.com/shauryashan/football-intelligence-ai) | Agent picks SQL, similarity, clustering, prediction and RAG tools | StatsBomb open data | LLM explains model outputs; no published verification |
| [AI-Football-Scout](https://github.com/Jade-ok/AI-Football-Scout) | Premier League scouting agent that checks its numbers against retrieved data | API data, Gemini | Checks against retrieval; closed model |
| [Scoutable](https://github.com/EyaAzzabi/scoutable) | Player-DNA embeddings, fair-value model, fit x quality x value ranking, grounded RAG | 17 smaller leagues | Cites players; source private |
| [FootyVision](https://github.com/pinthoz/FootyVision) | StatsBomb ETL, similarity, value model, local LLM reports | StatsBomb | No |
| [football-match-analyst](https://github.com/alexandrebertot/football-match-analyst) | Tool-calling agent + LightGBM match predictor | Match data, local Qwen | No |
| [footballscout](https://github.com/simonrl13/footballscout) | 12-month market-value forecaster + agent with news RAG | Transfermarkt values | Leakage-aware ML |
| [football_scout_rag](https://github.com/yotambraun/football_scout_rag) | Transfermarkt + Groq LLM, "hidden gems" via vector search | Transfermarkt scraping | No |

Common data routes: StatsBomb open data, Transfermarkt (often scraped), and the
[soccerdata](https://pypi.org/project/soccerdata/) scraper (FBref, FotMob, Sofascore, WhoScored, Understat), whose
own releases describe CAPTCHA and anti-bot workarounds. dugout uses none of the scraped sources (robots / terms).

## Research

- **Rumour veracity**: [Machine Learning for Predicting Key Factors to Identify Misinformation in Football Transfer
  News](https://www.mdpi.com/2073-431X/13/6/127) (Computers, 2024) predicts whether a rumour comes true from player
  and timing features (GPT-3 extraction, random forest). It does not score the *source*: no outlet-level track record.
- **Undervalued players**: [Objective Mispricing Detection](https://arxiv.org/abs/2603.17687) (arXiv, 2026) models
  expected market value from market history, contracts and transfers, and adds news features; market dynamics
  dominate, news adds small consistent gains. Similar in spirit to dugout's valuation and potential models.
- **LLM hallucination in sport**: [SUMMIR](https://arxiv.org/abs/2604.04947) (arXiv, 2026) measures hallucinated
  sports insights from LLMs; grounded inputs and multi-stage checks are the main mitigations. No published football
  system guarantees that every published number traces to a source; dugout's Stat Auditor does.
- **Fee inflation**: CIES reports Premier League fees rising about 12% a year vs about 8% elsewhere
  ([Inside World Football](https://insideworldfootball.com/?p=139572)). No rigorous study isolates a homegrown/English
  premium; in dugout's own test data nationality adds nothing once club context is known (valuation.md).
- **Conformal prediction** has been used for sport forecasts (e.g. [NCAA win probability](https://arxiv.org/abs/2208.08598))
  but not, as far as found, for player valuation.

## Novelty to build: a rumour track record

Nobody scores football transfer rumours by their **source's track record**. dugout can, from data it already has:

1. The Guardian's Rumour Mill archive: 5,000+ daily columns (Guardian Open Platform, our key), each rumour naming a
   player, a club and usually the outlet it came from.
2. What actually happened: transfermarkt-datasets transfers (to July 2026) plus the latest window (to add, below).
3. Label each historical rumour "happened within N months / not", then estimate each outlet's hit rate (shrunk
   towards the average for outlets with few rumours) and a per-rumour probability (outlet, club wealth gap, player
   age, contract months, window timing).
4. Show it on the site: "Linked in the press: Liverpool and Lamine Camara · sources like this were right 23% of the
   time", backtested like every other model (train on old seasons, test on the latest).

Second, smaller novelty: calibrated ranges (conformal intervals) on fair fees and forecasts, shown next to each
estimate.

## Removing the current limitations

| Limitation | Fix | Needs |
|---|---|---|
| Free LLM quota (50 requests/day) | Add Groq's free tier (open-weight models such as gpt-oss-120b, Llama 3.3 70B; about 1,000 requests/day per model) ahead of OpenRouter | User creates a Groq account and adds `GROQ_API_KEY` (local `.env` + GitHub secret) |
| Rumour Mill only runs in windows | Archive-based track record (above); add BBC Sport RSS gossip headlines (RSS headline + link only) and Guardian transfer news | Code |
| No summer 2026 transfers (dataset frozen July) | Wikipedia "List of ... transfers summer 2026" pages via the MediaWiki API (CC BY-SA), fees parsed | Code |
| Championship, Colombia, Ecuador not in the dataset | Championship squads via the local Transfermarkt refresh (as for the Premier League) | Local run |
| Squad needs use listed positions | Use played positions (positions.py) | Code |
| No injury filter outside the Premier League | API-Football free-plan injury lists for the other big leagues (2022-24, a few calls each) | Code |
