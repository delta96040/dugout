"""Fact cards and the toolbox that records them.

A tool never hands the LLM a number. It records the number in the evidence ledger and returns a `Fact`:
the placeholder to copy, what it measures, a qualitative *gloss* computed by code ("well above the league
average"), and a ready-made template sentence used when the LLM is unavailable or fails validation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import duckdb
import yaml
from dugout_pipeline.settings import get_settings
from dugout_verify.ledger import Evidence, EvidenceLedger

REPO = "https://github.com/delta96040/dugout/blob/main"
FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
TM_URL = "https://www.transfermarkt.com/"
MODEL_DOCS = {
    "predictor": f"{REPO}/docs/backtests/predictor.md",
    "lineup": f"{REPO}/docs/backtests/lineup.md",
    "injury": f"{REPO}/docs/backtests/injury.md",
    "roles": f"{REPO}/docs/backtests/roles.md",
    "fingerprint": f"{REPO}/docs/backtests/fingerprint.md",
    "valuation": f"{REPO}/docs/backtests/valuation.md",
    "realism": f"{REPO}/docs/backtests/realism.md",
    "scout": f"{REPO}/packages/models/src/dugout_models/market.py",
    "potential": f"{REPO}/docs/backtests/potential.md",
    "fit": f"{REPO}/docs/backtests/opportunity.md",
    "lookup": f"{REPO}/packages/models/src/dugout_models/lookup.py",
}


@dataclass
class Fact:
    key: str  # short handle shown to the LLM, e.g. F3
    subject: str  # who/what it is about, as named in prose
    label: str  # what is measured
    template: str  # sentence with "{p}" where the value goes (or a complete sentence for text facts)
    eid: str | None = None
    fmt: str | None = None
    gloss: str = ""  # qualitative reading by code; never contains digits
    entity_id: str | None = None
    when: tuple[str, str | None] | None = None  # optional second slot "{d}" (ledger id, format), e.g. a date

    @property
    def placeholder(self) -> str:
        return _ph(self.eid, self.fmt) if self.eid else ""

    @property
    def when_placeholder(self) -> str:
        return _ph(*self.when) if self.when else ""

    def ids(self) -> list[str]:
        return [i for i in (self.eid, self.when[0] if self.when else None) if i]

    def sentence(self) -> str:
        return self.template.replace("{p}", self.placeholder).replace("{d}", self.when_placeholder)

    def card(self) -> str:
        """How the fact is shown to the LLM."""
        value = f"value: {self.placeholder}" if self.eid else "statement"
        gloss = f" — reading: {self.gloss}" if self.gloss else ""
        return f'[{self.key}] {self.subject} | {self.label} | {value}{gloss}\n      e.g. "{self.sentence()}"'


def _ph(eid: str, fmt: str | None) -> str:
    return f"{{{{stat:{eid}|{fmt}}}}}" if fmt else f"{{{{stat:{eid}}}}}"


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


class Toolbox:
    """Warehouse access for tools: records ledger rows and knows how entities are named."""

    def __init__(self, con: duckdb.DuckDBPyConnection, ledger: EvidenceLedger, now: datetime | None = None):
        self.con, self.ledger = con, ledger
        self.now = now or datetime.now(UTC)
        self.season = con.execute("SELECT max(season) FROM matches").fetchone()[0]
        self.club_league: dict[str, str] = dict(con.execute("SELECT club_id, league FROM clubs").fetchall())
        self.names: dict[str, str] = {}
        self.aliases: dict[str, list[str]] = {}
        self._load_names()
        self._load_lookup_names()
        self._n = 0

    # -- naming --------------------------------------------------------------------------------------------
    def _load_names(self) -> None:
        cfg = yaml.safe_load((get_settings().config_dir / "leagues.yaml").read_text(encoding="utf-8"))
        extra: dict[str, list[str]] = {}
        for alias, slug in (cfg.get("club_aliases") or {}).items():
            extra.setdefault(slug, []).append(alias.title())
        for cid, name in self.con.execute("SELECT club_id, name FROM clubs").fetchall():
            self.names[cid] = name
            words = [name, cid.replace("-", " ").title(), *extra.get(cid, [])]
            first = name.split()[0]
            if len(first) > 4 and first not in ("Manchester",):
                words.append(first)  # "Brighton", "Newcastle", "Tottenham"
            self.aliases[cid] = sorted(set(words), key=len, reverse=True)
        for pid, name, web in self.con.execute("SELECT player_id, name, web_name FROM players").fetchall():
            self.names[pid] = name
            self.aliases[pid] = _person_aliases(name, web)
        for mid, name in self.con.execute(
            "SELECT manager_id, any_value(name) FROM managers GROUP BY 1 UNION SELECT manager_id, any_value(name) "
            "FROM manager_tenures GROUP BY 1"
        ).fetchall():
            self.names[mid] = name
            self.aliases[mid] = _person_aliases(name, None)

    def _load_lookup_names(self) -> None:
        """Players outside the Premier League squads (lookup pool) can be named in questions too."""
        try:
            rows = self.con.execute(
                "SELECT entity_id, value_text FROM model_outputs WHERE model = 'lookup' AND metric = 'name'"
            ).fetchall()
        except duckdb.Error:
            return
        for pid, name in rows:
            if name and pid not in self.names:
                self.register(pid, name)

    def name(self, entity_id: str) -> str:
        if entity_id in self.names:
            return self.names[entity_id]
        if entity_id.count(":") == 3:  # match id league:season:home:away
            _, _, h, a = entity_id.split(":")
            return f"{self.name(h)} v {self.name(a)}"
        return entity_id

    def league(self, club: str) -> dict[str, Any]:
        """The club's league block from config/leagues.yaml, with its key under `key`."""
        from dugout_pipeline.clubs import league_config

        key = self.club_league.get(club, "premier_league")
        return {"key": key, **league_config(key)}

    def register(self, entity_id: str, name: str) -> None:
        """Entities outside the warehouse tables (e.g. scouting candidates from other leagues)."""
        self.names.setdefault(entity_id, name)
        self.aliases.setdefault(entity_id, _person_aliases(name, None))

    def match_aliases(self, match_id: str) -> None:
        _, _, h, a = match_id.split(":")
        self.aliases[match_id] = self.aliases.get(h, [h]) + self.aliases.get(a, [a])
        self.names[match_id] = self.name(match_id)

    def key(self) -> str:
        self._n += 1
        return f"F{self._n}"

    # -- recording -----------------------------------------------------------------------------------------
    def sql(
        self,
        sql: str,
        params: list[Any],
        *,
        entity_type: str,
        entity_id: str,
        metric: str,
        source: str,
        tier: int,
        fetched_at: datetime,
        url: str | None,
        unit: str | None = None,
    ) -> tuple[str, Any]:
        return self.ledger.record_sql(
            sql,
            params,
            entity_type=entity_type,
            entity_id=entity_id,
            metric=metric,
            source=source,
            source_tier=tier,
            fetched_at=fetched_at,
            source_url=url,
            unit=unit,
        )

    def model(
        self,
        model: str,
        entity_type: str,
        entity_id: str,
        metric: str,
        context: str = "",
        unit: str | None = None,
    ) -> tuple[str, Any] | None:
        """Cites a published model output (`dugout-models publish`)."""
        where = "model = ? AND entity_type = ? AND entity_id = ? AND metric = ? AND context = ?"
        row = self.con.execute(
            f"SELECT as_of, value_num, value_text FROM model_outputs WHERE {where}",
            [model, entity_type, entity_id, metric, context],
        ).fetchone()
        if row is None or (row[1] is None and row[2] is None):
            return None
        column = "value_num" if row[1] is not None else "value_text"
        return self.ledger.record_sql(
            f"SELECT {column} FROM model_outputs WHERE {where}",
            [model, entity_type, entity_id, metric, context],
            entity_type=entity_type,
            entity_id=entity_id,
            metric=metric,
            source=f"dugout_model:{model}",
            source_tier=3,
            fetched_at=row[0],
            source_url=MODEL_DOCS.get(model),
            unit=unit,
        )

    def latest(
        self, entity_type: str, entity_id: str, metric: str, source: str | None = None
    ) -> Evidence | None:
        """Most recent ledger row an adapter recorded for this entity/metric (re-runnable at the source)."""
        q = "SELECT id FROM evidence_ledger WHERE entity_type = ? AND entity_id = ? AND metric = ?"
        args: list[Any] = [entity_type, entity_id, metric]
        if source:
            q += " AND source = ?"
            args.append(source)
        row = self.con.execute(q + " ORDER BY fetched_at DESC LIMIT 1", args).fetchone()
        return self.ledger.get(row[0]) if row else None


def _person_aliases(name: str, web: str | None) -> list[str]:
    parts = name.split()
    out = {name, ascii_fold(name)}
    if web:
        out |= {web, ascii_fold(web)}
    if len(parts) > 1:
        last = parts[-1]
        if len(last) >= 4:
            out |= {last, ascii_fold(last)}
        if len(parts) > 2:  # "Virgil van Dijk" -> "van Dijk"
            out.add(" ".join(parts[-2:]))
    return sorted({o for o in out if o and not re.search(r"\d", o)}, key=len, reverse=True)


# ---- qualitative readings (code decides the words; the LLM only repeats them) -----------------------------
def z_gloss(z: float, high: str, low: str) -> str:
    """`high`/`low` are comparative phrases, e.g. "more territorial" / "less territorial"."""
    if z >= 1.5:
        return f"{high} than almost any Premier League manager's side"
    if z >= 0.5:
        return f"{high} than average"
    if z <= -1.5:
        return f"{low} than almost any Premier League manager's side"
    if z <= -0.5:
        return f"{low} than average"
    return "close to the league average"


def prob_gloss(p: float) -> str:
    if p >= 0.6:
        return "clear favourite"
    if p >= 0.45:
        return "favourite"
    if p >= 0.33:
        return "slight edge or coin-flip"
    if p >= 0.2:
        return "underdog"
    return "long shot"


def rank_gloss(pos: float, n: int = 20, table: dict[str, int] | None = None) -> str:
    """Words for a league position. Qualification and relegation places come from the league's `table` config;
    a league without one gets plain words (top three, top half...) rather than places we have not checked."""
    if table:
        if pos <= table["champions_league"]:
            return "Champions League places"
        if pos <= table["europe"]:
            return "European places"
        if pos > n - table["relegation"]:
            return "relegation zone"
        return "mid-table"
    if pos <= 3:
        return "top three"
    if pos > n - 3:
        return "bottom three"
    return "top half" if pos <= n / 2 else "bottom half"
