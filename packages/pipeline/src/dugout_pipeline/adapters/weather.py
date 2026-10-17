"""Match-day weather: Open-Meteo (free, CC-BY 4.0, non-commercial) + venue coordinates.

Venue coordinates come from Wikidata when available, else the club's postcode (football-data.org address)
via postcodes.io (free, Open Government Licence). Forecast for upcoming matches, archive for past ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from dugout_pipeline.adapters.base import DAY, HOUR, TIER_DATA, SourceAdapter
from dugout_pipeline.db import upsert

UK_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")


class PostcodesAdapter(SourceAdapter):
    name = "postcodes_io"
    tier = TIER_DATA
    ttl = 365 * DAY
    rate, per = 2, 1.0
    respect_robots = False  # API host has no robots.txt (404 JSON); public open-data API
    base_url = "https://api.postcodes.io/postcodes/"

    def locate(self, address: str) -> tuple[float, float] | None:
        m = UK_POSTCODE.search(address.upper())
        if not m:
            return None
        res = self.client.get(self.base_url + f"{m.group(1)}{m.group(2)}")
        r = res.json().get("result") or {}
        return (r["latitude"], r["longitude"]) if "latitude" in r else None

    def ingest(self) -> dict[str, int]:
        return {}


class OpenMeteoAdapter(SourceAdapter):
    name = "open_meteo"
    tier = TIER_DATA
    ttl = 3 * HOUR
    rate, per = 2, 1.0
    respect_robots = False  # API host; free tier 600/min, 10k/day
    forecast_url = "https://api.open-meteo.com/v1/forecast"
    archive_url = "https://archive-api.open-meteo.com/v1/archive"

    def locate_missing_venues(self) -> int:
        """Fallback for clubs without Wikidata coordinates: geocode the postcode in their address."""
        missing = self.ctx.con.execute(
            "SELECT club_id, address FROM clubs WHERE lat IS NULL AND address IS NOT NULL"
        ).fetchall()
        if not missing:
            return 0
        pc, n = PostcodesAdapter(self.ctx), 0
        try:
            for slug, address in missing:
                if loc := pc.locate(address):
                    self.ctx.con.execute("UPDATE clubs SET lat = ?, lon = ? WHERE club_id = ?", [*loc, slug])
                    n += 1
        finally:
            pc.close()
        return n

    def ingest(self, horizon_days: int = 7, lookback_days: int = 3) -> dict[str, int]:
        con, now = self.ctx.con, self.ctx.now
        located = self.locate_missing_venues()
        rows = []
        matches = con.execute(
            """SELECT m.match_id, m.kickoff_utc, c.lat, c.lon FROM matches m JOIN clubs c ON c.club_id = m.home_club_id
               WHERE c.lat IS NOT NULL AND m.league = ? AND m.kickoff_utc BETWEEN ? AND ?""",
            [self.ctx.league, now - timedelta(days=lookback_days), now + timedelta(days=horizon_days)],
        ).fetchall()
        for mid, ko, lat, lon in matches:
            past = ko < now - timedelta(days=2)
            url = self.archive_url if past else self.forecast_url
            params = {
                "latitude": round(lat, 3),
                "longitude": round(lon, 3),
                "timezone": "UTC",
                "hourly": "temperature_2m,precipitation,wind_speed_10m",
                "start_date": ko.date().isoformat(),
                "end_date": ko.date().isoformat(),
            }
            res = self.client.get(url, params=params, ttl=30 * DAY if past else 3 * HOUR)
            row = self.parse(mid, ko, res.json(), res.fetched_at)
            if row:
                rows.append(row)
        return {"weather": upsert(con, "weather", rows), "clubs_located": located}

    def parse(self, mid: str, kickoff: datetime, doc: dict, fetched_at: datetime) -> dict | None:
        h = doc.get("hourly") or {}
        stamp = kickoff.strftime("%Y-%m-%dT%H:00")
        if stamp not in h.get("time", []):
            return None
        i = h["time"].index(stamp)
        return {
            "match_id": mid,
            "temp_c": h["temperature_2m"][i],
            "precip_mm": h["precipitation"][i],
            "wind_kmh": h["wind_speed_10m"][i],
            "source": self.name,
            "fetched_at": fetched_at,
        }
