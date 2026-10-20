"""How a player plays: per-90 style percentiles, a named role and similar players, from API-Football season totals.

Data: `player_season_stats` (API-Football free plan, league totals per club and season, 2023-24 and 2024-25;
`dugout_pipeline.adapters.api_football_stats`), mapped to Transfermarkt ids. Box-score counts only: no
positions on the pitch, passing networks or aerial duels (the free plan reports duels without splitting them).

1. Per player-season-league with MIN_MINUTES or more: per-90 rates of chances created (key passes), dribbles
   completed, shots, goals, passes, tackles, interceptions, blocks, duels, fouls won and committed, and the share
   of duels won.
2. Position group from where he starts (Transfermarkt line-ups, `positions`), merged into five style groups:
   centre-backs, full-backs, midfielders, wingers, centre-forwards. Goalkeepers are left out (the free data has
   saves and goals conceded only).
3. Percentiles within the style group, the season and the league tier (TIER_NAMES: Europe's biggest leagues = TOP5,
   then the rest), so a Dutch full-back is compared with full-backs of comparable leagues.
4. Roles: features standardised within (season, tier, group); each player scored on the group's archetypes
   (ARCHETYPES: transparent weights on the features, e.g. a ball-winning midfielder = tackles, interceptions and
   duels); his role is the top archetype when it clearly leads (ROLE_MIN, ROLE_MARGIN), else none is claimed.
   Clustering (a Gaussian mixture per group, components named by archetype) was tried first and is kept as the
   comparison in the backtest: box-score styles form a continuum, the clusters separated weakly and did not
   survive resampling (roles_api.md).
5. Similar players: cosine similarity of the standardised features within the group and season.

Evaluation (`dugout-models backtest style`, docs/backtests/roles_api.md): silhouette, bootstrap stability, the
same player's role and nearest neighbour across seasons, and a table of well-known players.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import partial

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from sklearn.mixture import GaussianMixture

log = logging.getLogger(__name__)

MIN_MINUTES = 900  # as for the league goals-and-assists ranking (market.PROFILE_MIN_MINUTES)
MIN_POOL = 30  # players in a (season, tier, group) pool before percentiles mean anything
MIN_DUELS = 30  # duels before a share of duels won means anything
# a role is claimed when the top archetype score is at least ROLE_MIN standard deviations and ROLE_MARGIN ahead of
# the next archetype (else he is an all-rounder in box-score terms and no role is named)
ROLE_MIN, ROLE_MARGIN = 0.5, 0.25
SEED = 7

# Transfermarkt competition ids of Europe's top five leagues; every other league is the second tier
TOP5 = {"GB1", "ES1", "IT1", "L1", "FR1"}
# no number words: the Stat Auditor counts "five" as a number
TIER_NAMES = {1: "Europe's biggest leagues", 2: "the other leagues we cover"}
# API-Football league id -> Transfermarkt competition id (configured leagues and candidate leagues)
API_TO_TM = {
    39: "GB1",
    140: "ES1",
    135: "IT1",
    78: "L1",
    61: "FR1",
    88: "NL1",
    40: "GB2",
    94: "PO1",
    144: "BE1",
    203: "TR1",
    179: "SC1",
    197: "GR1",
    333: "UKR1",
    119: "DK1",
    235: "RU1",
    218: "A1",
    207: "C1",
}

# feature -> (how the site names it, plural noun phrase after "for")
LABELS = {
    "key_passes": "chances created",
    "dribbles": "dribbles completed",
    "shots": "shots",
    "goals": "goals",
    "passes": "passes",
    "tackles": "tackles",
    "interceptions": "interceptions",
    "blocks": "blocks",
    "duels": "duels contested",
    "duel_win": "share of duels won",
    "fouls_drawn": "fouls won",
    "fouls_committed": "fouls committed",
}
# features never sold as a strength
NOT_A_STRENGTH = {"fouls_committed"}

# Transfermarkt position group (market.GROUPS) -> style group
STYLE_GROUP = {
    "centre-back": "centre-back",
    "full-back": "full-back",
    "defensive midfield": "midfielder",
    "central midfield": "midfielder",
    "winger": "winger",
    "centre-forward": "centre-forward",
}
GROUP_PLURAL = {
    "centre-back": "centre-backs",
    "full-back": "full-backs",
    "midfielder": "midfielders",
    "winger": "wingers",
    "centre-forward": "centre-forwards",
}

FEATURES = {
    "centre-back": [
        "passes",
        "key_passes",
        "dribbles",
        "tackles",
        "interceptions",
        "blocks",
        "duels",
        "duel_win",
    ],
    "full-back": [
        "key_passes",
        "dribbles",
        "passes",
        "shots",
        "tackles",
        "interceptions",
        "duels",
        "duel_win",
    ],
    "midfielder": [
        "key_passes",
        "dribbles",
        "shots",
        "passes",
        "tackles",
        "interceptions",
        "duels",
        "duel_win",
        "fouls_drawn",
    ],
    "winger": ["key_passes", "dribbles", "shots", "goals", "passes", "tackles", "duels", "fouls_drawn"],
    "centre-forward": [
        "goals",
        "shots",
        "key_passes",
        "dribbles",
        "passes",
        "duels",
        "duel_win",
        "fouls_drawn",
        "tackles",
        "fouls_committed",
    ],
}

# Role archetypes: weights on standardised features (each archetype's weights are scaled to unit length, so a
# score is a projection in standard deviations). Transparent by design: the names and the weights are ours.
ARCHETYPES: dict[str, dict[str, dict[str, float]]] = {
    "centre-back": {
        "ball-playing centre-back": {"passes": 1.0, "key_passes": 0.5, "dribbles": 0.5},
        "front-foot defender": {"tackles": 1.0, "duels": 1.0, "duel_win": 0.3},
        "covering centre-back": {"interceptions": 1.0, "blocks": 1.0, "passes": -0.3},
    },
    "full-back": {
        "creative full-back": {"key_passes": 1.0, "shots": 0.3},
        "overlapping dribbler": {"dribbles": 1.0, "duels": 0.3},
        "defensive full-back": {"tackles": 1.0, "interceptions": 1.0, "key_passes": -0.5},
        "possession full-back": {"passes": 1.0, "key_passes": -0.3, "dribbles": -0.3},
    },
    "midfielder": {
        "ball-winning midfielder": {"tackles": 1.0, "interceptions": 0.7, "duels": 0.5, "duel_win": 0.5},
        "deep-lying playmaker": {"passes": 1.0, "interceptions": 0.3, "shots": -0.5, "dribbles": -0.3},
        "creative midfielder": {"key_passes": 1.0, "dribbles": 0.5, "fouls_drawn": 0.3},
        "box-to-box midfielder": {"shots": 0.7, "tackles": 0.5, "duels": 0.5, "dribbles": 0.3},
    },
    "winger": {
        "creative winger": {"key_passes": 1.0, "passes": 0.5},
        "direct dribbler": {"dribbles": 1.0, "fouls_drawn": 0.5, "duels": 0.3},
        "inside forward": {"shots": 1.0, "goals": 1.0, "key_passes": -0.3},
        "hard-working winger": {"tackles": 1.0, "dribbles": -0.3, "shots": -0.3},
    },
    "centre-forward": {
        "penalty-box striker": {"goals": 1.0, "shots": 1.0, "passes": -0.5, "duels": -0.3},
        "target forward": {"duels": 1.0, "fouls_drawn": 0.7, "duel_win": 0.3, "dribbles": -0.5},
        "pressing forward": {"tackles": 1.0, "fouls_committed": 0.7, "duels": 0.3},
        "creative forward": {"key_passes": 1.0, "dribbles": 0.7, "passes": 0.5},
    },
}


def season_label(season: int) -> str:
    return f"{season}-{(season + 1) % 100:02d}"


def load_stats(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """League totals per player, season and league (a mid-season move within a league adds up), with
    Transfermarkt ids. Empty when the table does not exist yet."""
    try:
        df = con.execute(
            """SELECT tm_player_id, season, league_id, any_value(player_name) AS player_name,
                      arg_max(team_name, minutes) AS team_name, sum(minutes) AS minutes,
                      sum(coalesce(key_passes, 0)) AS key_passes, sum(coalesce(dribbles_won, 0)) AS dribbles,
                      sum(coalesce(shots, 0)) AS shots, sum(coalesce(goals, 0)) AS goals,
                      sum(coalesce(passes, 0)) AS passes, sum(coalesce(tackles, 0)) AS tackles,
                      sum(coalesce(interceptions, 0)) AS interceptions, sum(coalesce(blocks, 0)) AS blocks,
                      sum(coalesce(duels, 0)) AS duels, sum(coalesce(duels_won, 0)) AS duels_won,
                      sum(coalesce(fouls_drawn, 0)) AS fouls_drawn,
                      sum(coalesce(fouls_committed, 0)) AS fouls_committed,
                      arg_max(position, minutes) AS api_position
               FROM player_season_stats WHERE domestic AND tm_player_id IS NOT NULL
               GROUP BY tm_player_id, season, league_id"""
        ).df()
    except duckdb.CatalogException:
        return pd.DataFrame()
    df["tm_player_id"] = df.tm_player_id.astype(str)
    df["comp"] = df.league_id.map(API_TO_TM)
    df["tier"] = np.where(df.comp.isin(TOP5), 1, 2)
    return df


def per90(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for f in LABELS:
        if f == "duel_win":
            continue
        out[f] = out[f] / out.minutes * 90
    out["duel_win"] = np.where(df.duels >= MIN_DUELS, df.duels_won / df.duels.where(df.duels > 0), np.nan)
    return out


def _pct(values: np.ndarray, pool: np.ndarray) -> np.ndarray:
    """Share of the pool below each value (ties count half): 0.9 = ahead of 90% of the pool."""
    pool = np.sort(pool[~np.isnan(pool)])
    if not len(pool):
        return np.full(len(values), np.nan)
    lo = np.searchsorted(pool, values, side="left")
    hi = np.searchsorted(pool, values, side="right")
    out = (lo + hi) / 2 / len(pool)
    return np.where(np.isnan(values), np.nan, out)


@dataclass
class StyleModel:
    """Fitted on every player-season with enough minutes (see the module docstring)."""

    min_minutes: int = MIN_MINUTES
    profiles: pd.DataFrame = field(default_factory=pd.DataFrame)  # one row per player-season-league

    @classmethod
    def from_warehouse(cls, con: duckdb.DuckDBPyConnection, group_of: pd.Series) -> StyleModel:
        """`group_of`: Transfermarkt player id -> the position group he starts in most (market.Market.main_group)."""
        m = cls()
        m.fit(load_stats(con), group_of)
        return m

    def fit(self, stats: pd.DataFrame, group_of: pd.Series) -> None:
        if stats.empty:
            self.profiles = pd.DataFrame()
            return
        p = per90(stats[stats.minutes >= self.min_minutes])
        p["group"] = p.tm_player_id.map(
            group_of.astype(str).map(STYLE_GROUP).to_dict() if len(group_of) else {}
        )
        p = p.dropna(subset=["group"]).reset_index(drop=True)
        # standardise within (season, tier, group): "relative to his league tier that season"
        for g, feats in FEATURES.items():
            sel = p.group == g
            for _, idx in p[sel].groupby(["season", "tier"]).groups.items():
                block = p.loc[idx, feats]
                z = (block - block.mean()) / block.std(ddof=0).replace(0, np.nan)
                p.loc[idx, [f"z_{f}" for f in feats]] = z.to_numpy()
                pool_ok = len(idx) >= MIN_POOL
                for f in feats:
                    vals = p.loc[idx, f].to_numpy(dtype=float)
                    p.loc[idx, f"pct_{f}"] = _pct(vals, vals) if pool_ok else np.nan
        p["role"], p["role_score"] = None, np.nan
        for g in FEATURES:
            sel = (p.group == g).to_numpy()
            if sel.sum():
                labels, score = archetype_roles(p.loc[sel, [f"z_{f}" for f in FEATURES[g]]], g)
                p.loc[sel, "role"] = labels
                p.loc[sel, "role_score"] = score
        self.profiles = p

    # -- queries -----------------------------------------------------------------------------------------
    def latest(self, tm_id: str) -> pd.Series | None:
        """His newest season with enough minutes (the league he played most in that season)."""
        if self.profiles.empty:
            return None
        rows = self.profiles[self.profiles.tm_player_id == str(tm_id)]
        if rows.empty:
            return None
        return rows.sort_values(["season", "minutes"]).iloc[-1]

    def pool(self, season: int, tier: int, group: str) -> pd.DataFrame:
        p = self.profiles
        return p[(p.season == season) & (p.tier == tier) & (p.group == group)]

    def percentiles(self, row: pd.Series, group: str) -> dict[str, float]:
        """His percentile for each of `group`'s features against that group's pool in his season and tier (his own
        group's pool for his role; the need's group when he is measured for a position he plays less)."""
        pool = self.pool(row.season, row.tier, group)
        if len(pool) < MIN_POOL:
            return {}
        out = {}
        for f in FEATURES[group]:
            v = float(row[f]) if f in row and row[f] == row[f] else np.nan
            if v == v:
                out[f] = float(_pct(np.array([v]), pool[f].to_numpy(dtype=float))[0])
        return out

    def similar(self, row: pd.Series, n: int = 3) -> list[tuple[str, str, float]]:
        """(Transfermarkt id, name, cosine similarity) of the n most similar players in his group and season."""
        feats = [f"z_{f}" for f in FEATURES[row.group]]
        grp = self.profiles[(self.profiles.group == row.group) & (self.profiles.season == row.season)]
        grp = grp[grp.tm_player_id != row.tm_player_id].dropna(subset=feats)
        v = row[feats].to_numpy(dtype=float)
        if np.isnan(v).any() or grp.empty:
            return []
        Z = grp[feats].to_numpy(dtype=float)
        cos = Z @ v / (np.linalg.norm(Z, axis=1) * np.linalg.norm(v) + 1e-12)
        top = np.argsort(-cos)[:n]
        return [(grp.tm_player_id.iloc[i], grp.player_name.iloc[i], float(cos[i])) for i in top]


ADDS_MIN, ADDS_GAP = (
    0.7,
    0.25,
)  # he adds a feature when he is at or above this and this far ahead of the starter
LACKS_MAX = 0.35  # a starter's feature at or below this is what the side lacks there


def describe(m: StyleModel, tm_id: str, need: str) -> dict | None:
    """How a player plays, measured for a need (market position group): his newest season, the pool he is
    compared with, his role (when he plays in the need's style group) and percentiles against that group."""
    g = STYLE_GROUP.get(need)
    row = m.latest(tm_id) if g else None
    if row is None:
        return None
    pct = m.percentiles(row, g)
    if not pct:
        return None
    return {
        "season": season_label(int(row.season)),
        "tier": int(row.tier),
        "pool": f"{GROUP_PLURAL[g]} in {TIER_NAMES[int(row.tier)]}",
        "role": row.role if row.group == g and isinstance(row.role, str) else None,
        "pct": pct,
        "similar": [n for _, n, c in m.similar(row, 2) if c >= 0.8] if row.group == g else [],
    }


def fit_vs(cand: dict | None, starter: dict | None) -> tuple[str, float, float] | None:
    """The feature the candidate adds most over the starter he would replace (same pool, same tier): (feature, his
    percentile, the starter's), or None when he adds nothing clear."""
    if not cand or not starter or cand["tier"] != starter["tier"]:
        return None
    gains = [
        (f, v, starter["pct"][f])
        for f, v in cand["pct"].items()
        if f in starter["pct"]
        and f not in NOT_A_STRENGTH
        and v >= ADDS_MIN
        and v - starter["pct"][f] >= ADDS_GAP
    ]
    return max(gains, key=lambda x: x[1] - x[2]) if gains else None


def lacks(starter: dict | None, n: int = 2) -> list[tuple[str, float]]:
    """The starter's weakest features (percentile <= LACKS_MAX), weakest first."""
    if not starter:
        return []
    low = [(f, v) for f, v in starter["pct"].items() if v <= LACKS_MAX and f not in NOT_A_STRENGTH]
    return sorted(low, key=lambda x: x[1])[:n]


def standouts(pct: dict[str, float], n: int = 3, floor: float = 0.75) -> list[tuple[str, float]]:
    """His strongest features (percentile >= floor), best first."""
    good = [(f, v) for f, v in pct.items() if v >= floor and f not in NOT_A_STRENGTH]
    return sorted(good, key=lambda x: -x[1])[:n]


def top_share(pct: float) -> float:
    """'Top 10%' style: the share of the pool at or above him, rounded up to a 5% step (a claim that stays true; the
    best in the pool is in the top 5%, not the top 0%)."""
    return max(0.05, float(np.ceil(round((1 - pct) * 100, 6) / 5) * 5 / 100)) if pct == pct else np.nan


def archetype_scores(z: pd.DataFrame, group: str) -> pd.DataFrame:
    """Players x archetypes: his standardised features projected on each archetype's unit-length weights."""
    feats = [c.removeprefix("z_") for c in z.columns]
    arche = ARCHETYPES[group]
    W = np.array([[arche[a].get(f, 0.0) for f in feats] for a in arche])
    W = W / np.linalg.norm(W, axis=1, keepdims=True)
    return pd.DataFrame(z.fillna(0.0).to_numpy() @ W.T, index=z.index, columns=list(arche))


def archetype_roles(z: pd.DataFrame, group: str) -> tuple[np.ndarray, np.ndarray]:
    """(role or None, top score) per player: the leading archetype when it clearly leads."""
    s = archetype_scores(z, group).to_numpy()
    order = np.sort(s, axis=1)
    top, second = order[:, -1], order[:, -2]
    names = np.array(list(ARCHETYPES[group]), dtype=object)[s.argmax(axis=1)]
    clear = (top >= ROLE_MIN) & (top - second >= ROLE_MARGIN)
    return np.where(clear, names, None), top


def fit_group(
    grp: pd.DataFrame, group: str, seed: int = SEED
) -> tuple[np.ndarray, np.ndarray, GaussianMixture, list[str]]:
    """The rejected alternative, kept for the backtest's comparison: a Gaussian mixture on the group's standardised
    features (k by BIC, 2..number of archetypes), components named by archetype (Hungarian assignment of centres to
    archetype weights). Returns labels, the posterior of the chosen component, the mixture and the names."""
    feats = FEATURES[group]
    Z = grp[[f"z_{f}" for f in feats]].fillna(0.0).to_numpy()
    arche = ARCHETYPES[group]
    ks = range(2, len(arche) + 1)
    mix = min(
        (GaussianMixture(k, covariance_type="diag", random_state=seed, n_init=5).fit(Z) for k in ks),
        key=lambda g: g.bic(Z),
    )
    names = name_components(mix.means_, feats, arche)
    post = mix.predict_proba(Z)
    comp = post.argmax(axis=1)
    return np.array([names[c] for c in comp]), post.max(axis=1), mix, names


def name_components(centres: np.ndarray, feats: list[str], arche: dict[str, dict[str, float]]) -> list[str]:
    names = list(arche)
    W = np.array([[arche[a].get(f, 0.0) for f in feats] for a in names])  # archetypes x features
    score = centres @ W.T  # components x archetypes
    rows, cols = linear_sum_assignment(-score)
    out = [""] * len(centres)
    for r, c in zip(rows, cols, strict=True):
        out[r] = names[c]
    return out


def tm_groups() -> pd.Series:
    """Transfermarkt player id -> the position group he starts in most (listed position below GROUP_STARTS
    recorded starts), as market.Market.main_group, straight from transfermarkt-datasets."""
    from dugout_models.market import GROUP_OF
    from dugout_models.positions import group_weights, played
    from dugout_models.valuation import TM_DIR

    players = pd.read_parquet(TM_DIR / "players.parquet", columns=["player_id", "sub_position"])
    listed = players.set_index(players.player_id.astype(str)).sub_position
    w = group_weights(played(), listed, GROUP_OF)
    return (
        w.sort_values(["weight", "group"])
        .drop_duplicates("player_id", keep="last")
        .set_index("player_id")
        .group
    )


# well-known players for the sanity table (name as API-Football writes it, matched as a substring)
SANITY = [
    "Virgil van Dijk",
    "W. Saliba",
    "J. Gvardiol",
    "Alexander-Arnold",
    "A. Robertson",
    "D. Udogie",
    "D. Rice",
    "M. Caicedo",
    "R. Gravenberch",
    "B. Fernandes",
    "M. Ødegaard",
    "C. Palmer",
    "Mohamed Salah",
    "B. Saka",
    "J. Doku",
    "E. Haaland",
    "A. Isak",
    "C. Wood",
    "J. Mateta",
    "O. Watkins",
    "Y. Wissa",
]


def backtest(con: duckdb.DuckDBPyConnection, out_dir=None, n_boot: int = 20) -> dict:
    """Coverage, cluster quality (silhouette), bootstrap stability, the same players across seasons, and a sanity
    table of well-known players. Writes roles_api.{md,json}."""
    import json
    from datetime import UTC, datetime

    m = StyleModel()
    m.fit(load_stats(con), tm_groups())
    p = m.profiles
    rep: dict = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "min_minutes": MIN_MINUTES}
    try:
        raw = con.execute(
            """SELECT season, league_name, count(DISTINCT team_id) AS clubs, count(*) AS rows,
                      count(tm_player_id) AS matched, sum(CASE WHEN minutes >= ? THEN 1 ELSE 0 END) AS enough
               FROM player_season_stats WHERE domestic GROUP BY 1, 2 ORDER BY 1 DESC, 2""",
            [MIN_MINUTES],
        ).df()
    except duckdb.CatalogException:
        raw = pd.DataFrame()
    rep["coverage"] = raw.to_dict("records")
    rng = np.random.default_rng(SEED)
    rep["groups"] = {}
    for g in FEATURES:
        grp = p[p.group == g] if not p.empty else p
        if len(grp) < MIN_POOL:
            rep["groups"][g] = {"players": len(grp), "fitted": False}
            continue
        Z = grp[[f"z_{f}" for f in FEATURES[g]]].fillna(0.0).to_numpy()
        gmm_labels, gmm_post, mix, _ = fit_group(grp, g)
        rep["groups"][g] = {
            "players": len(grp),
            "fitted": True,
            "roles": {str(k): int(v) for k, v in grp.role.fillna("no clear role").value_counts().items()},
            "named_share": float(grp.role.notna().mean()),
            **_quality(grp, Z, grp.role.to_numpy(), partial(_restandardised, grp, g), rng, n_boot),
            "mixture": {
                "k": int(mix.n_components),
                "roles": {str(k): int(v) for k, v in pd.Series(gmm_labels).value_counts().items()},
                "mean_posterior": float(gmm_post.mean()),
                **_quality(grp, Z, gmm_labels, partial(_refit_mixture, grp, g, rng), rng, n_boot),
            },
            **_across_seasons(grp, g),
        }
    rep["sanity"] = _sanity(m)
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "roles_api.json").write_text(json.dumps(rep, indent=2, default=str))
        (out_dir / "roles_api.md").write_text(_report(rep), encoding="utf-8")
    return rep


def _quality(grp: pd.DataFrame, Z: np.ndarray, labels: np.ndarray, refit, rng, n_boot: int) -> dict:
    """Silhouette over labelled players (and the same labels shuffled), and the mean adjusted Rand index between
    the labels and labels refitted on bootstrap resamples (unlabelled players count as one class)."""
    from sklearn.metrics import adjusted_rand_score, silhouette_score

    lab = pd.Series(labels, index=grp.index).fillna("none")
    named = (lab != "none").to_numpy()
    ok = named.sum() > 2 and lab[named].nunique() > 1
    sil = float(silhouette_score(Z[named], lab[named])) if ok else float("nan")
    shuf = float(silhouette_score(Z[named], rng.permutation(lab[named].to_numpy()))) if ok else float("nan")
    aris = []
    for _ in range(n_boot):
        idx = rng.choice(len(grp), size=len(grp), replace=True)
        boot = pd.Series(refit(idx), index=grp.index[idx]).fillna("none").groupby(level=0).first()
        aris.append(adjusted_rand_score(lab.loc[boot.index], boot))
    return {"silhouette": sil, "silhouette_shuffled": shuf, "bootstrap_ari": float(np.mean(aris))}


def _restandardised(grp: pd.DataFrame, g: str, idx: np.ndarray) -> np.ndarray:
    """Archetype roles of a resample, its features standardised anew."""
    b = grp[FEATURES[g]].iloc[idx]
    z = (b - b.mean()) / b.std(ddof=0).replace(0, np.nan)
    return archetype_roles(z.add_prefix("z_"), g)[0]


def _refit_mixture(grp: pd.DataFrame, g: str, rng, idx: np.ndarray) -> np.ndarray:
    return fit_group(grp.iloc[idx], g, seed=int(rng.integers(1e9)))[0]


def _across_seasons(grp: pd.DataFrame, g: str) -> dict:
    """Players with enough minutes in two consecutive seasons: share keeping their role (vs agreement by chance
    from the role shares), nearest-neighbour self-retrieval among the earlier season's profiles, and the rank
    correlation of each feature's percentile."""
    from scipy.stats import spearmanr

    seasons = sorted(grp.season.unique())
    if len(seasons) < 2:
        return {"seasons": [int(s) for s in seasons], "across_seasons": None}
    a_s, b_s = seasons[-2], seasons[-1]
    a = grp[grp.season == a_s].sort_values("minutes").drop_duplicates("tm_player_id", keep="last")
    b = grp[grp.season == b_s].sort_values("minutes").drop_duplicates("tm_player_id", keep="last")
    a, b = a.set_index("tm_player_id"), b.set_index("tm_player_id")
    both = a.index.intersection(b.index)
    if len(both) < 10:
        return {"seasons": [int(a_s), int(b_s)], "across_seasons": {"players": len(both)}}
    kept = float((a.loc[both, "role"] == b.loc[both, "role"]).mean())
    chance = float((grp.role.value_counts(normalize=True) ** 2).sum())
    feats = [f"z_{f}" for f in FEATURES[g]]
    A = a[feats].fillna(0.0).to_numpy()
    A = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-12)
    ids = a.index.to_numpy()
    top1 = top5 = 0
    for pid in both:
        v = b.loc[pid, feats].fillna(0.0).to_numpy(dtype=float)
        v = v / (np.linalg.norm(v) + 1e-12)
        rank = list(ids[np.argsort(-(A @ v))]).index(pid)
        top1 += rank == 0
        top5 += rank < 5
    rho = {
        f: float(spearmanr(a.loc[both, f"pct_{f}"], b.loc[both, f"pct_{f}"], nan_policy="omit").statistic)
        for f in FEATURES[g]
    }
    return {
        "seasons": [int(a_s), int(b_s)],
        "across_seasons": {
            "players": len(both),
            "role_kept": kept,
            "role_kept_by_chance": chance,
            "nn_top1": top1 / len(both),
            "nn_top5": top5 / len(both),
            "nn_random_top1": 1 / len(a),
            "nn_random_top5": min(1.0, 5 / len(a)),
            "percentile_rank_corr": rho,
        },
    }


def _sanity(m: StyleModel) -> list[dict]:
    out: list[dict] = []
    p = m.profiles
    if p.empty:
        return out
    for name in SANITY:
        rows = p[p.player_name.str.contains(name, regex=False, na=False)]
        if rows.empty:
            continue
        r = rows.sort_values(["season", "minutes"]).iloc[-1]
        pct = m.percentiles(r, r.group)
        out.append(
            {
                "player": r.player_name,
                "season": season_label(int(r.season)),
                "group": r.group,
                "role": r.role,
                "score": round(float(r.role_score), 2) if r.role_score == r.role_score else None,
                "standouts": ", ".join(f"{LABELS[f]} {v:.0%}" for f, v in standouts(pct, 3, 0.0)),
                "similar": ", ".join(n for _, n, _ in m.similar(r, 3)),
            }
        )
    return out


def _report(rep: dict) -> str:
    L = [
        "# Playing style: roles, percentiles and similar players (API-Football season totals)",
        "",
        f"Generated {rep['generated_at']} by `uv run dugout-models backtest style`. Players with "
        f">= {rep['min_minutes']} league minutes in a season; percentiles within the style group, the season and the "
        "league tier (Europe's biggest five leagues / the rest). Model: dugout_models/style.py.",
        "",
        "## Data on hand",
        "",
        "The free plan allows 100 requests a day and three pages of 20 players per query, so the backfill "
        "(`api_football_stats`, nightly) takes a club at a time: ~3 requests a club, at most 70 a day. "
        "Coverage when this report was generated:",
        "",
        "| Season | League | Clubs | Player rows | With a Transfermarkt id | 900+ minutes |",
        "|---|---|---|---|---|---|",
    ]
    for c in rep["coverage"]:
        L.append(
            f"| {season_label(int(c['season']))} | {c['league_name']} | {c['clubs']} | {c['rows']} | "
            f"{c['matched']} ({c['matched'] / c['rows']:.0%}) | {int(c['enough'])} |"
        )
    L += [
        "",
        "## Roles",
        "",
        "Two ways to name a role, on the same standardised per-90 features (within season, tier and group):",
        "",
        "- **Archetype scores (shipped):** each player projected on transparent archetype weights (`ARCHETYPES`); "
        f"the top archetype is his role when it scores >= {ROLE_MIN} and leads the next by >= {ROLE_MARGIN} "
        "(else no role is claimed).",
        "- **Gaussian mixture (tried first):** k by BIC (2 to the number of archetypes), components named by the "
        "archetype their centre matches best.",
        "",
        "Silhouette: how well the roles separate (-1..1; the same labels shuffled give ~0). Bootstrap: mean adjusted "
        "Rand index between the roles and roles refitted on 20 resamples (1 = identical, 0 = chance; unnamed "
        "players count as one class).",
        "",
        "| Group | Players | Method | Roles (players) | Silhouette (shuffled) | Bootstrap ARI |",
        "|---|---|---|---|---|---|",
    ]
    for g, r in rep["groups"].items():
        if not r.get("fitted"):
            L.append(f"| {g} | {r['players']} | not fitted (under {MIN_POOL} players) | | | |")
            continue
        for label, x in (("archetypes", r), (f"mixture, k={r['mixture']['k']}", r["mixture"])):
            roles = "; ".join(f"{k} {v}" for k, v in x["roles"].items())
            L.append(
                f"| {g} | {r['players']} | {label} | {roles} | {x['silhouette']:.2f} ({x['silhouette_shuffled']:.2f}) "
                f"| {x['bootstrap_ari']:.2f} |"
            )
    L += ["", "## Across seasons", ""]
    any_cross = False
    for g, r in rep["groups"].items():
        x = r.get("across_seasons")
        if not x or "role_kept" not in x:
            continue
        if not any_cross:
            L += [
                "Players with 900+ minutes in both seasons: is his role the same, and is his own earlier season his "
                "nearest neighbour among all earlier-season profiles in the group?",
                "",
                "| Group | Seasons | Players | Role kept (chance) | Nearest = himself, top-1 / top-5 (random) | "
                "Percentile rank correlation, median feature |",
                "|---|---|---|---|---|---|",
            ]
            any_cross = True
        s = " to ".join(season_label(y) for y in r["seasons"])
        med = float(np.nanmedian(list(x["percentile_rank_corr"].values())))
        L.append(
            f"| {g} | {s} | {x['players']} | {x['role_kept']:.0%} ({x['role_kept_by_chance']:.0%}) | "
            f"{x['nn_top1']:.0%} / {x['nn_top5']:.0%} ({x['nn_random_top1']:.0%} / {x['nn_random_top5']:.0%}) | "
            f"{med:.2f} |"
        )
    if not any_cross:
        L.append(
            "Not measurable yet: only one season is in for any group. The backfill takes 2023-24 after the 2024-25 "
            "clubs; re-run this report then."
        )
    L += [
        "",
        "## Sanity check: well-known players (newest season on hand)",
        "",
        "Percentiles of his three strongest features against his group; similar = nearest neighbours that season.",
        "",
        "| Player | Season | Group | Role (score) | Strongest features | Similar players |",
        "|---|---|---|---|---|---|",
    ]
    for s in rep["sanity"]:
        L.append(
            f"| {s['player']} | {s['season']} | {s['group']} | {s['role'] or 'no clear role'} ({s['score']}) | {s['standouts']} | "
            f"{s['similar']} |"
        )
    L += [
        "",
        "## Limitations",
        "",
        "- Box-score counts: no positions on the pitch, passing networks or pressing data; the free plan's duels "
        'are not split into aerial and ground duels, so "target forward" leans on duels contested and fouls won.',
        "- Defensive counts depend on how much of the ball a team has; they are not possession-adjusted.",
        "- The newest season the free plan has is 2024-25: the site says which season a figure is from.",
        "- Season totals: no in-season split, so season-to-season agreement is the stability test.",
    ]
    return "\n".join(L) + "\n"
