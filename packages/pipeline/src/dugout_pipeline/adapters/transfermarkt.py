"""Transfermarkt: three pieces.

1. `TransfermarktRefresher` (local only, weekly, `dugout refresh-values`): polite squad-page scraper, one request
   per club every few seconds, robots.txt checked. Writes a cleaned CSV of derived facts (ids, contract expiry,
   market value) to `data/reference/transfermarkt_squads.csv`, which is committed. Never runs in CI.
2. `TransfermarktFileAdapter`: loads that committed CSV in CI/nightly (no scraping) -> market_values, contracts.
3. `TransfermarktDatasetsLoader`: dcaribou/transfermarkt-datasets (CC0, frozen 2026-07-06) for history/training.
"""

from __future__ import annotations

import csv
import gzip
import io
import re
from datetime import date, datetime
from pathlib import Path

from dugout_pipeline.adapters.base import DAY, TIER_DATA, SourceAdapter
from dugout_pipeline.db import upsert
from dugout_pipeline.ids import attach_catalogue
from dugout_pipeline.settings import REPO_ROOT

SQUADS_CSV = REPO_ROOT / "data" / "reference" / "transfermarkt_squads.csv"
TM_DIR = REPO_ROOT / "data" / "raw" / "transfermarkt"  # CC0 dataset tables for model training (git-ignored)
LINEUPS_SINCE = (
    "2023-07-01"  # starting positions from the last three seasons (game_lineups is 3M rows in all)
)
FIELDS = [
    "tm_player_id",
    "player_id",
    "name",
    "tm_club_id",
    "club_id",
    "birth_date",
    "joined",
    "contract_expires",
    "market_value_eur",
    "season",
    "scraped_on",
    "source_url",
]


def parse_money(text: str) -> float | None:
    """'€28.00m' -> 28_000_000, '€800k' -> 800_000, '-' -> None."""
    m = re.search(r"([\d.,]+)\s*(bn|m|k|Th\.|Mio\.)?", text.replace("\xa0", " "))
    if not m or not any(ch.isdigit() for ch in m.group(1)):
        return None
    value = float(m.group(1).replace(",", "."))
    mult = {"bn": 1e9, "m": 1e6, "Mio.": 1e6, "k": 1e3, "Th.": 1e3}.get(m.group(2) or "", 1)
    return round(value * mult)


def parse_tm_date(text: str) -> date | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text) or re.search(r"(\w{3}) (\d{1,2}), (\d{4})", text)
    if not m:
        return None
    if "/" in m.group(0):
        d, mo, y = m.groups()
        return date(int(y), int(mo), int(d))
    return datetime.strptime(m.group(0), "%b %d, %Y").date()


def parse_squad(html: bytes) -> list[dict]:
    import lxml.html

    doc = lxml.html.fromstring(html)
    headers = [th.text_content().strip() for th in doc.xpath('//table[@class="items"]/thead/tr/th')]
    col = {name: i for i, name in enumerate(headers)}
    out = []
    for tr in doc.xpath(
        '//table[@class="items"]/tbody/tr[contains(@class,"odd") or contains(@class,"even")]'
    ):
        tds = tr.xpath("./td")
        link = tr.xpath('.//td[contains(@class,"posrela")]//td[contains(@class,"hauptlink")]//a/@href')
        pid = re.search(r"/spieler/(\d+)", link[0]) if link else None
        if not pid or len(tds) < len(headers):
            continue
        name = (
            tr.xpath('.//td[contains(@class,"posrela")]//td[contains(@class,"hauptlink")]//a')[0]
            .text_content()
            .strip()
        )
        cells = {h: tds[i].text_content().strip() for h, i in col.items()}
        out.append(
            {
                "tm_player_id": pid.group(1),
                "name": name,
                "birth_date": parse_tm_date(cells.get("Date of birth/Age", "")),
                "joined": parse_tm_date(cells.get("Joined", "")),
                "contract_expires": parse_tm_date(cells.get("Contract", "")),
                "market_value_eur": parse_money(cells.get("Market value", "")),
            }
        )
    return out


class TransfermarktRefresher(SourceAdapter):
    name = "transfermarkt"
    tier = TIER_DATA
    ttl = 6 * DAY
    rate, per = 1, 6.0  # one page every 6 s
    local_only = True
    base_url = "https://www.transfermarkt.com/"

    def ingest(self) -> dict[str, int]:
        con, cfg = self.ctx.con, self.ctx.league_cfg
        clubs = con.execute(
            """SELECT e.external_id, e.dugout_id FROM entity_ids e JOIN clubs c ON c.club_id = e.dugout_id
                               WHERE e.entity_type='club' AND e.provider='transfermarkt' AND c.league = ?""",
            [self.ctx.league],
        ).fetchall()
        tm_to_player = dict(
            con.execute(
                "SELECT external_id, dugout_id FROM entity_ids WHERE entity_type='player' AND provider='transfermarkt'"
            ).fetchall()
        )
        rows = []
        for tm_club, slug in clubs:
            res = self.client.get(
                f"{self.base_url}-/kader/verein/{tm_club}/saison_id/{cfg['season_start_year']}/plus/1"
            )
            for r in parse_squad(res.content):
                rows.append(
                    {
                        **r,
                        "player_id": tm_to_player.get(r["tm_player_id"]),
                        "tm_club_id": tm_club,
                        "club_id": slug,
                        "season": cfg["season"],
                        "scraped_on": res.fetched_at.date(),
                        "source_url": res.url,
                    }
                )
        # the file holds every league refreshed so far: replace this league's clubs, keep the others
        mine = {slug for _, slug in clubs}
        kept = [r for r in read_squads_csv(SQUADS_CSV) if r["club_id"] not in mine]
        write_squads_csv(kept + rows, SQUADS_CSV)
        return {"transfermarkt_squads_csv": len(rows)}


def read_squads_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_squads_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["club_id"], r["name"])):
            w.writerow({k: r.get(k) for k in FIELDS})


class TransfermarktFileAdapter(SourceAdapter):
    """Reads the committed CSV; no network."""

    name = "transfermarkt_file"
    scope = "global"  # one committed file for every league
    tier = TIER_DATA

    def ingest(self, path: Path = SQUADS_CSV) -> dict[str, int]:
        if not path.exists():
            return {}
        values, contracts, n_ev = [], [], 0
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                pid = r["player_id"] or f"transfermarkt:{r['tm_player_id']}"
                scraped = date.fromisoformat(r["scraped_on"])
                fetched = datetime.combine(scraped, datetime.min.time()).replace(tzinfo=self.ctx.now.tzinfo)
                if r["market_value_eur"]:
                    values.append(
                        {
                            "player_id": pid,
                            "date": scraped,
                            "value_eur": float(r["market_value_eur"]),
                            "source": "transfermarkt",
                        }
                    )
                    self.ctx.ledger.record(
                        entity_type="player",
                        entity_id=pid,
                        metric="market_value",
                        value=float(r["market_value_eur"]),
                        unit="EUR",
                        source="transfermarkt",
                        source_tier=self.tier,
                        fetched_at=fetched,
                        source_url=r["source_url"],
                        query={
                            "kind": "file_csv",
                            "path": "data/reference/transfermarkt_squads.csv",
                            "key": {"tm_player_id": r["tm_player_id"]},
                            "column": "market_value_eur",
                        },
                    )
                    n_ev += 1
                if r["contract_expires"]:
                    contracts.append(
                        {
                            "player_id": pid,
                            "club_id": r["club_id"],
                            "source": "transfermarkt",
                            "expires": date.fromisoformat(r["contract_expires"]),
                            "fetched_at": fetched,
                        }
                    )
        return {
            "market_values": upsert(self.ctx.con, "market_values", values),
            "contracts": upsert(self.ctx.con, "contracts", contracts),
            "evidence_ledger": n_ev,
        }


class TransfermarktDatasetsLoader(SourceAdapter):
    """Historical transfers & valuations from transfermarkt-datasets (CC0). Frozen at 2026-07-06."""

    name = "transfermarkt_datasets"
    scope = "global"
    tier = TIER_DATA
    ttl = 30 * DAY
    rate, per = 1, 1.0
    respect_robots = False  # public R2 bucket of a CC0 dataset
    base_url = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/"

    def table(self, name: str) -> list[dict]:
        res = self.client.get(f"{self.base_url}{name}.csv.gz")
        return list(csv.DictReader(io.StringIO(gzip.decompress(res.content).decode("utf-8"))))

    def ingest(self) -> dict[str, int]:
        con = self.ctx.con
        valuations, transfers = self.table("player_valuations"), self.table("transfers")
        con.execute("CREATE OR REPLACE TEMP TABLE _tm_map (tm_id VARCHAR PRIMARY KEY, player_id VARCHAR)")
        tm_ids = {r["player_id"] for r in valuations} | {r["player_id"] for r in transfers}
        if attach_catalogue(con):
            con.execute("""INSERT INTO _tm_map SELECT b.external_id, b.reep_id FROM reep.bridges b
                           WHERE b.provider='transfermarkt' AND b.namespace='spieler'""")
        mapping = dict(con.execute("SELECT tm_id, player_id FROM _tm_map").fetchall())
        pid = lambda tm: mapping.get(tm) or f"transfermarkt:{tm}"  # noqa: E731
        vals = [
            {
                "player_id": pid(r["player_id"]),
                "date": date.fromisoformat(r["date"][:10]),
                "value_eur": float(r["market_value_in_eur"]),
                "source": self.name,
            }
            for r in valuations
            if r.get("market_value_in_eur")
        ]
        trs = [
            {
                "player_id": pid(r["player_id"]),
                "date": date.fromisoformat(r["transfer_date"][:10]),
                "from_club": r.get("from_club_name"),
                "to_club": r.get("to_club_name"),
                "fee_eur": float(r["transfer_fee"]) if r.get("transfer_fee") else None,
                "fee_text": r.get("transfer_fee") or None,
                "kind": None,
                "source": self.name,
            }
            for r in transfers
            if r.get("transfer_date")
        ]
        return {
            "market_values": upsert(con, "market_values", vals),
            "transfers": upsert(con, "transfers", trs),
            "players_mapped": sum(1 for t in tm_ids if t in mapping),
            "players_total": len(tm_ids),
            "parquet_tables": self.save_model_tables(),
        }

    MODEL_TABLES = (
        "players",
        "transfers",
        "player_valuations",
        "appearances",
        "clubs",
        "competitions",
        "game_lineups",
    )

    def save_model_tables(self) -> int:
        """Full CC0 tables the valuation / realism / injury models train on, as Parquet under data/raw/."""
        import pandas as pd

        TM_DIR.mkdir(parents=True, exist_ok=True)
        for name in self.MODEL_TABLES:
            res = self.client.get(f"{self.base_url}{name}.csv.gz")
            df = pd.read_csv(io.BytesIO(gzip.decompress(res.content)), low_memory=False)
            if name == "game_lineups":
                # where players actually started (e.g. a "Centre-Back" who plays right-back): recent XIs only
                df = df.loc[
                    (df.type == "starting_lineup") & (df.date >= LINEUPS_SINCE),
                    ["game_id", "date", "player_id", "club_id", "position"],
                ]
            df.to_parquet(TM_DIR / f"{name}.parquet", index=False)
        return len(self.MODEL_TABLES)
