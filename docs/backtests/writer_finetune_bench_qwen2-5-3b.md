# Writer benchmark: which LLM writes notes that pass the publish gate

Generated 2026-10-19T18:02:24+00:00. Sections: the six club specialists' dossier questions for bayern-munchen, chelsea, elche, elversberg, espanyol, heerenveen, internazionale, manchester-city, monza, olympique-lyonnais, rennes, utrecht. A draft counts only if it passes every gate check; otherwise the section falls back to deterministic templates (numbers are never affected either way).

| Model | Accepted | First try | Drafts with stray numbers | LLM errors | s / section | API calls |
|---|---|---|---|---|---|---|
| local:qwen2.5:3b | 44% | 20% | 10 | 0 | 62.5 | 90 |

## Sample accepted notes

**local:qwen2.5:3b** (bayern-munchen, match, placeholders unrendered):

> Facts: - {{stat:ev_33f576ddd32b76e7|date}} Freiburg v Bayern München — kick-off date (code {{stat:ev_33f576ddd32b76e7|date}}). We recommend Bayern München should prepare for a competitive match, considering Freiburg's recent form and their own readiness.

