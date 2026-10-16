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
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from dugout_pipeline.adapters.base import HOUR, TIER_SOCIAL, SourceAdapter
from dugout_pipeline.adapters.news import club_names, news_id, tag_clubs
from dugout_pipeline.db import upsert

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
}


def outlet_of(paragraph_html: str) -> str:
    """The first linked outlet that is not the Guardian itself; the Guardian when it is its own reporting."""
    for href in re.findall(r'href="([^"]+)"', paragraph_html):
        host = urlsplit(href).netloc.lower().removeprefix("www.")
        if not host or host.endswith("theguardian.com"):
            continue
        for domain, name in OUTLETS.items():
            if host == domain or host.endswith("." + domain):
                return name
        return host
    return "The Guardian"


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
            out.append({"index": i, "text": text, "players": players, "outlet": outlet_of(p)})
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
