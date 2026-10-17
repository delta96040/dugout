"""News: Guardian Open Platform (keyed), RSS feeds (keyless), optional self-hosted SearXNG, Trafilatura extraction.

Article text is stored locally only, as evidence for Fact-Check NLI. It is never republished; the site links out.
"""

from __future__ import annotations

import contextlib
import hashlib
import html as html_lib
import logging
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import feedparser

from dugout_pipeline.adapters.base import (
    DAY,
    HOUR,
    TIER_AGGREGATOR,
    TIER_DATA,
    TIER_JOURNALIST,
    SourceAdapter,
)
from dugout_pipeline.clubs import normalise
from dugout_pipeline.db import upsert
from dugout_pipeline.http import FetchError, RobotsDisallowed

log = logging.getLogger(__name__)


def canonical_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def news_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:16]


def tag_clubs(text: str, names: dict[str, str]) -> list[str]:
    """names: normalised club name/alias -> slug. Whole-word match on normalised text, longest names first (a
    match is used up: "Paris Saint-Germain" does not also tag "Paris"). A short one-word name that is also an
    ordinary word ("Nice", "Lens", "Paris") must be capitalised in the text."""
    t = f" {normalise(text)} "
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = set()
    for name, slug in sorted(names.items(), key=lambda kv: -len(kv[0])):
        if f" {name} " not in t:
            continue
        if " " not in name and len(name) <= 5:
            words = re.findall(r"\b" + re.escape(name) + r"\b", folded, re.I)
            if not any(w[0].isupper() for w in words):
                continue
        out.add(slug)
        t = t.replace(f" {name} ", " | ")
    return sorted(out)


def club_names(con, aliases: dict[str, str]) -> dict[str, str]:
    names = {}
    for slug, name in con.execute("SELECT club_id, name FROM clubs").fetchall():
        names[normalise(name)] = slug
    known = set(names.values())
    names.update({a: s for a, s in aliases.items() if s in known and len(a) > 4})
    return names


class GuardianAdapter(SourceAdapter):
    name = "guardian"
    tier = TIER_JOURNALIST
    ttl = 6 * HOUR
    rate, per = 1, 1.0  # developer tier: 60/min, 500/day (headers checked 2026-10-15)
    daily_limit = 450
    key_setting = "guardian_api_key"
    respect_robots = False  # keyed developer API governed by its terms; its robots.txt itself requires a key
    base_url = "https://content.guardianapis.com/"

    def ingest(self, days: int = 3) -> dict[str, int]:
        con = self.ctx.con
        names = club_names(con, self.ctx.clubs.aliases)
        since = (self.ctx.now - timedelta(days=days)).date().isoformat()
        rows: dict[str, dict] = {}
        for slug, name in con.execute(
            "SELECT club_id, name FROM clubs WHERE league = ?", [self.ctx.league]
        ).fetchall():
            res = self.client.get(
                f"{self.base_url}search",
                params={
                    "section": "football",
                    "q": f'"{name}"',
                    "from-date": since,
                    "page-size": 20,
                    "order-by": "newest",
                    "show-fields": "trailText,byline,bodyText",
                    "api-key": self.api_key,
                },
            )
            for item in self.parse(res.json(), names, res.fetched_at):
                rows.setdefault(item["news_id"], item)
                if slug not in rows[item["news_id"]]["club_ids"]:
                    rows[item["news_id"]]["club_ids"].append(slug)
        return {"news_items": upsert(con, "news_items", rows.values())}

    def parse(self, doc: dict[str, Any], names: dict[str, str], fetched_at: datetime) -> list[dict]:
        out = []
        for r in doc["response"]["results"]:
            f = r.get("fields", {})
            text = f"{r['webTitle']} {f.get('trailText', '')}"
            out.append(
                {
                    "news_id": news_id(r["webUrl"]),
                    "source": self.name,
                    "outlet": "The Guardian",
                    "url": r["webUrl"],
                    "title": r["webTitle"],
                    "published_at": datetime.fromisoformat(r["webPublicationDate"].replace("Z", "+00:00")),
                    "summary": _strip(f.get("trailText")),
                    "body_text": f.get("bodyText"),
                    "club_ids": tag_clubs(text, names),
                    "source_tier": self.tier,
                    "fetched_at": fetched_at,
                }
            )
        return out


class RSSAdapter(SourceAdapter):
    name = "rss"
    tier = TIER_JOURNALIST
    ttl = 1 * HOUR
    rate, per = 1, 2.0

    def ingest(self) -> dict[str, int]:
        names = club_names(self.ctx.con, self.ctx.clubs.aliases)
        rows, failed = [], []
        feeds = self.ctx.league_cfg.get("news_feeds", [])
        for feed in feeds:
            try:
                res = self.client.get(feed["url"])
            except (FetchError, RobotsDisallowed) as exc:  # one dead feed must not drop the others
                log.warning("feed %s failed: %s", feed["outlet"], exc)
                failed.append(feed["outlet"])
                continue
            rows += self.parse(res.content, feed, names, res.fetched_at)
        if feeds and len(failed) == len(feeds):
            raise FetchError(f"all RSS feeds failed: {', '.join(failed)}")
        return {
            "news_items": upsert(self.ctx.con, "news_items", {r["news_id"]: r for r in rows}.values()),
            "feeds_failed": len(failed),
        }

    def parse(
        self, content: bytes, feed: dict[str, Any], names: dict[str, str], fetched_at: datetime
    ) -> list[dict]:
        parsed = feedparser.parse(content)
        out = []
        for e in parsed.entries:
            link = e.get("link")
            if not link:
                continue
            published = None
            if e.get("published"):
                try:
                    published = parsedate_to_datetime(e["published"])
                    # feeds mix offsets and naive times; normalise to UTC so the column has one type
                    published = (
                        published.replace(tzinfo=UTC)
                        if published.tzinfo is None
                        else published.astimezone(UTC)
                    )
                except (TypeError, ValueError):
                    published = None
            summary = _strip(e.get("summary"))
            clubs = tag_clubs(f"{e.get('title', '')} {summary or ''}", names)
            if feed.get("club") and feed["club"] not in clubs:
                clubs.append(feed["club"])
            out.append(
                {
                    "news_id": news_id(link),
                    "source": self.name,
                    "outlet": feed["outlet"],
                    "url": link,
                    "title": e.get("title"),
                    "published_at": published,
                    "summary": summary,
                    "body_text": None,
                    "club_ids": clubs,
                    "source_tier": feed.get("tier", self.tier),
                    "fetched_at": fetched_at,
                }
            )
        return out


class SearxngAdapter(SourceAdapter):
    """Optional self-hosted SearXNG (SEARXNG_URL) for Fact-Check evidence retrieval. Off unless configured."""

    name = "searxng"
    tier = TIER_JOURNALIST
    ttl = 6 * HOUR
    rate, per = 1, 2.0
    respect_robots = False  # our own instance

    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        base = self.ctx.settings.searxng_url
        if not base:
            return []
        res = self.client.get(
            base.rstrip("/") + "/search", params={"q": query, "format": "json", "categories": "news"}
        )
        return res.json().get("results", [])[:max_results]

    def ingest(self) -> dict[str, int]:
        return {}  # used on demand by the Fact-Check agent (Phase 3)


class ArticleExtractor(SourceAdapter):
    """Fetches one article page (robots-checked, cached a week) and extracts the main text with Trafilatura."""

    name = "articles"
    tier = TIER_JOURNALIST
    ttl = 7 * DAY
    rate, per = 1, 3.0

    def extract(self, url: str) -> str | None:
        import trafilatura

        res = self.client.get(url)
        return trafilatura.extract(res.text, include_comments=False, include_tables=False)

    def ingest(self) -> dict[str, int]:
        return {}


def _strip(html: str | None) -> str | None:
    if not html:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


# Outlets whose stories club sites reproduce, with the tier the original deserves (default: TIER_DATA).
REPRODUCED_TIERS = {
    "sky sports": TIER_JOURNALIST,
    "sky sports news": TIER_JOURNALIST,
    "bbc sport": TIER_JOURNALIST,
    "the athletic": TIER_JOURNALIST,
    "the guardian": TIER_JOURNALIST,
    "the times": TIER_JOURNALIST,
    "the telegraph": TIER_JOURNALIST,
    "espn": TIER_JOURNALIST,
}
_SQUAD_SLUG = re.compile(
    r"sign|deal|contract|agree|transfer|loan|joins|depart|leave|injur|fitness|update|medical|squad|return",
    re.I,
)
_FULL_STORY = re.compile(r"Full story:\s*(?P<outlet>[A-Z][\w&'. -]{1,40}?)\s*$", re.M)


class ClubNewsAdapter(SourceAdapter):
    """Official club websites (tier 1 for the club's own announcements), discovered through their sitemaps.

    Clubs publish no RSS (checked 2026-10-15), but list every article in a sitemap with `lastmod`. Club sites also
    *reproduce* press stories ("This story has been reproduced ... Full story: Liverpool Echo"): those are stored
    under the original outlet at that outlet's tier, never as official. Configured per club in leagues.yaml
    (`club_sites`); robots.txt is respected.
    """

    name = "club_news"
    tier = 1
    ttl = 6 * HOUR
    rate, per = 1, 3.0  # polite: one article every three seconds
    days = 60
    max_articles = 150  # per club per run; the backlog fills over a few nightly runs

    def sites(self) -> list[dict[str, Any]]:
        """Hand-configured sites (leagues.yaml) plus those found by `dugout setup-club` (club_sites.yaml)."""
        from dugout_pipeline.onboard import load_club_sites

        cfg = list(self.ctx.league_cfg.get("club_sites", []))
        known = {s["club"] for s in cfg}
        return cfg + [s for s in load_club_sites(self.ctx.settings.config_dir) if s["club"] not in known]

    def ingest(self) -> dict[str, int]:
        names = club_names(self.ctx.con, self.ctx.clubs.aliases)
        rows = []
        have = {
            r[0]
            for r in self.ctx.con.execute(
                "SELECT news_id FROM news_items WHERE source = ? AND body_text IS NOT NULL", [self.name]
            ).fetchall()
        }
        for site in self.sites():
            try:
                entries = sitemap_entries(self.client, site["sitemap"])
            except (FetchError, RobotsDisallowed, ValueError) as exc:
                log.warning("club news %s sitemap: %s", site["club"], exc)
                continue
            urls = self.recent(entries, site.get("path_prefix", "/news/"), self.ctx.now, self.days)
            todo = [(u, d) for u, d in urls if news_id(u) not in have][: self.max_articles]
            for url, lastmod in todo:
                try:
                    page = self.client.get(url, ttl=30 * DAY)
                except (FetchError, RobotsDisallowed) as exc:
                    log.warning("club news %s: %s", url, exc)
                    continue
                row = self.parse(page.text, url, lastmod, site, names, page.fetched_at)
                if row:
                    rows.append(row)
        return {"news_items": upsert(self.ctx.con, "news_items", {r["news_id"]: r for r in rows}.values())}

    @staticmethod
    def recent(
        entries: list[tuple[str, datetime | None]], prefix: str, now: datetime, days: int = 60
    ) -> list[tuple[str, datetime]]:
        out = [
            (loc, when)
            for loc, when in entries
            if loc and prefix in loc and "/tag/" not in loc and when and now - when <= timedelta(days=days)
        ]
        # squad news first (signings, contracts, injuries), then everything else; newest first within each
        return sorted(out, key=lambda x: (not _SQUAD_SLUG.search(x[0]), -x[1].timestamp()))

    def parse(
        self,
        html: str,
        url: str,
        lastmod: datetime,
        site: dict[str, Any],
        names: dict[str, str],
        fetched_at: datetime,
    ) -> dict | None:
        import json

        import trafilatura

        raw = trafilatura.extract(html, with_metadata=True, output_format="json", include_comments=False)
        if not raw:
            return None
        doc = json.loads(raw)
        text = doc.get("text") or ""
        outlet, tier = site["outlet"], self.tier
        m = _FULL_STORY.search(text)
        if m or "reproduced" in (doc.get("author") or "").lower():
            outlet = m.group("outlet").strip() if m else "unattributed press report"
            tier = REPRODUCED_TIERS.get(outlet.lower(), TIER_DATA if m else TIER_AGGREGATOR)
        published = lastmod
        if doc.get("date"):
            with contextlib.suppress(ValueError):
                published = datetime.fromisoformat(doc["date"]).replace(tzinfo=UTC)
        # the page <title> is complete; metadata titles are sometimes cut from the description
        m_title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = html_lib.unescape(m_title.group(1)).strip() if m_title else (doc.get("title") or "")
        title = title.removesuffix(f" - {site['outlet']}").strip()
        tagged = tag_clubs(f"{title} {text[:2000]}", names)
        clubs = [site["club"], *(c for c in tagged if c != site["club"])]  # the site's own club comes first
        return {
            "news_id": news_id(url),
            "source": self.name,
            "outlet": outlet,
            "url": url,
            "title": title,
            "published_at": published,
            "summary": None,
            "body_text": text,
            "club_ids": clubs,
            "source_tier": tier,
            "fetched_at": fetched_at,
        }


SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def parse_sitemap(content: bytes) -> tuple[list[str], list[tuple[str, datetime | None]]]:
    """(child sitemaps, (url, lastmod) entries) of one sitemap document."""
    from xml.etree import ElementTree as ET

    root = ET.fromstring(content)
    children = [e.findtext(f"{SITEMAP_NS}loc") or "" for e in root.iter(f"{SITEMAP_NS}sitemap")]
    entries = []
    for e in root.iter(f"{SITEMAP_NS}url"):
        mod = e.findtext(f"{SITEMAP_NS}lastmod")
        try:
            when = datetime.fromisoformat(mod.replace("Z", "+00:00")) if mod else None
        except ValueError:
            when = None
        entries.append((e.findtext(f"{SITEMAP_NS}loc") or "", when))
    return [c for c in children if c], entries


def sitemap_entries(client, url: str, max_fetches: int = 8) -> list[tuple[str, datetime | None]]:
    """All (url, lastmod) entries reachable from a sitemap, following nested sitemap indexes.

    News/article sitemaps are always visited before others, wherever they sit; otherwise in discovery order
    (sites list their newest page first). At most `max_fetches` documents are fetched per call."""
    queue: list[tuple[bool, int, str]] = [(False, 0, url)]
    entries: list[tuple[str, datetime | None]] = []
    order = 0
    for _ in range(max_fetches):
        if not queue:
            break
        queue.sort()
        _, _, next_url = queue.pop(0)
        children, found = parse_sitemap(client.get(next_url).content)
        entries += found
        for c in children:
            order += 1
            queue.append((not re.search(r"news|article", c, re.I), order, c))
    return entries
