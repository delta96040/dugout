"""ClubElo (api.clubelo.com): free CSV of club Elo ratings. One call per day returns every club's rating."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime

from dugout_pipeline.adapters.base import HOUR, TIER_DATA, SourceAdapter
from dugout_pipeline.db import upsert


class ClubEloAdapter(SourceAdapter):
    name = "clubelo"
    requires = "clubelo_country"
    tier = TIER_DATA
    ttl = 12 * HOUR
    rate, per = 1, 2.0
    base_url = "http://api.clubelo.com/"

    def ingest(self, on: date | None = None) -> dict[str, int]:
        self.ctx.load_clubs()
        on = on or self.ctx.now.date()
        res = self.client.get(f"{self.base_url}{on.isoformat()}")
        rows = self.parse(res.text, on, res.fetched_at)
        n = upsert(self.ctx.con, "elo", [{k: v for k, v in r.items() if k != "_raw"} for r in rows])
        for r in rows:
            self.ctx.ledger.record(
                entity_type="club",
                entity_id=r["club_id"],
                metric="elo",
                value=r["elo"],
                unit="Elo points",
                source=self.name,
                source_tier=self.tier,
                fetched_at=res.fetched_at,
                source_url=res.url,
                query={
                    "kind": "http_csv",
                    "source": self.name,
                    "url": res.url,
                    "key": {"Club": r["_raw"]},
                    "column": "Elo",
                },
            )
        return {"elo": n, "evidence_ledger": len(rows)}

    def parse(self, text: str, on: date, fetched_at: datetime) -> list[dict]:
        country = self.ctx.league_cfg["provider_ids"]["clubelo_country"]
        rows = []
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("Country") != country:
                continue
            slug = self.ctx.clubs.by_id("clubelo/team", r["Club"])
            if slug:
                rows.append(
                    {
                        "club_id": slug,
                        "date": on,
                        "elo": float(r["Elo"]),
                        "source": self.name,
                        "_raw": r["Club"],
                    }
                )
        return rows
