"""Transfer rumours: the Guardian's daily Rumour Mill column, through the Guardian Open Platform (keyed API).

The column gathers the day's transfer talk and credits each item to the outlet that reported it (a link to the
Sun, the Mirror, L'Equipe ...) or to the Guardian's own reporting. It runs during transfer windows.

Each paragraph that names a player in bold becomes a news item: outlet = the original outlet, tier 5 (rumour),
url = the Guardian column, body_text = the paragraph (kept locally for Fact-Check, never republished). The site
shows rumours only as "linked in the press", attributed, in our own words, and only after Fact-Check confirms the
paragraph says so. The BBC gossip column is not used: its terms could not be confirmed to allow automated use.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from dugout_pipeline.adapters.base import DAY, HOUR, TIER_SOCIAL, SourceAdapter
from dugout_pipeline.adapters.news import club_names, news_id, tag_clubs
from dugout_pipeline.db import upsert
from dugout_pipeline.http import BudgetExceeded

log = logging.getLogger(__name__)

TAG = "football/series/rumourmill"

# where the column's links point -> the outlet to credit
OUTLETS = {
    "thesun.co.uk": "The Sun",
    "mirror.co.uk": "The Mirror",
    "dailymail.co.uk": "Daily Mail",
    "telegraph.co.uk": "The Telegraph",
    "thetimes.com": "The Times",
    "thetimes.co.uk": "The Times",
    "theathletic.com": "The Athletic",
    "nytimes.com": "The Athletic",
    "skysports.com": "Sky Sports",
    "bbc.co.uk": "BBC Sport",
    "bbc.com": "BBC Sport",
    "standard.co.uk": "Evening Standard",
    "independent.co.uk": "The Independent",
    "express.co.uk": "Daily Express",
    "football.london": "football.london",
    "liverpoolecho.co.uk": "Liverpool Echo",
    "manchestereveningnews.co.uk": "Manchester Evening News",
    "chroniclelive.co.uk": "Chronicle Live",
    "marca.com": "Marca",
    "as.com": "AS",
    "mundodeportivo.com": "Mundo Deportivo",
    "sport.es": "Sport",
    "relevo.com": "Relevo",
    "gazzetta.it": "La Gazzetta dello Sport",
    "corrieredellosport.it": "Corriere dello Sport",
    "tuttosport.com": "Tuttosport",
    "calciomercato.com": "Calciomercato",
    "lequipe.fr": "L'Equipe",
    "rmcsport.bfmtv.com": "RMC Sport",
    "bild.de": "Bild",
    "kicker.de": "Kicker",
    "sport1.de": "Sport1",
    "skysport.de": "Sky Germany",
    "record.pt": "Record",
    "ojogo.pt": "O Jogo",
    "abola.pt": "A Bola",
    "x.com": "a reporter on X",
    "twitter.com": "a reporter on X",
    "dailystar.co.uk": "Daily Star",
    "metro.co.uk": "Metro",
    "talksport.com": "talkSPORT",
    "caughtoffside.com": "CaughtOffside",
    "teamtalk.com": "Teamtalk",
    "mirrorfootball.co.uk": "The Mirror",
    "dailyrecord.co.uk": "Daily Record",
    "telegraaf.nl": "De Telegraaf",
}
# links in the column that are jokes or references, not the source of the story
NOT_OUTLETS = (
    "youtube.com",
    "youtu.be",
    "wikipedia.org",
    "instagram.com",
    "amazon.",
    "twimg.com",
    "imdb.com",
    "spotify.com",
    "giphy.com",
    "flickr.com",
    "google.",
    "vimeo.com",
    "facebook.com",
    "tiktok.com",
)

# Outlets the column names in its prose ("says the Mirror"), case-sensitive. Order matters only for overlaps
# (De Telegraaf before the Telegraph, Sky Italia before Sky Sports); the earliest mention in a paragraph wins.
# Each pattern is matched as whole words.
TEXT_OUTLETS: list[tuple[str, str]] = [
    ("De Telegraaf", r"De Telegraaf"),
    ("The Sun", r"[Tt]he (?:Sun|Currant Bun)"),
    ("The Mirror", r"(?:[Tt]he (?:Daily |Sunday )?Mirror|Daily Mirror)"),
    ("Daily Express", r"(?:[Tt]he (?:Daily |Sunday )?Express|Daily Express)"),
    ("Daily Star", r"(?:[Tt]he (?:Daily )?Star|Daily Star)"),
    ("Daily Mail", r"(?:[Tt]he (?:Daily )?Mail|Daily Mail|Mail on Sunday|MailOnline)"),
    ("The Times", r"[Tt]he (?:Sunday )?Times"),
    ("The Telegraph", r"[Tt]he (?:Daily |Sunday )?Telegraph"),
    ("The Independent", r"[Tt]he Independent"),
    ("Evening Standard", r"(?:[Tt]he|Evening) Standard"),
    ("Daily Record", r"Daily Record"),
    ("The Athletic", r"[Tt]he Athletic(?! Bilbao| Club)"),
    ("Sky Italia", r"Sky (?:Italia|Sport Italia)"),
    ("Sky Germany", r"Sky (?:Germany|Deutschland|Sport Germany|Sport Deutschland)"),
    ("Sky Sports", r"Sky Sports?(?: News)?"),
    ("BBC Sport", r"BBC"),
    ("talkSPORT", r"[Tt]alk ?SPORT|TalkSport"),
    ("Teamtalk", r"(?:TEAMtalk|Teamtalk|TeamTalk)"),
    ("Planet Football", r"Planet ?Football"),
    ("Marca", r"Marca"),
    ("AS", r"AS(?! (?:Roma|Rome|Monaco|Saint|St|Nancy|Bari|Livorno|Cannes|Trencin))"),
    ("Mundo Deportivo", r"Mundo Deportivo"),
    ("La Gazzetta dello Sport", r"Gazzetta"),
    ("Corriere dello Sport", r"Corriere dello Sport"),
    ("Corriere della Sera", r"Corriere della Sera"),
    ("Tuttosport", r"Tuttosport"),
    ("Calciomercato", r"Calciomercato"),
    ("Il Messaggero", r"Il Messaggero"),
    ("La Repubblica", r"La Repubblica"),
    ("L'Equipe", r"L['’]?[ÉE]quipe"),
    ("Le Parisien", r"Le Parisien"),
    ("RMC Sport", r"RMC"),
    ("Bild", r"(?:Sport )?Bild"),
    ("Kicker", r"Kicker"),
    ("A Bola", r"A Bola"),
    ("O Jogo", r"O Jogo"),
    ("Fanatik", r"Fanatik"),
]
_TEXT_OUTLETS = [(name, re.compile(r"\b(?:" + rx + r")\b")) for name, rx in TEXT_OUTLETS]


def outlet_of(paragraph_html: str) -> str:
    """The first linked outlet that is not the Guardian itself; the Guardian when it is its own reporting."""
    for href in re.findall(r'href="([^"]+)"', paragraph_html):
        host = urlsplit(href).netloc.lower().removeprefix("www.")
        if not host or host.endswith("theguardian.com") or any(n in host for n in NOT_OUTLETS):
            continue
        for domain, name in OUTLETS.items():
            if host == domain or host.endswith("." + domain):
                return name
        return host
    return "The Guardian"


def outlet_in_text(text: str) -> str | None:
    """The outlet the paragraph names first ("..., reports the Mirror"), if any."""
    hits = [(m.start(), name) for name, rx in _TEXT_OUTLETS if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def rumour_paragraphs(body_html: str) -> list[dict[str, Any]]:
    """Paragraphs that name at least one player (bold, as the column marks them), with outlet and players.
    A paragraph that only uses a surname from the paragraph before inherits that player."""
    out: list[dict[str, Any]] = []
    last: list[str] = []
    for i, p in enumerate(re.findall(r"<p>(.*?)</p>", body_html, flags=re.S)):
        text = _text(p)
        if not text or text.startswith("Related:"):
            continue
        players = []
        for frag in (_text(s).strip(" ,.;:") for s in re.findall(r"<strong>(.*?)</strong>", p, flags=re.S)):
            # one name split over two bold runs ("Darío" + "Osorio") reads as one in the text
            if players and f"{players[-1]} {frag}" in text:
                players[-1] = f"{players[-1]} {frag}"
            elif frag:
                players.append(frag)
        players = [n for n in players if 1 <= len(n.split()) <= 4 and not any(ch.isdigit() for ch in n)]
        if not players:
            players = [n for n in last if n.split()[-1] in text]
        else:
            last = players
        if players:
            outlet = outlet_of(p)
            if (
                outlet == "The Guardian"
            ):  # no outside link: the outlet named in the text, else the column itself
                outlet = outlet_in_text(text) or outlet
            out.append({"index": i, "text": text, "players": players, "outlet": outlet})
    return out


class GuardianRumourMillAdapter(SourceAdapter):
    name = "guardian_rumours"
    client_name = "guardian"  # same key, cache and daily budget as the Guardian news adapter
    tier = TIER_SOCIAL
    ttl = 6 * HOUR
    rate, per = 1, 1.0
    daily_limit = 450
    key_setting = "guardian_api_key"
    respect_robots = False  # keyed developer API governed by its terms
    base_url = "https://content.guardianapis.com/"

    def ingest(self, days: int = 60) -> dict[str, int]:
        con = self.ctx.con
        res = self.client.get(
            f"{self.base_url}search",
            params={
                "tag": TAG,
                "from-date": (self.ctx.now - timedelta(days=days)).date().isoformat(),
                "page-size": 50,
                "order-by": "newest",
                "show-fields": "body",
                "api-key": self.api_key,
            },
        )
        rows = self.parse(res.json(), club_names(con, self.ctx.clubs.aliases), res.fetched_at)
        return {"news_items": upsert(con, "news_items", rows)}

    def parse(self, doc: dict[str, Any], names: dict[str, str], fetched_at: datetime) -> list[dict]:
        out = []
        for r in doc["response"]["results"]:
            published = datetime.fromisoformat(r["webPublicationDate"].replace("Z", "+00:00"))
            for item in rumour_paragraphs(r.get("fields", {}).get("body", "")):
                clubs = tag_clubs(item["text"], names)
                if not clubs:
                    continue
                out.append(
                    {
                        "news_id": f"{news_id(r['webUrl'])}-p{item['index']}",  # one row per rumour
                        "source": self.name,
                        "outlet": item["outlet"],
                        "url": r["webUrl"],
                        "title": "; ".join(item["players"]),  # who the item is about (names only)
                        "published_at": published,
                        "summary": None,
                        "body_text": item["text"],
                        "club_ids": clubs,
                        "source_tier": self.tier,
                        "fetched_at": fetched_at,
                    }
                )
        return out


class RumourArchiveAdapter(SourceAdapter):
    """The Rumour Mill back-catalogue, year by year, into `rumours` (one row per paragraph naming a player).

    The outcome labels and the track record are built from it by `dugout-models rumours`. A finished year never
    changes, so its pages are cached for good; the current year is re-read daily. Shares the Guardian key's daily
    budget: when it runs out the years fetched so far are kept and the next run carries on from the cache.
    """

    name = "rumour_archive"
    client_name = "guardian"
    tier = TIER_SOCIAL
    ttl = 3650 * DAY
    rate, per = 1, 1.0
    daily_limit = 450
    key_setting = "guardian_api_key"
    respect_robots = False  # keyed developer API governed by its terms
    base_url = "https://content.guardianapis.com/"
    first_year = 2001  # the series tag's first column, 16 February 2001 (API, order-by=oldest)
    page_size = 50  # the API's maximum with show-fields
    record_kwargs: ClassVar[dict[str, Any]] = {
        "years": [2016]
    }  # `dugout record rumour_archive`: one year is enough for fixtures

    def ingest(self, years: Iterable[int] | None = None) -> dict[str, int]:
        con = self.ctx.con
        written = 0
        for year in years or range(self.first_year, self.ctx.now.year + 1):
            try:
                rows = self.year(year)
            except BudgetExceeded:
                log.warning("rumour_archive: Guardian budget used up at %s; run again tomorrow", year)
                break
            written += upsert(con, "rumours", rows)
        return {"rumours": written}

    def year(self, year: int) -> list[dict[str, Any]]:
        done = year < self.ctx.now.year
        rows: list[dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            res = self.client.get(
                f"{self.base_url}search",
                params={
                    "tag": TAG,
                    "from-date": f"{year}-01-01",
                    "to-date": f"{year}-12-31",
                    "order-by": "oldest",
                    "page": page,
                    "page-size": self.page_size,
                    "show-fields": "body",
                    "api-key": self.api_key,
                },
                ttl=None if done else DAY,
            )
            doc = res.json()
            pages = int(doc["response"].get("pages") or 1)
            rows += self.parse(doc, res.fetched_at)
            page += 1
        return rows

    def parse(self, doc: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
        out = []
        for r in doc["response"]["results"]:
            published = datetime.fromisoformat(r["webPublicationDate"].replace("Z", "+00:00")).astimezone(UTC)
            for item in rumour_paragraphs(r.get("fields", {}).get("body", "")):
                out.append(
                    {
                        # the same id as the column's paragraph in news_items, so live rumours join their record
                        "rumour_id": f"{news_id(r['webUrl'])}-p{item['index']}",
                        "published_at": published,
                        "url": r["webUrl"],
                        "paragraph": item["index"],
                        "players": item["players"],
                        "outlet": item["outlet"],
                        "body_text": item["text"],
                        "fetched_at": fetched_at,
                    }
                )
        return out
