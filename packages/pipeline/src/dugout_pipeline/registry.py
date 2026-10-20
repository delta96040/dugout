"""Which adapters exist and which run in each job, in dependency order."""

from __future__ import annotations

from dugout_pipeline.adapters.api_football import ApiFootballAdapter, ApiFootballHistoryAdapter
from dugout_pipeline.adapters.api_football_stats import ApiFootballStatsAdapter
from dugout_pipeline.adapters.base import HOUR, IngestContext, SourceAdapter
from dugout_pipeline.adapters.clubelo import ClubEloAdapter
from dugout_pipeline.adapters.football_data_couk import FootballDataCoUkAdapter
from dugout_pipeline.adapters.football_data_org import FootballDataOrgAdapter
from dugout_pipeline.adapters.fpl import FPLAdapter
from dugout_pipeline.adapters.fpl_history import FPLHistoryAdapter
from dugout_pipeline.adapters.news import ClubNewsAdapter, GuardianAdapter, RSSAdapter
from dugout_pipeline.adapters.rumours import GuardianRumourMillAdapter, RumourArchiveAdapter
from dugout_pipeline.adapters.transfermarkt import (
    TransfermarktDatasetsLoader,
    TransfermarktFileAdapter,
    TransfermarktPoolRefresher,
    TransfermarktRefresher,
)
from dugout_pipeline.adapters.weather import OpenMeteoAdapter
from dugout_pipeline.adapters.wikidata import WikidataAdapter
from dugout_pipeline.adapters.wikipedia_transfers import WikipediaTransfersAdapter
from dugout_pipeline.ids import sync_catalogue


class ReepAdapter(SourceAdapter):
    """Keeps the local Reep Register catalogue current (CC0, ~210 MB; the reep SDK verifies SHA-256)."""

    name = "reep"
    scope = "global"
    ttl = 24 * HOUR
    required = True

    def ingest(self) -> dict[str, int]:
        release = sync_catalogue()
        return {"reep_release": 0 if release == "cached" else 1}  # 0 = upstream broken, cached copy in use


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
        RumourArchiveAdapter,
        WikipediaTransfersAdapter,
        RSSAdapter,
        ClubNewsAdapter,
        OpenMeteoAdapter,
        TransfermarktRefresher,
        TransfermarktPoolRefresher,
        TransfermarktDatasetsLoader,
        FPLHistoryAdapter,
        ApiFootballHistoryAdapter,
        ApiFootballStatsAdapter,
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
        "wikipedia_transfers",
        "rss",
        "club_news",
        "open_meteo",
        # last: a few pages of player season stats a night, within what the day's other API-Football calls leave
        "api_football_stats",
    ],
    "injuries": ["fpl", "api_football"],
    "history": [
        "reep",
        "fpl",
        "football_data_couk",
        "transfermarkt_datasets",
        "fpl_history",
        "api_football_history",
        "rumour_archive",
    ],
}


def build(name: str, ctx: IngestContext) -> SourceAdapter:
    return ADAPTERS[name](ctx)
