"""Which adapters exist and which run in each job, in dependency order."""

from __future__ import annotations

from dugout_pipeline.adapters.api_football import ApiFootballAdapter, ApiFootballHistoryAdapter
from dugout_pipeline.adapters.base import HOUR, IngestContext, SourceAdapter
from dugout_pipeline.adapters.clubelo import ClubEloAdapter
from dugout_pipeline.adapters.football_data_couk import FootballDataCoUkAdapter
from dugout_pipeline.adapters.football_data_org import FootballDataOrgAdapter
from dugout_pipeline.adapters.fpl import FPLAdapter
from dugout_pipeline.adapters.fpl_history import FPLHistoryAdapter
from dugout_pipeline.adapters.news import ClubNewsAdapter, GuardianAdapter, RSSAdapter
from dugout_pipeline.adapters.rumours import GuardianRumourMillAdapter
from dugout_pipeline.adapters.transfermarkt import (
    TransfermarktDatasetsLoader,
    TransfermarktFileAdapter,
    TransfermarktRefresher,
)
from dugout_pipeline.adapters.weather import OpenMeteoAdapter
from dugout_pipeline.adapters.wikidata import WikidataAdapter
from dugout_pipeline.ids import sync_catalogue


class ReepAdapter(SourceAdapter):
    """Keeps the local Reep Register catalogue current (CC0, ~210 MB; the reep SDK verifies SHA-256)."""

    name = "reep"
    ttl = 24 * HOUR
    required = True

    def ingest(self) -> dict[str, int]:
        return {"reep_release": 1 if sync_catalogue() else 0}


ADAPTERS: dict[str, type[SourceAdapter]] = {
    a.name: a
    for a in [
        ReepAdapter,
        FPLAdapter,
        FootballDataOrgAdapter,
        WikidataAdapter,
        ApiFootballAdapter,
        ClubEloAdapter,
        FootballDataCoUkAdapter,
        TransfermarktFileAdapter,
        GuardianAdapter,
        GuardianRumourMillAdapter,
        RSSAdapter,
        ClubNewsAdapter,
        OpenMeteoAdapter,
        TransfermarktRefresher,
        TransfermarktDatasetsLoader,
        FPLHistoryAdapter,
        ApiFootballHistoryAdapter,
    ]
}

JOBS: dict[str, list[str]] = {
    # order matters: FPL creates clubs/players; others map onto them
    "nightly": [
        "reep",
        "fpl",
        "football_data_org",
        "wikidata",
        "api_football",
        "clubelo",
        "football_data_couk",
        "transfermarkt_file",
        "guardian",
        "guardian_rumours",
        "rss",
        "club_news",
        "open_meteo",
    ],
    "injuries": ["fpl", "api_football"],
    "history": [
        "reep",
        "fpl",
        "football_data_couk",
        "transfermarkt_datasets",
        "fpl_history",
        "api_football_history",
    ],
}


def build(name: str, ctx: IngestContext) -> SourceAdapter:
    return ADAPTERS[name](ctx)
