"""Reep Register wrapper: every club/player/manager gets a Reep ID before it is stored.

Matching is exact on provider ids (no name matching). The local catalogue (~210 MB, CC0) is synced by
`dugout ingest --only reep` and cached in CI. Tests inject a `StaticIndex`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import duckdb

log = logging.getLogger(__name__)

# Providers we keep in entity_ids for clubs (reep provider name -> namespace).
CLUB_PROVIDERS = {
    "api_football": "team",
    "clubelo": "team",
    "transfermarkt": "verein",
    "understat": "team",
    "statsbomb": "offline_team",
    "wyscout": "team",
    "skillcorner": "team",
    "sofascore": "team",
    "fotmob": "team",
}
PLAYER_PROVIDERS = {
    "api_football": "player",
    "transfermarkt": "spieler",
    "fbref": "person",
    "understat": "player",
    "statsbomb": "offline_player",
    "wyscout": "player",
    "skillcorner": "player",
    "sofascore": "player",
}


@dataclass(frozen=True)
class ReepMatch:
    reep_id: str
    label: str
    ids: dict[str, str] = field(default_factory=dict)  # "provider/namespace" -> external id


class ReepIndex(Protocol):
    def lookup(self, provider: str, namespace: str, external_id: str | int) -> ReepMatch | None: ...

    def teams(self, country: str) -> list[tuple[str, str, str]]: ...


def catalogue_path() -> Path:
    from reep.config import Settings

    return Settings.from_env().catalog_path


class LocalReepIndex:
    """Reads the local Reep catalogue (DuckDB: `bridges`, `entities`, `redirects`) directly."""

    def __init__(self, path: Path | None = None) -> None:
        path = path or catalogue_path()
        if not path.is_file():
            raise FileNotFoundError(path)
        self._con = duckdb.connect(str(path), read_only=True)

    def lookup(self, provider: str, namespace: str, external_id: str | int) -> ReepMatch | None:
        row = self._con.execute(
            """SELECT coalesce(r.to_id, b.reep_id) FROM bridges b LEFT JOIN redirects r ON r.from_id = b.reep_id
               WHERE b.provider = ? AND b.namespace = ? AND b.external_id = ? LIMIT 1""",
            [provider, namespace, str(external_id)],
        ).fetchone()
        if not row or not row[0]:
            return None
        rid = row[0]
        label = self._con.execute("SELECT label FROM entities WHERE reep_id = ?", [rid]).fetchone()
        ids = {
            f"{p}/{ns}": ext
            for p, ns, ext in self._con.execute(
                "SELECT provider, namespace, external_id FROM bridges WHERE reep_id = ? ORDER BY provider, namespace, external_id",
                [rid],
            ).fetchall()
        }
        return ReepMatch(rid, label[0] if label else "", ids)

    def teams(self, country: str) -> list[tuple[str, str, str]]:
        """(reep id, label, Transfermarkt club id) of men's teams in a country: candidates when a provider without
        a Reep bridge (football-data.org) names a club."""
        return self._con.execute(
            """SELECT e.reep_id, e.label, b.external_id FROM entities e JOIN bridges b USING (reep_id)
               WHERE e.entity_type = 'team' AND coalesce(e.gender, 'men') = 'men' AND e.country = ?
                 AND e.status = 'active'
                 AND b.provider = 'transfermarkt' AND b.namespace = 'verein'""",
            [country],
        ).fetchall()


def attach_catalogue(con: duckdb.DuckDBPyConnection, alias: str = "reep") -> bool:
    """ATTACH the Reep catalogue read-only for bulk SQL joins. Returns False if it is not synced."""
    path = catalogue_path()
    if not path.is_file():
        return False
    if alias not in {r[0] for r in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}:
        con.execute(f"ATTACH '{path.as_posix()}' AS {alias} (READ_ONLY)")
    return True


@dataclass
class StaticIndex:
    """In-memory index for tests: {(provider, namespace, external_id): ReepMatch}."""

    entries: dict[tuple[str, str, str], ReepMatch] = field(default_factory=dict)
    countries: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)

    def lookup(self, provider: str, namespace: str, external_id: str | int) -> ReepMatch | None:
        return self.entries.get((provider, namespace, str(external_id)))

    def teams(self, country: str) -> list[tuple[str, str, str]]:
        return self.countries.get(country, [])


class NullIndex:
    def lookup(self, provider: str, namespace: str, external_id: str | int) -> ReepMatch | None:
        return None

    def teams(self, country: str) -> list[tuple[str, str, str]]:
        return []


@lru_cache
def default_index() -> ReepIndex:
    try:
        return LocalReepIndex()
    except Exception as exc:  # catalogue not synced yet
        log.warning(
            "Reep catalogue unavailable (%s); run `dugout ingest --only reep`. Using no-op index.", exc
        )
        return NullIndex()


def sync_catalogue() -> str:
    import reep

    result = reep.catalog.sync()
    default_index.cache_clear()
    return str(getattr(result, "release", result))
