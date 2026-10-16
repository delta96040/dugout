"""Record / replay HTTP fixtures for adapter tests.

`dugout record <source>` runs an adapter live through `RecordingTransport`, trims each response to a tiny
excerpt (so provider data is not redistributed in the repo), and writes
`packages/pipeline/tests/fixtures/<source>/` = index.json {redacted url: file} + gzipped bodies.
Tests replay them with `ReplayTransport`; any request without a fixture fails loudly.
Request headers are never recorded, and URLs are stored redacted (no API keys).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from dugout_pipeline.http import redact_url
from dugout_pipeline.settings import REPO_ROOT

FIXTURES = REPO_ROOT / "packages" / "pipeline" / "tests" / "fixtures"
KEEP_CLUBS = {"Liverpool", "Arsenal"}  # trimmed fixtures keep two clubs


class RecordingTransport(httpx.BaseTransport):
    def __init__(self, source: str, trim: Callable[[str, bytes], bytes] | None = None):
        self.dir = FIXTURES / source
        self.trim = trim or (lambda url, body: body)
        self.index: dict[str, str] = {}
        self._real = httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._real.handle_request(request)
        resp.read()
        url = redact_url(str(request.url))
        if not url.endswith("/robots.txt"):
            body = self.trim(url, resp.content)
            name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".gz"
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / name).write_bytes(gzip.compress(body, mtime=0))
            self.index[url] = name
            (self.dir / "index.json").write_text(json.dumps(self.index, indent=1, sort_keys=True))
            return httpx.Response(
                resp.status_code, content=body, headers={"content-type": resp.headers.get("content-type", "")}
            )
        return httpx.Response(
            resp.status_code,
            content=resp.content,
            headers={"content-type": resp.headers.get("content-type", "")},
        )


class ReplayTransport(httpx.BaseTransport):
    """Serves recorded fixtures. robots.txt is answered with 404 (= no restrictions) unless recorded."""

    # Query params that depend on when a fixture was recorded; ignored when matching.
    VOLATILE = ("from-date", "to-date", "date")

    def __init__(self, *sources: str, root: Path = FIXTURES):
        self.index: dict[str, Path] = {}
        for s in sources:
            d = root / s
            for url, name in json.loads((d / "index.json").read_text()).items():
                self.index[self._key(url)] = d / name
        self.requests: list[str] = []

    @classmethod
    def _key(cls, url: str) -> str:
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in cls.VOLATILE]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = self._key(redact_url(str(request.url)))
        self.requests.append(url)
        if url in self.index:
            return httpx.Response(200, content=gzip.decompress(self.index[url].read_bytes()))
        if url.endswith("/robots.txt"):
            return httpx.Response(404)
        raise AssertionError(f"no recorded fixture for {url}")


# ---------------------------------------------------------------------------------------------------------
# Trimmers: keep the structure, drop almost all rows.


def _json(fn: Callable[[str, Any], Any]) -> Callable[[str, bytes], bytes]:
    def trim(url: str, body: bytes) -> bytes:
        try:
            doc = json.loads(body)
        except ValueError:
            return body
        return json.dumps(fn(url, doc), separators=(",", ":")).encode()

    return trim


_fpl_state: dict[str, Any] = {}


def _trim_fpl(url: str, doc: Any) -> Any:
    """Keeps all clubs but players of two only, a dozen players, the last finished gameweek and its fixtures, consistently."""
    st = _fpl_state
    if url.endswith("bootstrap-static/"):
        teams = doc["teams"]  # all 20 clubs (tiny) so fixtures resolve; players only for two
        st["teams"] = {t["id"] for t in teams if t["name"] in KEEP_CLUBS}
        els = [e for e in doc["elements"] if e["team"] in st["teams"]]
        picked = (
            sorted(els, key=lambda e: -(e["minutes"] or 0))[:8] + [e for e in els if e["status"] != "a"][:4]
        )
        elements = list({e["id"]: e for e in picked}.values())
        st["elements"] = {e["id"] for e in elements}
        events = [e for e in doc["events"] if e["finished"]][-1:]
        st["event"] = events[0]["id"] if events else None
        return {"teams": teams, "elements": elements, "events": events, "element_types": doc["element_types"]}
    if url.endswith("fixtures/"):
        t = st.get("teams", set())
        return [
            f
            for f in doc
            if (f["team_h"] in t or f["team_a"] in t)
            and (f["event"] == st.get("event") or (f["team_h"] in t and f["team_a"] in t))
        ]
    if "/live/" in url:
        return {"elements": [e for e in doc["elements"] if e["id"] in st.get("elements", set())]}
    return doc


def _trim_list(key: str, n: int) -> Callable[[str, Any], Any]:
    def f(url: str, doc: Any) -> Any:
        if isinstance(doc, dict) and isinstance(doc.get(key), list):
            doc = {**doc, key: doc[key][:n]}
        return doc

    return f


def _trim_rss(url: str, body: bytes) -> bytes:
    """Keep the channel header and the first three <item>s."""
    text = body.decode("utf-8", errors="replace")
    head, sep, rest = text.partition("<item")
    items = re.findall(r"<item[\s>].*?</item>", sep + rest, flags=re.S)[:3]
    return (head + "".join(items) + "</channel></rss>").encode()


def _trim_csv(url: str, body: bytes) -> bytes:
    return b"\n".join(body.splitlines()[:21]) + b"\n"


def _trim_rumour_mill(url: str, doc: Any) -> Any:
    """Two columns, one page, and of each body only the first two paragraphs that name a player (bold)."""
    results = []
    for r in doc["response"]["results"][:2]:
        paras = [
            p
            for p in re.findall(r"<p>.*?</p>", r.get("fields", {}).get("body", ""), flags=re.S)
            if "<strong>" in p
        ]
        results.append({**r, "fields": {"body": "".join(paras[:2])}})
    return {"response": {**doc["response"], "pages": 1, "results": results}}


TRIMMERS: dict[str, Callable[[str, bytes], bytes]] = {
    "rss": _trim_rss,
    "football_data_couk": _trim_csv,
    "clubelo": _trim_csv,
    "fpl": _json(_trim_fpl),
    "football_data_org": _json(
        lambda u, d: {
            **d,
            **({"matches": d["matches"][:12]} if "matches" in d else {}),
            **({"teams": d["teams"][:6]} if "teams" in d else {}),
        }
    ),
    "guardian": _json(
        lambda u, d: {
            "response": {
                **d["response"],
                "results": [
                    {
                        **r,
                        "fields": {
                            **r.get("fields", {}),
                            "bodyText": r.get("fields", {}).get("bodyText", "")[:300],
                        },
                    }
                    for r in d["response"]["results"][:2]
                ],
            }
        }
    ),
    "api_football": _json(_trim_list("response", 6)),
    "rumour_archive": _json(_trim_rumour_mill),
}
