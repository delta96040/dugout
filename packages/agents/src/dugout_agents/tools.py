"""Tools: warehouse and model queries for each specialist. Every number goes to the ledger; tools return Facts.

Templates are written so a sentence names its subject and uses a word that fits the metric (the Fact-Check
binding rule), and avoids news keywords when the sentence only states a model output.
"""

from __future__ import annotations

import csv
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

from dugout_agents.facts import MODEL_DOCS, Fact, Toolbox, prob_gloss, rank_gloss, z_gloss
from dugout_pipeline.settings import REPO_ROOT

FDO_URL = "https://api.football-data.org/v4/competitions/{comp}/standings"


def _fact(
    tb: Toolbox,
    subject: str,
    label: str,
    template: str,
    rec=None,
    fmt: str | None = None,
    gloss: str = "",
    entity_id: str | None = None,
) -> Fact | None:
    if rec is None and "{p}" in template:
        return None
    eid = rec[0] if rec else None
    return Fact(tb.key(), subject, label, template, eid, fmt, gloss, entity_id)


def _keep(*facts: Fact | None) -> list[Fact]:
    return [f for f in facts if f is not None]


def _fetched(tb: Toolbox, table: str, where: str, args: list, column: str = "fetched_at") -> datetime:
    row = tb.con.execute(f"SELECT max({column}) FROM {table} WHERE {where}", args).fetchone()
    return row[0] or tb.now


# ---- Club & Tactics ----------------------------------------------------------------------------------------
def club_profile(tb: Toolbox, club: str) -> list[Fact]:
    name = tb.name(club)
    lg = tb.league(club)
    facts: list[Fact] = []
    pos = tb.latest("club", club, "league_position")
    if pos is not None:
        n = sum(1 for c, k in tb.club_league.items() if k == lg["key"]) or 20
        facts.append(
            _fact(
                tb,
                name,
                "league position",
                f"{name} are {{p}} in the {lg['name']} table.",
                (pos.id, pos.value),
                "ord",
                rank_gloss(pos.value_num or n / 2, n, lg.get("table")),
                club,
            )
        )
    pts = tb.latest("club", club, "league_points")
    if pts is not None:
        facts.append(
            _fact(
                tb, name, "league points", f"{name} have {{p}} points.", (pts.id, pts.value), entity_id=club
            )
        )
    gf, ga = tb.latest("club", club, "league_goalsFor"), tb.latest("club", club, "league_goalsAgainst")
    if gf is not None:
        facts.append(
            _fact(
                tb,
                name,
                "league goals scored",
                f"{name} have scored {{p}} league goals.",
                (gf.id, gf.value),
                entity_id=club,
            )
        )
    if ga is not None:
        facts.append(
            _fact(
                tb,
                name,
                "league goals conceded",
                f"{name} have conceded {{p}} league goals.",
                (ga.id, ga.value),
                entity_id=club,
            )
        )
    w = "league = ? AND season = ? AND club_id = ? AND source = 'football_data_org'"
    args = [lg["key"], tb.season, club]
    fetched = _fetched(tb, "standings", w, args)
    form = tb.con.execute(f"SELECT form FROM standings WHERE {w}", args).fetchone()
    if form and form[0]:
        rec = tb.sql(
            f"SELECT replace(form, ',', '') FROM standings WHERE {w}",
            args,
            entity_type="club",
            entity_id=club,
            metric="league_form",
            source="football_data_org",
            tier=2,
            fetched_at=fetched,
            url=FDO_URL.format(comp=lg["provider_ids"]["football_data_org"]),
        )
        facts.append(
            _fact(
                tb,
                name,
                "recent league results, oldest first",
                f"{name}'s most recent league results read {{p}}.",
                rec,
                entity_id=club,
            )
        )
    elo = tb.model("predictor", "club", club, "elo")
    facts.append(
        _fact(
            tb,
            name,
            "Elo rating (dugout's own)",
            f"{name}'s Elo rating is {{p}}.",
            elo,
            "int",
            entity_id=club,
        )
    )
    return _keep(*facts)


def manager_style(tb: Toolbox, club: str) -> list[Fact]:
    row = tb.con.execute(
        "SELECT manager_id, name, start_date FROM managers WHERE club_id = ? AND source = 'wikidata'", [club]
    ).fetchone()
    if not row:
        return []
    mid, name, start = row
    surname = name.split()[-1]
    facts: list[Fact] = []
    if start:
        rec = tb.sql(
            "SELECT CAST(start_date AS VARCHAR) FROM managers WHERE manager_id = ? AND source = 'wikidata'",
            [mid],
            entity_type="manager",
            entity_id=mid,
            metric="appointed",
            source="wikidata",
            tier=3,
            fetched_at=_fetched(tb, "managers", "manager_id = ?", [mid], "updated_at"),
            url=f"https://www.wikidata.org/wiki/{mid.split(':')[-1]}",
        )
        facts.append(
            _fact(
                tb,
                name,
                f"appointed head coach of {tb.name(club)}",
                f"{name} has been {tb.name(club)}'s head coach since {{p}}.",
                rec,
                "date",
                entity_id=mid,
            )
        )
    n = tb.model("fingerprint", "manager", mid, "pl_matches")
    facts.append(
        _fact(
            tb,
            name,
            "Premier League matches in the style sample",
            f"{name}'s style profile covers {{p}} Premier League matches.",
            n,
            "int",
            entity_id=mid,
        )
    )
    style = [
        (
            "threat_share",
            "share of the match threat (territory)",
            "pct",
            f"Teams under {surname} have taken {{p}} of the threat in their matches",
            ("more territorial", "less territorial"),
        ),
        (
            "def_actions",
            "defensive actions per match",
            ".0f",
            f"Teams under {surname} average {{p}} defensive actions per match",
            ("more active without the ball", "less active without the ball"),
        ),
        (
            "xg_for",
            "expected goals for per match",
            ".2f",
            f"Teams under {surname} create {{p}} expected goals per match",
            ("more dangerous", "less dangerous"),
        ),
        (
            "xg_against",
            "expected goals against per match",
            ".2f",
            f"Teams under {surname} concede {{p}} expected goals per match",
            ("more open", "tighter"),
        ),
        (
            "creativity_share",
            "share of chance creation",
            "pct",
            f"Teams under {surname} account for {{p}} of the chance creation in their matches",
            ("more creative", "less creative"),
        ),
    ]
    for metric, label, fmt, template, (hi, lo) in style:
        rec = tb.model("fingerprint", "manager", mid, metric)
        z = tb.model("fingerprint", "manager", mid, f"z_{metric}")
        if rec is None or z is None:
            continue
        gloss = z_gloss(float(z[1]), hi, lo)
        facts.append(_fact(tb, name, label, template + f", {gloss}.", rec, fmt, gloss, mid))
    return _keep(*facts)


def player_season(tb: Toolbox, player_id: str) -> list[Fact]:
    name = tb.name(player_id)
    out = []
    for metric, label, template, fmt in (
        (
            "season_goals_scored",
            "league goals this season",
            f"{name} has scored {{p}} league goals this season.",
            None,
        ),
        (
            "season_assists",
            "league assists this season",
            f"{name} has {{p}} league assists this season.",
            None,
        ),
        (
            "season_minutes",
            "league minutes this season",
            f"{name} has played {{p}} league minutes this season.",
            "int",
        ),
        (
            "season_expected_goals",
            "expected goals (xG) this season",
            f"{name}'s expected goals (xG) this season total {{p}}.",
            ".2f",
        ),
    ):
        ev = tb.latest("player", player_id, metric, "fpl")
        if ev is not None:
            out.append(_fact(tb, name, label, template, (ev.id, ev.value), fmt, entity_id=player_id))
    return _keep(*out)


# ---- Match Predictor ---------------------------------------------------------------------------------------
def next_match(tb: Toolbox, club: str) -> list[Fact]:
    row = tb.con.execute(
        """SELECT match_id, home_club_id, away_club_id FROM matches WHERE status = 'scheduled' AND kickoff_utc >= ?
           AND (home_club_id = ? OR away_club_id = ?) ORDER BY kickoff_utc LIMIT 1""",
        [tb.now, club, club],
    ).fetchone()
    if not row:
        return []
    mid, home, away = row
    tb.match_aliases(mid)
    h, a = tb.name(home), tb.name(away)
    is_home = home == club
    me, opp = (h, a) if is_home else (a, h)
    facts: list[Fact] = []
    ko = tb.model("predictor", "match", mid, "kickoff_utc")
    if ko is None:  # a league without a backtested forecast: the fixture itself, from football-data.org
        lg = tb.league(club)
        ko = tb.sql(
            "SELECT strftime(kickoff_utc, '%Y-%m-%dT%H:%M:%S+00:00') FROM matches WHERE match_id = ?",
            [mid],
            entity_type="match",
            entity_id=mid,
            metric="kickoff_utc",
            source="football_data_org",
            tier=2,
            fetched_at=_fetched(tb, "matches", "match_id = ?", [mid], "updated_at"),
            url=f"https://api.football-data.org/v4/competitions/{lg['provider_ids']['football_data_org']}/matches",
        )
    facts.append(
        _fact(
            tb,
            f"{h} v {a}",
            "kick-off date",
            f"{tb.name(club)}'s next league match is {h} v {a} on {{p}}.",
            ko,
            "date",
            entity_id=mid,
        )
    )
    for metric, side in (("p_home_win", h), ("p_draw", None), ("p_away_win", a)):
        rec = tb.model("predictor", "match", mid, metric)
        if rec is None:
            continue
        if side:
            tmpl = f"Our forecast gives {side} a {{p}} chance of winning against {a if side == h else h}."
            label = f"probability {side} win"
        else:
            tmpl = f"Our forecast gives {h} v {a} a {{p}} chance of ending in a draw."
            label = "probability of a draw"
        facts.append(_fact(tb, side or f"{h} v {a}", label, tmpl, rec, "pct", prob_gloss(float(rec[1])), mid))
    for metric, side in (("exp_home_goals", h), ("exp_away_goals", a)):
        rec = tb.model("predictor", "match", mid, metric)
        facts.append(
            _fact(
                tb,
                side,
                "expected goals in this match",
                f"We expect {side} to score {{p}} goals against {a if side == h else h}.",
                rec,
                ".1f",
                entity_id=mid,
            )
        )
    for c in (club, away if is_home else home):
        rec = tb.model("predictor", "club", c, "elo")
        facts.append(
            _fact(
                tb,
                tb.name(c),
                "Elo rating (dugout's own)",
                f"{tb.name(c)}'s Elo rating is {{p}}.",
                rec,
                "int",
                entity_id=c,
            )
        )
    del me, opp
    return _keep(*facts)


# ---- Lineup Selector ---------------------------------------------------------------------------------------
SLOT_NAMES = {
    "GK": "goalkeeper",
    "CB": "centre-back",
    "RB": "right-back",
    "LB": "left-back",
    "RWB": "right wing-back",
    "LWB": "left wing-back",
    "DM": "defensive midfield",
    "CM": "central midfield",
    "AM": "attacking midfield",
    "RM": "right midfield",
    "LM": "left midfield",
    "RW": "right wing",
    "LW": "left wing",
    "CF": "centre-forward",
}
SLOT_ORDER = list(SLOT_NAMES)


def predicted_xi(tb: Toolbox, club: str) -> list[Fact]:
    row = tb.con.execute(
        "SELECT context FROM model_outputs WHERE model='lineup' AND entity_type='club' AND entity_id=? "
        "AND metric='predicted_formation'",
        [club],
    ).fetchone()
    if not row:
        return []
    mid = row[0]
    name = tb.name(club)
    facts = _keep(
        _fact(
            tb,
            name,
            "predicted formation for the next match",
            f"We expect {name} to line up in a {{p}} for the next match.",
            tb.model("lineup", "club", club, "predicted_formation", mid),
            entity_id=club,
        )
    )
    xi = tb.con.execute(
        "SELECT entity_id, value_text FROM model_outputs WHERE model='lineup' AND metric='predicted_xi_slot' "
        "AND context = ? AND entity_id IN (SELECT player_id FROM players WHERE club_id = ?)",
        [mid, club],
    ).fetchall()
    xi.sort(key=lambda r: SLOT_ORDER.index(r[1]) if r[1] in SLOT_ORDER else 99)
    for pid, slot in xi:
        pname = tb.name(pid)
        rec = tb.model("lineup", "player", pid, "predicted_xi_slot", mid)
        facts += _keep(
            _fact(
                tb,
                pname,
                f"predicted starting slot ({SLOT_NAMES.get(slot, slot)})",
                f"Predicted to start: {pname} at {{p}}.",
                rec,
                entity_id=pid,
            )
        )
        tier = tb.model("injury", "player", pid, "injury_risk_tier")
        if tier and tier[1] in ("elevated", "high"):
            facts += _keep(
                _fact(
                    tb,
                    pname,
                    "injury-risk tier (estimate from load and injury history)",
                    f"We estimate {pname}'s injury risk as {{p}}.",
                    tier,
                    gloss="worth managing his minutes",
                    entity_id=pid,
                )
            )
    return facts


# ---- News & Injury -----------------------------------------------------------------------------------------
STATUS_WORDS = {
    "injured": "injured",
    "suspended": "suspended",
    "doubtful": "a doubt",
    "unavailable": "unavailable",
}


def availability(tb: Toolbox, club: str, player_id: str | None = None) -> list[Fact]:
    """FPL availability flags (official, tier 1). Statements are text facts verified against the same flags."""
    rows = tb.con.execute(
        """SELECT i.player_id, i.status, i.reason, i.chance_next FROM injuries i
           WHERE i.source = 'fpl' AND i.snapshot_date = (SELECT max(snapshot_date) FROM injuries WHERE source = 'fpl')
             AND i.status <> 'available' AND (i.club_id = ? OR i.player_id = ?)
           ORDER BY CASE i.status WHEN 'injured' THEN 0 WHEN 'suspended' THEN 1 WHEN 'doubtful' THEN 2 ELSE 3 END""",
        [club, player_id or ""],
    ).fetchall()
    facts: list[Fact] = []
    for pid, status, reason, chance in rows:
        if status == "unavailable" and reason and ("loan" in reason.lower() or "joined" in reason.lower()):
            continue  # departures are squad news, not availability
        pname = tb.name(pid)
        what = (reason or status).rstrip(".")
        if status == "doubtful" and chance is not None:
            ev = tb.latest("player", pid, "season_chance_of_playing_next_round", "fpl")
            if ev is not None and ev.value_num is not None:
                facts += _keep(
                    _fact(
                        tb,
                        pname,
                        "chance of playing next round (FPL)",
                        f"{pname} is doubtful with {_article(_clean(what).lower())} and a {{p}}% chance of playing.",
                        (ev.id, ev.value),
                        entity_id=pid,
                    )
                )
                continue
        if any(ch.isdigit() for ch in what):
            what = status  # e.g. "Suspended until 25 Oct": the date is not ledgered, so keep only the status
        facts += _keep(
            _fact(
                tb,
                pname,
                "availability (FPL)",
                f"{pname} is {STATUS_WORDS.get(status, status)}"
                + (
                    f" with {_article(_clean(what).lower())}"
                    if status in ("injured", "doubtful") and what != status
                    else ""
                )
                + ".",
                entity_id=pid,
            )
        )
    return facts


def _clean(text: str) -> str:
    return text.replace(" - Unknown return date", "").strip()


def headlines(tb: Toolbox, club: str, days: int = 7, limit: int = 4) -> list[Fact]:
    rows = tb.con.execute(
        """SELECT outlet, title FROM news_items WHERE list_contains(club_ids, ?) AND published_at >= ?
           ORDER BY published_at DESC""",
        [club, tb.now - __import__("datetime").timedelta(days=days)],
    ).fetchall()
    out, seen = [], set()
    for outlet, title in rows:
        if (
            not title
            or any(ch.isdigit() for ch in title)
            or title in seen
            or "as it happened" in title.lower()
        ):
            continue  # titles with numbers would put unledgered digits into prose
        seen.add(title)
        t = title.strip().rstrip(".")
        out.append(_fact(tb, outlet, "headline", f"According to {outlet}, {t}."))
        if len(out) >= limit:
            break
    return _keep(*out)


# ---- Transfer & Valuation ----------------------------------------------------------------------------------
def squad_market(tb: Toolbox, club: str, player_id: str | None = None, n: int = 3) -> list[Fact]:
    facts: list[Fact] = []
    ids = (
        [player_id]
        if player_id
        else [
            r[0]
            for r in tb.con.execute(
                """SELECT o.entity_id FROM model_outputs o JOIN players p ON p.player_id = o.entity_id
               WHERE o.model = 'realism' AND o.metric = 'sale_likelihood' AND p.club_id = ?
               ORDER BY o.value_num DESC LIMIT ?""",
                [club, n],
            ).fetchall()
        ]
    )
    for pid in ids:
        pname = tb.name(pid)
        rec = tb.model("realism", "player", pid, "sale_likelihood")
        facts += _keep(
            _fact(
                tb,
                pname,
                "estimated chance of being sold for a fee in a window (no academy or contract-length signal)",
                f"We estimate {pname}'s chance of being sold in a transfer window at {{p}}.",
                rec,
                "pct",
                "an estimate from comparable players, not a report that he is for sale",
                pid,
            )
        )
    top = (
        [player_id]
        if player_id
        else [
            r[0]
            for r in tb.con.execute(
                """SELECT e.entity_id FROM evidence_ledger e JOIN players p ON p.player_id = e.entity_id
               WHERE e.metric = 'market_value' AND p.club_id = ? ORDER BY e.fetched_at DESC, e.value_num DESC""",
                [club],
            ).fetchall()
        ]
    )
    if not top:  # no squad-file values (leagues without FPL): transfermarkt-datasets' latest valuation
        top = [
            r[0]
            for r in tb.con.execute(
                """SELECT v.player_id FROM market_values v JOIN players p USING (player_id)
                   WHERE v.source = 'transfermarkt_datasets' AND p.club_id = ?
                   QUALIFY row_number() OVER (PARTITION BY v.player_id ORDER BY v.date DESC) = 1
                   ORDER BY v.value_eur DESC""",
                [club],
            ).fetchall()
        ]
    for pid in list(dict.fromkeys(top))[:n]:
        pname = tb.name(pid)
        mv = tb.latest("player", pid, "market_value", "transfermarkt")
        mv_rec = (mv.id, mv.value) if mv is not None else None
        label = "market value (Transfermarkt)"
        if mv is None:
            mv_rec, when = _dataset_value(tb, pid)
            label = f"market value (Transfermarkt, as of {when:%B %Y})" if when else label
        if mv_rec is not None:
            facts += _keep(
                _fact(
                    tb,
                    pname,
                    label,
                    f"Transfermarkt valued {pname} at {{p}}."
                    if mv is None
                    else f"Transfermarkt values {pname} at {{p}}.",
                    mv_rec,
                    "eur_m",
                    entity_id=pid,
                )
            )
        ff = tb.model("valuation", "player", pid, "fair_fee")
        gloss = ""
        mv_num = float(mv_rec[1]) if mv_rec is not None and mv_rec[1] is not None else None
        if mv_num and ff is not None:
            r = float(ff[1]) / mv_num
            gloss = (
                "above his market value"
                if r > 1.15
                else "below his market value"
                if r < 0.85
                else "in line with his market value"
            )
        facts += _keep(
            _fact(
                tb,
                pname,
                f"estimated fair fee if sold to another {tb.league(club)['name']} club",
                f"Our fair-fee estimate for {pname} is {{p}}" + (f", {gloss}." if gloss else "."),
                ff,
                "eur_m",
                gloss,
                pid,
            )
        )
    rows = tb.con.execute(
        """SELECT c.player_id FROM contracts c JOIN players p USING (player_id)
           WHERE p.club_id = ? AND c.expires <= ? AND c.expires >= ? AND (? IS NULL OR c.player_id = ?)
           ORDER BY c.expires""",
        [club, tb.now.date().replace(year=tb.now.year + 1), tb.now.date(), player_id, player_id],
    ).fetchall()
    for (pid,) in rows[:n]:
        pname = tb.name(pid)
        rec = tb.sql(
            "SELECT CAST(expires AS VARCHAR) FROM contracts WHERE player_id = ? AND source = 'transfermarkt'",
            [pid],
            entity_type="player",
            entity_id=pid,
            metric="contract_expires",
            source="transfermarkt",
            tier=3,
            fetched_at=_fetched(tb, "contracts", "player_id = ?", [pid]),
            url="https://www.transfermarkt.com/",
        )
        facts += _keep(
            _fact(
                tb,
                pname,
                "contract expiry",
                f"{pname}'s contract runs until {{p}}.",
                rec,
                "date",
                "expires within a year",
                pid,
            )
        )
    return facts


def _dataset_value(tb: Toolbox, pid: str) -> tuple[tuple[str, Any] | None, date | None]:
    """The player's latest transfermarkt-datasets valuation (CC0; the dataset stops in June), cited by SQL."""
    row = tb.con.execute(
        """SELECT max(date) FROM market_values WHERE player_id = ? AND source = 'transfermarkt_datasets'""",
        [pid],
    ).fetchone()
    if not row or row[0] is None:
        return None, None
    rec = tb.sql(
        """SELECT value_eur FROM market_values WHERE player_id = ? AND source = 'transfermarkt_datasets'
           AND date = CAST(? AS DATE)""",
        [pid, row[0].isoformat()],
        entity_type="player",
        entity_id=pid,
        metric="market_value",
        source="transfermarkt_datasets",
        tier=3,
        fetched_at=datetime.combine(row[0], datetime.min.time()).replace(tzinfo=tb.now.tzinfo),
        url="https://github.com/dcaribou/transfermarkt-datasets",
        unit="EUR",
    )
    return rec, row[0]


# ---- Scout -------------------------------------------------------------------------------------------------
# best: dream targets whatever the cost; realistic: the moves most likely to happen; gem: young and undervalued
RUMOUR_DAYS = 60  # the fact-check corpus keeps news this long (runtime.NEWS_DAYS)


def _fold_name(name: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", name.lower()) if not unicodedata.combining(c))


def _joined(club: str) -> list[tuple[str, date]]:
    """(player name, date he joined) for the club, from the committed squad file (data/reference)."""
    path = REPO_ROOT / "data" / "reference" / "transfermarkt_squads.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [
            (r["name"], date.fromisoformat(r["joined"]))
            for r in csv.DictReader(fh)
            if r["club_id"] == club and r.get("joined")
        ]


def rumours(tb: Toolbox, club: str, direction: str, limit: int = 3) -> list[Fact]:
    """Transfer talk from the Guardian's Rumour Mill (pipeline adapter `guardian_rumours`), newest first.
    direction "in": players at other clubs linked with this club; "out": this club's players linked with a move.
    Each sentence is attributed to the Guardian and must be confirmed by Fact-Check against the column's paragraph;
    it reports talk, never a fact about a transfer."""
    name = tb.name(club)
    squad = {
        _fold_name(n)
        for row in tb.con.execute("SELECT name, web_name FROM players WHERE club_id = ?", [club]).fetchall()
        for n in row
        if n
    }
    # a rumour from before a player joined his current club is about the move that already happened
    joined = {_fold_name(n): d for n, d in _joined(club)}
    facts: list[Fact] = []
    seen: set[str] = set()
    for title, published in tb.con.execute(
        """SELECT title, published_at FROM news_items WHERE source = 'guardian_rumours'
           AND list_contains(club_ids, ?) AND published_at >= ? ORDER BY published_at DESC""",
        [club, tb.now - timedelta(days=RUMOUR_DAYS)],
    ).fetchall():
        for player in (title or "").split("; "):
            key = _fold_name(player)
            ours = key in squad or key.split()[-1] in squad
            if not player or key in seen or ours != (direction == "out"):
                continue
            since = joined.get(key) or next(
                (d for n, d in joined.items() if n.split()[-1] == key.split()[-1]), None
            )
            if since is not None and published.date() < since:
                continue
            seen.add(key)
            tb.register(f"rumour:{key}", player)
            text = (
                f"According to The Guardian, {player} has been linked with a move away from {name}."
                if direction == "out"
                else f"According to The Guardian, {name} have been linked with {player}."
            )
            label = (
                f"transfer rumour ({'outgoing' if direction == 'out' else 'incoming'}), {published:%d %B %Y}"
            )
            facts += _keep(_fact(tb, "The Guardian", label, text))
            if len(facts) >= limit:
                return facts
    return facts


LEADS = {"best": "Dream target", "realistic": "Realistic target", "gem": "Hidden gem"}
# What each pick shows for the top need: who, where, price (fair fee) and the numbers behind "why this player".
# The second need gets one pick per list with its headline number only (KIND_HEADLINE).
KIND_METRICS = {
    "best": {
        "age",
        "candidate_club",
        "fair_fee",
        "market_value",
        "expected_minutes_share",
        "contract_months",
    },
    "realistic": {"age", "candidate_club", "fair_fee", "realism", "p_sold", "contract_months"},
    "gem": {"age", "candidate_club", "fair_fee", "proj_value_4y", "minutes_share_now"},
}
KIND_HEADLINE = {
    "best": {"age", "market_value"},
    "realistic": {"age", "fair_fee", "realism"},
    "gem": {"age", "proj_value_4y"},
}


def shortlist(tb: Toolbox, club: str, per_need: int = 3) -> list[Fact]:
    name = tb.name(club)
    facts: list[Fact] = []
    # signings the club has already announced (official site) shape the needs; say so first
    for (title,) in tb.con.execute(
        """SELECT value_text FROM model_outputs WHERE model = 'scout' AND entity_type = 'club' AND entity_id = ?
           AND metric = 'agreed_signing' ORDER BY context""",
        [club],
    ).fetchall():
        outlet = tb.con.execute(
            "SELECT outlet FROM news_items WHERE source = 'club_news' AND source_tier = 1 AND title = ? LIMIT 1",
            [title],
        ).fetchone()
        if outlet and title and not any(ch.isdigit() for ch in title):
            facts += _keep(
                _fact(
                    tb,
                    outlet[0],
                    "signing already announced by the club",
                    f"According to {outlet[0]}, {title.rstrip('.')}.",
                )
            )
    facts += rumours(tb, club, "in")  # who the press links with the club, before the models' own lists
    for rank in (1, 2):
        need = tb.model("scout", "club", club, "need", f"need:{rank}")
        if need is None:
            continue
        facts += _keep(
            _fact(
                tb,
                name,
                f"squad need, priority {'one' if rank == 1 else 'two'}",
                f"{name}'s {'biggest' if rank == 1 else 'second'} squad need is {{p}}.",
                need,
                entity_id=club,
            )
        )
        # three lists per need: the best fit whatever the odds, the moves most likely to happen, young players
        # who should grow into the role; several names each for the top need, the leading name for the second
        picked: list[tuple[str, str, str, str]] = []
        n_kind = per_need if rank == 1 else 1
        metrics = KIND_METRICS if rank == 1 else KIND_HEADLINE
        which = "" if rank == 1 else ", second need"
        for kind in ("best", "realistic", "gem"):
            cands = tb.con.execute(
                """SELECT entity_id, context, value_text FROM model_outputs WHERE model = 'scout'
                   AND metric = 'candidate_name' AND context LIKE ?
                   ORDER BY CAST(split_part(context, ':', 5) AS INTEGER)""",
                [f"{club}:need:{rank}:{kind}:%"],
            ).fetchall()
            fresh = [(kind, *c) for c in cands if c[0] not in {p[1] for p in picked}]
            picked += fresh[:n_kind]
        for kind, pid, ctx, cname in picked:
            lead = LEADS[kind]
            tb.register(pid, cname)
            cclub = tb.con.execute(
                "SELECT value_text FROM model_outputs WHERE model='scout' AND entity_id=? AND metric='candidate_club' "
                "AND context=?",
                [pid, ctx],
            ).fetchone()[0]
            tb.register(f"club_name:{cclub}", cclub)  # names like "TSG 1899 Hoffenheim" are allowed digits
            for metric, label, tmpl, fmt in (
                (
                    "age",
                    f"age ({kind} target{which})",
                    f"{lead}: {cname} ({cclub}), who is {{p}} years old.",
                    "int",
                ),
                ("candidate_club", "current club", f"{cname} plays for {{p}}.", None),
                (
                    "minutes_share_now",
                    "share of his club's minutes over the last year",
                    f"{cname} has played {{p}} of {cclub}'s minutes over the last year.",
                    "pct",
                ),
                (
                    "p_sold",
                    "chance his club sells him in a window",
                    f"We estimate a {{p}} chance that {cclub} sell {cname} in a transfer window.",
                    "pct",
                ),
                (
                    "contract_months",
                    "months left on his contract",
                    f"{cname} has {{p}} months left on his contract.",
                    "int",
                ),
                ("market_value", "market value", f"{cname} has a market value of {{p}}.", "eur_m"),
                (
                    "fair_fee",
                    "estimated fair fee",
                    f"Our fair-fee estimate for {cname} is {{p}}.",
                    "eur_m",
                ),
                (
                    "expected_minutes_share",
                    "expected share of minutes at the club",
                    f"We project {cname} to play {{p}} of {name}'s minutes.",
                    "pct",
                ),
                (
                    "proj_value_4y",
                    "projected market value in a few seasons",
                    f"We project {cname}'s market value to reach {{p}}.",
                    "eur_m",
                ),
                (
                    "realism",
                    "chance the move happens in a window (sale likelihood x this club's share of buyers)",
                    f"We estimate a {{p}} chance that {cname} joins {name} in a transfer window.",
                    "pct1",
                ),
            ):
                if metric not in metrics[kind]:
                    continue
                rec = tb.model("scout", "player", pid, metric, ctx)
                gloss = ""
                if metric == "realism" and rec is not None:
                    # any one specific move is unlikely; say so, so the prose cannot call it "a strong chance"
                    p = float(rec[1])
                    gloss = (
                        "a long shot, as almost every specific move is; compare candidates with each other"
                        if p < 0.02
                        else "relatively plausible for a specific move"
                    )
                facts += _keep(_fact(tb, cname, label, tmpl, rec, fmt, gloss, pid))
    return facts


# ---- Player Development (potential) ------------------------------------------------------------------------
def _text(tb: Toolbox, model: str, pid: str, metric: str, context: str = "") -> str | None:
    row = tb.con.execute(
        "SELECT value_text FROM model_outputs WHERE model = ? AND entity_type = 'player' AND entity_id = ? "
        "AND metric = ? AND context = ?",
        [model, pid, metric, context],
    ).fetchone()
    return row[0] if row else None


def _horizon(tb: Toolbox, pid: str, metric: str, years: int):
    """Ledger row for the date a projection refers to (publish date + horizon), so prose can say "by <date>"."""
    return tb.sql(
        f"""SELECT CAST(CAST(as_of AS DATE) + INTERVAL {years} YEAR AS VARCHAR) FROM model_outputs
            WHERE model = 'potential' AND entity_type = 'player' AND entity_id = ? AND metric = ?""",
        [pid, metric],
        entity_type="player",
        entity_id=pid,
        metric="projection_date",
        source="dugout_model:potential",
        tier=3,
        fetched_at=tb.now,
        url=MODEL_DOCS["potential"],
    )


def player_potential(tb: Toolbox, pid: str) -> list[Fact]:
    name = tb.name(pid)
    if tb.model("potential", "player", pid, "proj_value_4y") is None:
        return []
    club = _text(tb, "lookup", pid, "club_name") or ""
    facts = _keep(
        _fact(
            tb,
            name,
            "age",
            f"{name} is {{p}} years old.",
            tb.model("lookup", "player", pid, "age"),
            "int",
            entity_id=pid,
        ),
        _fact(
            tb,
            name,
            "market value today",
            f"{name}{f' ({club})' if club else ''} is valued at {{p}} today, "
            f"{_text(tb, 'lookup', pid, 'level_now') or ''}.",
            tb.model("lookup", "player", pid, "market_value"),
            "eur_m",
            _text(tb, "lookup", pid, "level_now") or "",
            pid,
        ),
    )
    for metric, label, tmpl, level in (
        (
            "proj_value_2y",
            "projected market value, short horizon (median)",
            f"We project {name}'s value at {{p}} by {{d}}.",
            None,
        ),
        (
            "proj_value_4y",
            "projected market value, long horizon (median)",
            f"We project {name}'s value at {{p}} by {{d}}, {{level}}.",
            "level_4y",
        ),
        (
            "ceiling_value_4y",
            "optimistic case, long horizon (90th percentile)",
            f"In our optimistic case {name} would be worth {{p}} by {{d}}, {{level}}.",
            "level_ceiling",
        ),
        (
            "floor_value_4y",
            "cautious case, long horizon (10th percentile)",
            f"In our cautious case {name} would be worth {{p}} by {{d}}.",
            None,
        ),
    ):
        rec = tb.model("potential", "player", pid, metric)
        if rec is None:
            continue
        words = _text(tb, "potential", pid, level) if level else ""
        f = _fact(tb, name, label, tmpl.replace("{level}", words or ""), rec, "eur_m", words or "", pid)
        f.when = (_horizon(tb, pid, metric, 2 if "2y" in metric else 4)[0], "date")
        facts.append(f)
    role = tb.model("roles", "player", pid, "role", "2025-26")
    facts += _keep(
        _fact(
            tb,
            name,
            "role last season",
            f"Last season {name} played as a {{p}}.",
            role,
            entity_id=pid,
        )
    )
    return facts


# ---- Player Fit --------------------------------------------------------------------------------------------
def player_fit(tb: Toolbox, pid: str, club: str) -> list[Fact]:
    name, cname = tb.name(pid), tb.name(club)
    group = tb.model("fit", "player", pid, "position_group", club)
    if group is None:
        return []
    facts = _keep(
        _fact(tb, name, "position group", f"{name} is a {{p}}.", group, entity_id=pid),
        _fact(
            tb,
            name,
            f"expected share of {cname}'s minutes",
            f"We project {name} to play {{p}} of {cname}'s minutes.",
            tb.model("fit", "player", pid, "expected_minutes_share", club),
            "pct",
            entity_id=pid,
        ),
        _fact(
            tb,
            name,
            f"value rank among {cname}'s current options at his position",
            f"By market value {name} would rank {{p}} among {cname}'s current options in his position.",
            tb.model("fit", "player", pid, "rank_at_position", club),
            "ord",
            entity_id=pid,
        ),
    )
    need = _text(tb, "fit", pid, "fills_need", club)
    if need:
        facts += _keep(
            _fact(
                tb,
                name,
                "position is a top squad need",
                f"{name}'s position, {{p}}, is {'among' if need == 'yes' else 'not among'} "
                f"{cname}'s biggest squad needs, by our analysis.",
                group,
                entity_id=pid,
            )
        )
    style = tb.model("fit", "player", pid, "style_fit", club)
    mgr = tb.con.execute(
        "SELECT name FROM managers WHERE club_id = ? AND source = 'wikidata'", [club]
    ).fetchone()
    if style and mgr:
        facts += _keep(
            _fact(
                tb,
                name,
                "style similarity to the head coach's preferred profile (descriptive)",
                f"{name}'s style fit with the profile {mgr[0]} uses in his position is {{p}}.",
                style,
                "score",
                "descriptive only; it does not predict minutes",
                pid,
            )
        )
    return facts


def _article(phrase: str) -> str:
    return f"{'an' if phrase[:1] in 'aeiou' else 'a'} {phrase}"
