"""Club identity: slugs, name normalisation, and resolving any provider's club name/id to a dugout club slug."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from dugout_pipeline.settings import get_settings

_DROP = re.compile(r"\b(fc|afc|a f c|f c|football club|the)\b")


def normalise(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("'", " ").replace(".", " ")
    s = _DROP.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(name: str) -> str:
    s = normalise(name).replace(" and ", " ")
    return re.sub(r"\s+", "-", s)


@lru_cache
def load_config(config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or get_settings().config_dir) / "leagues.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def league_config(league: str = "premier_league") -> dict[str, Any]:
    return load_config()[league]


def leagues(config_dir: Path | None = None) -> list[str]:
    """Configured leagues in file order. The first is the default league: it runs every source of a job; the
    others run the sources their block lists under `sources`."""
    return [k for k, v in load_config(config_dir).items() if isinstance(v, dict) and "provider_ids" in v]


@dataclass
class ClubResolver:
    """Maps provider names and ids to dugout club slugs for one league-season."""

    slugs: set[str] = field(default_factory=set)
    aliases: dict[str, str] = field(default_factory=dict)
    by_provider: dict[tuple[str, str], str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, slugs: set[str] | None = None) -> ClubResolver:
        return cls(slugs=set(slugs or ()), aliases=dict(load_config().get("club_aliases", {})))

    def add(self, slug: str, provider: str | None = None, external_id: str | int | None = None) -> None:
        self.slugs.add(slug)
        if provider and external_id is not None:
            self.by_provider[(provider, str(external_id))] = slug

    def by_id(self, provider: str, external_id: str | int) -> str | None:
        return self.by_provider.get((provider, str(external_id)))

    def resolve(
        self, name: str, provider: str | None = None, external_id: str | int | None = None
    ) -> str | None:
        if provider and external_id is not None and (hit := self.by_id(provider, external_id)):
            return hit
        n = normalise(name)
        candidates = [self.aliases.get(n), slugify(name), self.aliases.get(n.replace(" and ", " "))]
        for c in candidates:
            if c and (not self.slugs or c in self.slugs):
                return c
        # prefix match: "Brighton" -> brighton-hove-albion, "Bournemouth" -> afc-bournemouth
        matches = [s for s in self.slugs if s.startswith(slugify(name)) or s.endswith(slugify(name))]
        return matches[0] if len(matches) == 1 else None
