"""The single interface every data source implements."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import duckdb
from dugout_verify.ledger import EvidenceLedger

from dugout_pipeline.clubs import ClubResolver, league_config
from dugout_pipeline.db import provenance_sink
from dugout_pipeline.http import CachedClient
from dugout_pipeline.ids import ReepIndex, default_index
from dugout_pipeline.settings import Settings, get_settings

log = logging.getLogger(__name__)

# Source tiers (lower = more authoritative), used by Fact-Check and the Stat Auditor.
TIER_OFFICIAL, TIER_JOURNALIST, TIER_DATA, TIER_AGGREGATOR, TIER_SOCIAL = 1, 2, 3, 4, 5

HOUR, DAY = 3600, 86400


class MissingCredential(RuntimeError):
    pass


class SourceDisabled(RuntimeError):
    """Raised when a source must not run in this environment (e.g. Transfermarkt in CI)."""


@dataclass
class IngestContext:
    con: duckdb.DuckDBPyConnection
    league: str = "premier_league"
    settings: Settings = field(default_factory=get_settings)
    clubs: ClubResolver = field(default_factory=ClubResolver.from_config)
    run_id: str = field(default_factory=lambda: datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    transport: Any = None  # httpx transport override (tests)
    reep_index: ReepIndex | None = None  # tests inject a StaticIndex; otherwise resolved lazily
    throttle: bool = True  # tests replaying fixtures turn rate limiting off
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def reep(self) -> ReepIndex:
        # lazy: the Reep adapter may sync the catalogue earlier in the same job
        return self.reep_index or default_index()

    @property
    def ledger(self) -> EvidenceLedger:
        return EvidenceLedger(self.con)

    @property
    def league_cfg(self) -> dict[str, Any]:
        return league_config(self.league)

    def load_clubs(self) -> None:
        """Fill the resolver from the clubs/entity_ids tables (populated by the FPL adapter)."""
        for (slug,) in self.con.execute(
            "SELECT club_id FROM clubs WHERE league = ?", [self.league]
        ).fetchall():
            self.clubs.add(slug)
        for slug, provider, ns, ext in self.con.execute(
            "SELECT dugout_id, provider, namespace, external_id FROM entity_ids WHERE entity_type = 'club'"
        ).fetchall():
            self.clubs.add(slug, f"{provider}/{ns}", ext)


class SourceAdapter(ABC):
    name: ClassVar[str]
    tier: ClassVar[int] = TIER_DATA
    ttl: ClassVar[float] = 6 * HOUR
    rate: ClassVar[float] = 1.0  # requests per `per` seconds
    per: ClassVar[float] = 1.0
    burst: ClassVar[int] = 1
    daily_limit: ClassVar[int | None] = None
    key_setting: ClassVar[str | None] = None  # Settings attribute holding the API key
    respect_robots: ClassVar[bool] = True
    local_only: ClassVar[bool] = False  # refuses to run when CI is set
    required: ClassVar[bool] = False  # nightly fails if a required source fails
    client_name: ClassVar[str | None] = None  # share another adapter's cache and daily budget (same provider)
    base_url: ClassVar[str] = ""
    # "league": runs for each league that takes it (runner.run_leagues); "global": once per job, whatever the league
    scope: ClassVar[str] = "league"
    requires: ClassVar[str | None] = None  # provider_ids key a league needs for this source to run there

    def __init__(self, ctx: IngestContext):
        self.ctx = ctx
        if self.local_only and ctx.settings.ci:
            raise SourceDisabled(f"{self.name} is local-only and never runs in CI")
        if self.requires and not ctx.league_cfg.get("provider_ids", {}).get(self.requires):
            raise SourceDisabled(f"{self.name} needs provider_ids.{self.requires} ({ctx.league} has none)")
        self.client = CachedClient(
            self.client_name or self.name,
            cache_dir=ctx.settings.cache_dir,
            user_agent=ctx.settings.user_agent,
            ttl=self.ttl,
            rate=self.rate if ctx.throttle else 1e9,
            per=self.per,
            burst=self.burst,
            daily_limit=self.daily_limit,
            respect_robots=self.respect_robots,
            headers=self.auth_headers(),
            provenance=provenance_sink(ctx.con),
            transport=ctx.transport,
        )

    @property
    def api_key(self) -> str:
        if not self.key_setting:
            raise MissingCredential(f"{self.name} has no key setting")
        key = self.ctx.settings.secret(self.key_setting)
        if not key:
            raise MissingCredential(f"{self.name}: {self.key_setting.upper()} is not set")
        return key

    def auth_headers(self) -> dict[str, str]:
        return {}

    @abstractmethod
    def ingest(self) -> dict[str, int]:
        """Fetch, parse and upsert. Returns rows written per table."""

    def close(self) -> None:
        self.client.close()
