# Agents and verification

How a question becomes a published, verified answer. Code: `packages/agents` (orchestration, tools, writer),
`packages/verify` (ledger, Stat Auditor, Fact-Check).

```
question ─► Sporting Director: route (keywords) ─┬─► Club & Tactics      ─┐
                                                 ├─► Match Predictor      │  each: tools → ledger rows → fact cards
                                                 ├─► Lineup Selector      │        → writer (LLM or template)
                                                 ├─► News & Injury        │
                                                 ├─► Transfer & Valuation │
                                                 └─► Scout               ─┘
                                                          ▼
                                     Sporting Director: synthesis (re-cites specialists' facts)
                                                          ▼
                     publish gate per section: Stat Auditor ✔ and Fact-Check ✔ → render → data/clubs/<slug>.json
```

## The rule: the LLM never writes a number

1. **Tools** query the warehouse (or `model_outputs`, written by `dugout-models publish`) and record every number
   in the **evidence ledger** with a re-runnable query: `sql` (warehouse), `http_json` (path into the archived
   API response) or `file_csv` (cell of a committed file). They return *fact cards*: subject, what is measured, a
   qualitative reading computed by code ("far more active without the ball than almost any Premier League
   manager's side"), and a template sentence.
2. **The writer** shows the LLM the cards with short codes (`[F3]`), never values. Code swaps each code for its
   `{{stat:<ledger id>|format}}` placeholder. A draft is rejected if it contains any digit or number word, an
   unknown code, or a cited number in a sentence that does not name its subject and metric. One retry with the
   errors, then the deterministic template sentences. The site records which sections an LLM wrote.
3. **The LLM** is any OpenAI-compatible endpoint serving an open-weight model. `LLM_CHAIN` lists
   `<provider>:<model>` entries tried in order: Groq's free tier, then OpenRouter's, then a local Ollama model
   (`qwen2.5:3b`). Entries without a key are skipped; the choice is benchmarked in docs/backtests/writer_llm.md.
   Without an LLM every section is written from templates; nothing else changes.

## Stat Auditor (no LLM)

- every placeholder resolves to a ledger row; no malformed placeholder survives;
- no stray number: after masking placeholders and allow-listed proper names (e.g. "TSG 1899 Hoffenheim"), no
  digit, number word ("twelve", "dozen"…) or fraction remains;
- re-run: each cited row's query is executed again and must reproduce the stored value (relative tolerance
  0.5 %);
- cross-source: match goals (FPL vs football-data.org vs API-Football), player goals and minutes (FPL season
  totals vs per-match sums), availability (FPL vs API-Football), fees (Transfermarkt vs transfer records).
  Goal and minute disagreements are errors; availability/fee disagreements are flagged as *disputed*.

Each stat on the site gets a status: `verified` (re-ran and a second source agrees, or re-read from an official
API), `single_source`, `model_output` (dugout's own model; links to its backtest).

## Fact-Check

The draft is split into sentences and each sentence is typed (`claims.py`):

| Type | Check |
|---|---|
| stat | Stat Auditor + **binding**: the ledger row's entity is named in the sentence (or the previous one) and the sentence uses a word that fits the metric. A stat sentence that also asserts an injury/transfer is news-checked too. |
| injury, transfer, result, fact | retrieve passages (entity filter → BGE-small dense ranking) from official data (FPL availability flags, tier 1), head-coach records (Wikidata, football-data.org) and news (title, summary, extracted text); score with DeBERTa-v3 NLI. **Verified** = one official source entails, or ≥ 2 independent tier ≤ 3 sources entail (one vote per ownership group; aggregators dropped; near-duplicate copy merged). **Conflicting** = a tier 1–2 source contradicts. Else **Unverified**. Injury news older than 14 days, transfer news older than 60, is ignored. |
| attributed | "According to BBC Sport, …": that outlet's own text must entail the reported content. |
| analysis | recommendations and judgements ("should", "we recommend"): not fact-checked, published labelled as analysis. |

**Publish gate.** A section with an auditor error or a conflicting claim is withheld. Unverified sentences are
cut and listed (without numbers) under `withheld`. What remains is audited again and rendered.

NLI model choice: `docs/backtests/factcheck_nli.md` (48 hand-labelled football pairs; base and large both make no
false verifications; base is three times faster and is used).

## Player lookup: potential and fit

Two player specialists answer about any player in a first-tier league (about 6 500 players):

- **Player Development** ("How good can X become?"): projected market value in two and four years with a
  10-90% band from the potential model (`docs/backtests/potential.md`), shown with a level in words (compared with
  current Premier League squad values). Market value mixes ability with age, contract and hype; the band is wide.
- **Player Fit** ("How would X fit Liverpool?"): playing-time outlook at the club (opportunity model), rank by value
  among the club's current options at his position, whether the position is a top need, and style similarity to the
  head coach's preferred profile (Premier League players only; descriptive).

## Official club news and setting up another club

`club_news` reads official club sites through their sitemaps (robots.txt respected, one page every three seconds).
A club's own announcements are official (tier 1) evidence. Stories the site reproduces from the press ("Full
story: Liverpool Echo") are credited to the original outlet at its tier. Announced signings who have not joined
yet (e.g. a goalkeeper loaned back for a season) count toward squad needs; women's team and academy news do not.

`uv run dugout setup-club <club> [--build]` finds the club's official site on Wikidata (P856), checks robots.txt,
finds the news section from the sitemap and writes `config/club_sites.yaml`; `--build` then ingests its news,
publishes the models (club-specific fit outputs included) and builds `data/clubs/<slug>.json`. Premier League
clubs only: other leagues need their own data pipeline (FPL covers the Premier League only).

## Evaluation

`uv run dugout-agents eval` runs 56 hand-labelled questions (`packages/agents/src/dugout_agents/eval/questions.yaml`):
routing accuracy, answer recall (labelled entity/metric cited), claim recall, citation rate, untraceable-number
rate (target 0), refusal accuracy on unanswerable questions, and how often the LLM tried to write numbers.
Results: `docs/backtests/agents_eval_templates.md` (no LLM) and `docs/backtests/agents_eval_llm.md`.

## Known limits

- Routing is keyword-based (deterministic and auditable); questions phrased unusually fall back to Club & Tactics
  plus News & Injury.
- Analysis sentences are not fact-checked; they can contain judgements the data does not support. They are always
  labelled as analysis on the site.
- The news corpus is what the RSS feeds and the Guardian API returned in the last 60 days; many true claims stay
  *unverified* for lack of a second independent source, and are cut.
- Transfer-market outputs use transfermarkt-datasets appearances up to June 2026 and current squad values from the
  last local Transfermarkt refresh; the shortlist ranking rule is a transparent heuristic over backtested parts.
