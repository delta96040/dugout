"""Offline forecast-log guard for the Premier League kickoff refresh."""

import csv
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORECAST_LOG = ROOT / "data/prediction_log.csv"
CLUB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def due_clubs(rows: list[dict[str, str]], now: datetime, lead_minutes: int = 60) -> list[str]:
    """Only upcoming PL fixtures; newer forecasts supersede old rescheduled fixtures."""
    latest: dict[str, tuple[datetime, datetime, tuple[str, str]]] = {}
    for row in rows:
        parts = row.get("match_id", "").split(":")
        if len(parts) != 4 or parts[0] != "premier_league":
            continue
        home, away = parts[2:]
        if not CLUB.fullmatch(home) or not CLUB.fullmatch(away):
            continue
        try:
            issued = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00"))
            kickoff = datetime.fromisoformat(row["kickoff_utc"].replace("Z", "+00:00"))
            if issued.utcoffset() is None or kickoff.utcoffset() is None:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        old = latest.get(row["match_id"])
        if old is None or issued > old[0]:
            latest[row["match_id"]] = issued, kickoff, (home, away)
    upper = now + timedelta(minutes=lead_minutes)
    return sorted({club for _, kickoff, pair in latest.values() if now < kickoff <= upper for club in pair})


def main() -> int:
    now = datetime.now(UTC)
    try:
        with FORECAST_LOG.open(newline="", encoding="utf-8") as source:
            clubs = due_clubs(list(csv.DictReader(source)), now)
    except (OSError, csv.Error) as exc:
        print(f"Cannot read current fixtures: {exc}", file=sys.stderr)
        clubs = []
    print(f"due={str(bool(clubs)).lower()}")
    print(f"clubs={' '.join(clubs)}")
    print(f"Kickoff check: {len(clubs)} clubs due", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
