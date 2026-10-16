"""Assembles a run: warehouse connection, ledger, Fact-Check corpus, archived-response resolver, LLM."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb
from dugout_agents.facts import FPL_URL, Toolbox
from dugout_agents.graph import Context
from dugout_agents.llm import LLM
from dugout_pipeline.db import connect
from dugout_pipeline.http import DiskCache
from dugout_pipeline.settings import REPO_ROOT, get_settings
from dugout_verify.factcheck import FactChecker, Passage, chunk
from dugout_verify.ledger import EvidenceLedger
from dugout_verify.nli import NLI, BgeEmbedder, CachedEmbedder, CachedNLI, DebertaNLI, Embedder

log = logging.getLogger(__name__)
NEWS_DAYS = 60


def body_resolver(cache_dir=None):
    """http_json queries re-read the archived response from the HTTP cache (whatever its age)."""
    root = (cache_dir or get_settings().cache_dir) / "http"

    def resolve(query: dict[str, Any]) -> Any:
        cache = DiskCache(root / query["source"])
        hit = cache.get(DiskCache.key("GET", query["url"]), float("inf"), datetime.now(UTC))
        return json.loads(hit[1]) if hit else None

    return resolve


def passages(con: duckdb.DuckDBPyConnection, tb: Toolbox, now: datetime) -> list[Passage]:
    out: list[Passage] = []
    # official: FPL availability flags (the Premier League's own fantasy game data), tier 1
    # (dated by the snapshot: a long-running injury is re-confirmed by every refresh, not stale)
    for pid, status, news, fetched in con.execute(
        """SELECT player_id, status, news, fetched_at FROM injuries WHERE source = 'fpl'
           AND snapshot_date = (SELECT max(snapshot_date) FROM injuries WHERE source = 'fpl')
           AND status <> 'available'"""
    ).fetchall():
        name = tb.name(pid)
        text = f"Fantasy Premier League status for {name}: {status}. {news or ''}".strip()
        out.append(Passage(text, "Fantasy Premier League", 1, FPL_URL, fetched or now, official=True))
    # head coaches: Wikidata and football-data.org are separate sources (tier 3 / tier 2)
    for name, club, source in con.execute(
        "SELECT name, club_id, source FROM managers WHERE club_id IS NOT NULL"
    ).fetchall():
        outlet = "Wikidata" if source == "wikidata" else "football-data.org"
        out.append(
            Passage(
                f"{name} is the head coach of {tb.name(club)}.",
                outlet,
                3 if source == "wikidata" else 2,
                None,
                None,
            )
        )
    # news: headline + summary, and extracted article text in overlapping chunks
    # (a club's own announcement on its official site is an official source; reproduced press stories are not)
    for source, outlet, tier, url, title, summary, body, published in con.execute(
        """SELECT source, outlet, source_tier, url, title, summary, body_text, published_at FROM news_items
           WHERE published_at >= ?""",
        [now - timedelta(days=NEWS_DAYS)],
    ).fetchall():
        official = source == "club_news" and tier == 1
        head = ". ".join(x for x in (title, summary) if x)
        if head:
            out.append(Passage(head, outlet or "unknown", tier, url, published, official=official))
        for part in chunk(body or ""):
            out.append(Passage(part, outlet or "unknown", tier, url, published, official=official))
    return out


def make_context(
    *,
    llm: LLM | None,
    db=None,
    now: datetime | None = None,
    nli: NLI | None = None,
    embedder: Embedder | None = None,
) -> Context:
    now = now or datetime.now(UTC)
    con = connect(db or get_settings().warehouse_path)  # writable: tools record ledger rows
    ledger = EvidenceLedger(con)
    tb = Toolbox(con, ledger, now)
    corpus = passages(con, tb, now)
    # Fact-Check model outputs persist between runs (.cache is kept by CI): unchanged passages are not re-scored
    cache = get_settings().cache_dir / "factcheck"
    nli = nli or CachedNLI(DebertaNLI("base"), cache / "nli.sqlite")
    embedder = embedder or CachedEmbedder(BgeEmbedder(), cache / "embeddings.sqlite")
    checker = FactChecker(corpus, nli, embedder, now=now, aliases=tb.aliases)
    log.info("fact-check corpus: %d passages", len(corpus))
    return Context(tb, ledger, checker, llm, resolve_body=body_resolver(), files_root=REPO_ROOT)
