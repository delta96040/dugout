"""Wikidata SPARQL (CC0): club venues + coordinates and current head coaches (birth date, nationality).

Clubs are joined on Transfermarkt club id (P7223), which every PL club carries in both Wikidata and Reep.
"""

from __future__ import annotations

from datetime import date, datetime

from dugout_pipeline.adapters.base import DAY, TIER_DATA, SourceAdapter
from dugout_pipeline.db import upsert

QUERY = """
SELECT ?tm ?club ?venueLabel ?coord ?vstart ?coach ?coachLabel ?start ?sprec ?dob ?countryLabel WHERE {
  VALUES ?tm { %s }
  ?club wdt:P7223 ?tm .
  OPTIONAL {
    # home venue statements that have not ended (clubs keep their old grounds with an end date)
    ?club p:P115 ?vs . ?vs ps:P115 ?venue . FILTER NOT EXISTS { ?vs pq:P582 ?vend }
    OPTIONAL { ?vs pq:P580 ?vstart }
    ?venue wdt:P625 ?coord .
  }
  OPTIONAL {
    ?club p:P286 ?st . ?st ps:P286 ?coach .
    FILTER NOT EXISTS { ?st pq:P582 ?end }
    OPTIONAL { ?st pq:P580 ?start }
    OPTIONAL { ?st pqv:P580 ?sv . ?sv wikibase:timePrecision ?sprec }  # 11 = day, 9 = year
    OPTIONAL { ?coach wdt:P569 ?dob }
    OPTIONAL { ?coach wdt:P27 ?country }
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


TENURES = """
SELECT ?tm ?coach ?coachLabel ?start ?end WHERE {
  VALUES ?tm { %s }
  ?club wdt:P7223 ?tm .
  ?club p:P286 ?st . ?st ps:P286 ?coach .
  OPTIONAL { ?st pq:P580 ?start }
  OPTIONAL { ?st pq:P582 ?end }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


def parse_point(wkt: str) -> tuple[float, float] | None:
    # "Point(-2.96083 53.4308)" -> (lat, lon)
    if not wkt or not wkt.startswith("Point("):
        return None
    lon, lat = wkt[6:-1].split()
    return float(lat), float(lon)


class WikidataAdapter(SourceAdapter):
    name = "wikidata"
    scope = "global"  # one SPARQL query for every club with a Transfermarkt id
    tier = TIER_DATA
    ttl = 7 * DAY
    rate, per = 1, 5.0
    respect_robots = False  # SPARQL endpoint is an API; Wikimedia asks for a descriptive UA, which we send
    base_url = "https://query.wikidata.org/sparql"

    def ingest(self) -> dict[str, int]:
        con = self.ctx.con
        tm = dict(
            con.execute(
                "SELECT external_id, dugout_id FROM entity_ids WHERE entity_type='club' AND provider='transfermarkt'"
            ).fetchall()
        )
        if not tm:
            return {}
        values = " ".join(f'"{k}"' for k in tm)
        res = self.client.request(
            "POST",
            self.base_url,
            data=(f"query={_q(QUERY % values)}").encode(),
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        venues, managers = self.parse(res.json(), tm, res.fetched_at)
        for slug, (name, lat, lon) in venues.items():
            con.execute(
                "UPDATE clubs SET venue = coalesce(?, venue), lat = ?, lon = ? WHERE club_id = ?",
                [name, lat, lon, slug],
            )
        tenures = self.parse_tenures(self.sparql(TENURES % values), tm)
        return {
            "clubs": len(venues),
            "managers": upsert(con, "managers", managers),
            "manager_tenures": upsert(con, "manager_tenures", tenures),
        }

    def sparql(self, query: str) -> dict:
        res = self.client.request(
            "POST",
            self.base_url,
            data=f"query={_q(query)}".encode(),
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        return res.json()

    def parse_tenures(self, doc: dict, tm: dict[str, str]) -> list[dict]:
        rows = []
        for b in doc["results"]["bindings"]:
            slug = tm.get(b["tm"]["value"])
            start, end = _wd_date(b, "start"), _wd_date(b, "end")
            # a spell without a start date cannot be placed in time; an "unknown value" end date is not current
            if not slug or start is None or ("end" in b and end is None):
                continue
            rows.append(
                {
                    "manager_id": "wikidata:" + b["coach"]["value"].rsplit("/", 1)[-1],
                    "name": b["coachLabel"]["value"],
                    "club_id": slug,
                    "start_date": start,
                    "end_date": end,
                    "source": self.name,
                }
            )
        return rows

    def parse(self, doc: dict, tm: dict[str, str], fetched_at: datetime):
        venues, managers, venue_start = {}, {}, {}
        for b in doc["results"]["bindings"]:
            slug = tm.get(b["tm"]["value"])
            if not slug:
                continue
            if "coord" in b and (pt := parse_point(b["coord"]["value"])):
                vstart = b.get("vstart", {}).get("value", "")
                # if several open-ended venue statements remain, the most recently started one wins
                if slug not in venue_start or vstart > venue_start[slug]:
                    venues[slug] = (b.get("venueLabel", {}).get("value"), *pt)
                    venue_start[slug] = vstart
            if "coach" in b:
                qid = b["coach"]["value"].rsplit("/", 1)[-1]
                start = b.get("start", {}).get("value")
                prev = managers.get(slug)
                # several open-ended statements can exist; keep the most recent start
                if prev and prev["start_date"] and start and str(prev["start_date"]) >= start[:10]:
                    continue
                managers[slug] = {
                    "manager_id": f"wikidata:{qid}",
                    "reep_id": None,
                    "name": b["coachLabel"]["value"],
                    "birth_date": _wd_date(b, "dob"),
                    "nationality": b.get("countryLabel", {}).get("value"),
                    "club_id": slug,
                    # a year-only start ("2026") arrives as 1 January: no appointment date rather than a wrong one
                    "start_date": _wd_date(b, "start") if _precision(b) >= DAY_PRECISION else None,
                    "source": self.name,
                    "updated_at": fetched_at,
                }
        return venues, list(managers.values())


def _q(s: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(s)


def _wd_date(binding: dict, key: str) -> date | None:
    """A Wikidata date value, or None when absent or "unknown value" (a blank-node URI, not a date)."""
    value = binding.get(key, {}).get("value", "")
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


DAY_PRECISION = 11  # Wikidata time precision: 11 day, 10 month, 9 year


def _precision(binding: dict) -> int:
    """The start date's precision; statements without one (older answers, tests) count as day-precise."""
    try:
        return int(binding.get("sprec", {}).get("value", DAY_PRECISION))
    except ValueError:
        return DAY_PRECISION
