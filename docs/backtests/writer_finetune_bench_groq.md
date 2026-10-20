# Writer benchmark: which LLM writes notes that pass the publish gate

Generated 2026-10-20T05:24:37+00:00. Sections: the six club specialists' dossier questions for bayern-munchen, chelsea, elche, elversberg, espanyol, heerenveen, internazionale, manchester-city, monza, olympique-lyonnais, rennes, utrecht. A draft counts only if it passes every gate check; otherwise the section falls back to deterministic templates (numbers are never affected either way).

| Model | Accepted | First try | Drafts with stray numbers | LLM errors | s / section | API calls |
|---|---|---|---|---|---|---|
| groq:qwen/qwen3.8-27b | 66% | 40% | 1 | 0 | 14.0 | 669 |

## Sample accepted notes

**groq:qwen/qwen3.8-27b** (bayern-munchen, club, placeholders unrendered):

> Bayern München are {{stat:ev_644c31810f1a0031|ord}} in the Bundesliga table with {{stat:ev_e7235b6445a7b95d}} points. The team has scored {{stat:ev_a8495bc121096ee6}} league goals while conceding {{stat:ev_6fae7f815a1c922a}} league goals. Vincent Kompany has been Bayern München's head coach since {{stat:ev_b6f8301866fe4628|date}}. We recommend continuing to support the current tactical setup.

