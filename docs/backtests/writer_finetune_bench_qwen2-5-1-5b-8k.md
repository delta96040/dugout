# Writer benchmark: which LLM writes notes that pass the publish gate

Generated 2026-10-19T17:08:34+00:00. Sections: the six club specialists' dossier questions for bayern-munchen, chelsea, elche, elversberg, espanyol, heerenveen, internazionale, manchester-city, monza, olympique-lyonnais, rennes, utrecht. A draft counts only if it passes every gate check; otherwise the section falls back to deterministic templates (numbers are never affected either way).

| Model | Accepted | First try | Drafts with stray numbers | LLM errors | s / section | API calls |
|---|---|---|---|---|---|---|
| local:qwen2.5-1.5b-8k | 32% | 24% | 24 | 0 | 33.2 | 88 |

## Sample accepted notes

**local:qwen2.5-1.5b-8k** (bayern-munchen, scout, placeholders unrendered):

> We recommend Bayern München to strengthen in the centre-forward position. The best option there is {{stat:ev_25b547a242f8eb34}}, with a market value of {{stat:ev_3f7a6a15cbbff64f|eur_m}}. Additionally, they should consider Hugo Ekitiké, who is {{stat:ev_44576b4d0bff6224|int}} years old and has a strong market value of {{stat:ev_8024bad3397f98db|eur_m}}.

