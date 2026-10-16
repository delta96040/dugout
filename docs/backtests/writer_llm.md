# Writer benchmark: which LLM writes notes that pass the publish gate

Generated 2026-10-16T17:59:06+00:00. Sections: the six club specialists' dossier questions for liverpool, arsenal. A draft counts only if it passes every gate check; otherwise the section falls back to deterministic templates (numbers are never affected either way).

| Model | Accepted | First try | Drafts with stray numbers | LLM errors | s / section | API calls |
|---|---|---|---|---|---|---|
| groq:openai/gpt-oss-120b | 58% | 50% | 0 | 0 | 41.7 | 18 |
| groq:openai/gpt-oss-20b | 58% | 50% | 0 | 0 | 15.4 | 19 |
| groq:qwen/qwen3.8-27b | 83% | 33% | 0 | 0 | 14.5 | 20 |
| openrouter:poolside/laguna-s-2.1:free | 0% | 0% | 0 | 12 | 36.2 | 48 |

## Sample accepted notes

**groq:openai/gpt-oss-120b** (liverpool, club, placeholders unrendered):

> Liverpool are {{stat:ev_d502a8bcaad692e0|ord}} in the Premier League table. Liverpool have scored {{stat:ev_87e91471026ac201}} league goals and conceded {{stat:ev_8d3e72466be9f644}} league goals. Under Andoni Iraola, teams take {{stat:ev_6cbaf4526c21fb51|pct}} of the threat in matches, average defensive actions per match are {{stat:ev_e20e1dd64de9e656|.0f}}, expected goals for per match are {{stat:ev_e0a220d39bef2f87|.2f}}, expected goals against per match are {{stat:ev_afa5ae2d50de7c98|.2f}}, and share of chance creation is {{stat:ev_f29411c29d9fde6b|pct}}. We recommend focusing on converting the defensive activity into more goal scoring to improve league position.

**groq:openai/gpt-oss-20b** (liverpool, club, placeholders unrendered):

> Liverpool are {{stat:ev_d502a8bcaad692e0|ord}} in the Premier League table, having accumulated {{stat:ev_10e9e4e3480aaf18}} points and scored {{stat:ev_87e91471026ac201}} league goals while conceding {{stat:ev_8d3e72466be9f644}} league goals. Their recent league results read {{stat:ev_748af597b924ffb6}}, and their Elo rating is {{stat:ev_5ab4710328dcd030|int}}. Under Andoni Iraola, the team has taken {{stat:ev_6cbaf4526c21fb51|pct}} of the threat, performed {{stat:ev_e20e1dd64de9e656|.0f}} defensive actions per match, created {{stat:ev_e0a220d39bef2f87|.2f}} expected goals per match, conceded {{stat:ev_afa5ae2d50de7c98|.2f}} expected goals per match, and accounted for {{stat:ev_f29411c29d9fde6b|pct}} of the chance creation in their matches. We recommend focusing on maintaining the defensive activity that has proven more effective than most Premier League sides.

**groq:qwen/qwen3.8-27b** (liverpool, club, placeholders unrendered):

> Liverpool are {{stat:ev_d502a8bcaad692e0|ord}} in the Premier League table with {{stat:ev_10e9e4e3480aaf18}} points, having scored {{stat:ev_87e91471026ac201}} league goals while conceding {{stat:ev_8d3e72466be9f644}}. Andoni Iraola has been Liverpool's head coach since {{stat:ev_c7b9e8c24fdb34a3|date}}, and his tactical profile is based on {{stat:ev_965bee8a10efd593|int}} Premier League matches. Teams under Iraola average {{stat:ev_e20e1dd64de9e656|.0f}} defensive actions per match, which is more active without the ball than almost any Premier League manager's side, while creating {{stat:ev_e0a220d39bef2f87|.2f}} expected goals per match. We recommend focusing on the defensive intensity as a key differentiator for Liverpool's current approach.


## Decision (2026-10-16)

`LLM_CHAIN` = Groq `qwen/qwen3.8-27b` -> Groq `openai/gpt-oss-120b` -> OpenRouter Laguna-S 2.1 -> local `qwen2.5:3b`.
Qwen on Groq had its notes accepted most often (83% of 12 sections) and was the fastest; the sample is small, so
the two gpt-oss models (58%) stay close behind. Groq's free tier allows about 1,000 requests a day per model
(8,000 tokens a minute), enough for every club's dossier each night. Laguna's errors in this run are its 50-a-day
OpenRouter quota running out (the earlier run, before this table, accepted 58%), not its writing.
