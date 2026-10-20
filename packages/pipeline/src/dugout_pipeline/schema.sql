-- dugout warehouse schema (DuckDB). Idempotent: safe to run on every connect.
-- Conventions: *_id columns are dugout ids (club slug, reep id for people when known);
-- per-source observations keep a `source` column so the Stat Auditor can cross-check them.

CREATE TABLE IF NOT EXISTS entity_ids (
    entity_type  VARCHAR NOT NULL,            -- club | player | manager | match
    dugout_id    VARCHAR NOT NULL,
    provider     VARCHAR NOT NULL,            -- reep provider name (api_football, opta, ...) or fpl / football_data_org
    namespace    VARCHAR NOT NULL,
    external_id  VARCHAR NOT NULL,
    reep_id      VARCHAR,
    PRIMARY KEY (entity_type, provider, namespace, external_id)
);

CREATE TABLE IF NOT EXISTS clubs (
    club_id      VARCHAR PRIMARY KEY,         -- slug, e.g. liverpool
    reep_id      VARCHAR,
    name         VARCHAR NOT NULL,
    short_name   VARCHAR,
    league       VARCHAR NOT NULL,
    season       VARCHAR NOT NULL,            -- 2026-27
    venue        VARCHAR,
    address      VARCHAR,
    lat          DOUBLE,
    lon          DOUBLE,
    updated_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    player_id    VARCHAR PRIMARY KEY,         -- reep id, else '<provider>:<id>'
    reep_id      VARCHAR,
    name         VARCHAR NOT NULL,
    web_name     VARCHAR,
    birth_date   DATE,
    nationality  VARCHAR,
    position     VARCHAR,                     -- GK | DEF | MID | FWD
    club_id      VARCHAR,
    updated_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS managers (
    manager_id   VARCHAR PRIMARY KEY,
    reep_id      VARCHAR,
    name         VARCHAR NOT NULL,
    birth_date   DATE,
    nationality  VARCHAR,
    club_id      VARCHAR,
    start_date   DATE,
    source       VARCHAR NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS matches (
    match_id     VARCHAR PRIMARY KEY,         -- <league>:<season>:<home>:<away> (stable across reschedules)
    league       VARCHAR NOT NULL,
    season       VARCHAR NOT NULL,
    gameweek     INTEGER,
    kickoff_utc  TIMESTAMPTZ,
    home_club_id VARCHAR NOT NULL,
    away_club_id VARCHAR NOT NULL,
    status       VARCHAR,                     -- scheduled | live | finished | postponed
    home_goals   INTEGER,
    away_goals   INTEGER,
    updated_at   TIMESTAMPTZ NOT NULL
);

-- One row per (match, source): what each provider reports. `matches` holds the agreed value.
CREATE TABLE IF NOT EXISTS match_observations (
    match_id     VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    external_id  VARCHAR,
    status       VARCHAR,
    home_goals   INTEGER,
    away_goals   INTEGER,
    home_xg      DOUBLE,
    away_xg      DOUBLE,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (match_id, source)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    player_id    VARCHAR NOT NULL,
    match_id     VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    club_id      VARCHAR,
    minutes      INTEGER,
    goals        INTEGER,
    assists      INTEGER,
    xg           DOUBLE,
    xa           DOUBLE,
    starts       INTEGER,
    yellow       INTEGER,
    red          INTEGER,
    -- FPL / Opta-derived extras (NULL where a season did not track them; defensive stats from 2025-26)
    saves        INTEGER,
    influence    DOUBLE,
    creativity   DOUBLE,
    threat       DOUBLE,
    bps          INTEGER,
    xgc          DOUBLE,
    tackles      INTEGER,
    recoveries   INTEGER,
    cbi          INTEGER,
    defensive_contribution INTEGER,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (player_id, match_id, source)
);

CREATE TABLE IF NOT EXISTS lineups (
    match_id     VARCHAR NOT NULL,
    club_id      VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    formation    VARCHAR,
    player_id    VARCHAR NOT NULL,
    starter      BOOLEAN NOT NULL,
    position     VARCHAR,
    grid         VARCHAR,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (match_id, club_id, source, player_id)
);

-- Open-data event streams (StatsBomb, Wyscout, PFF, Metrica...) in SPADL-like columns.
CREATE TABLE IF NOT EXISTS events (
    dataset      VARCHAR NOT NULL,            -- statsbomb | wyscout_public | pff_wc2022 | metrica | skillcorner
    game_id      VARCHAR NOT NULL,
    event_id     VARCHAR NOT NULL,
    period       INTEGER,
    time_seconds DOUBLE,
    team_id      VARCHAR,
    player_id    VARCHAR,
    type_name    VARCHAR,
    result_name  VARCHAR,
    bodypart     VARCHAR,
    start_x      DOUBLE,
    start_y      DOUBLE,
    end_x        DOUBLE,
    end_y        DOUBLE,
    xg           DOUBLE,                      -- provider shot xG where available (StatsBomb)
    PRIMARY KEY (dataset, game_id, event_id)
);

-- API-Football season statistics per player, club and competition (free plan: seasons 2022-2024). Raw counts as
-- the provider reports them; the role model (dugout_models.style) turns them into per-90 percentiles.
CREATE TABLE IF NOT EXISTS player_season_stats (
    api_player_id    INTEGER NOT NULL,
    season           INTEGER NOT NULL,           -- start year: 2024 = 2024-25
    league_id        INTEGER NOT NULL,           -- API-Football competition id
    team_id          INTEGER NOT NULL,
    player_name      VARCHAR,
    birth_date       DATE,
    team_name        VARCHAR,
    league_name      VARCHAR,
    league_country   VARCHAR,
    domestic         BOOLEAN,                    -- the club's league (not a cup or a European competition)
    position         VARCHAR,                    -- Goalkeeper | Defender | Midfielder | Attacker
    appearances      INTEGER,
    lineups          INTEGER,
    minutes          INTEGER,
    rating           DOUBLE,
    shots            INTEGER,
    shots_on         INTEGER,
    goals            INTEGER,
    assists          INTEGER,
    passes           INTEGER,
    key_passes       INTEGER,
    pass_accuracy    INTEGER,                    -- as reported; not a percentage in season totals (unused)
    tackles          INTEGER,
    blocks           INTEGER,
    interceptions    INTEGER,
    duels            INTEGER,
    duels_won        INTEGER,
    dribbles         INTEGER,
    dribbles_won     INTEGER,
    dribbled_past    INTEGER,
    fouls_drawn      INTEGER,
    fouls_committed  INTEGER,
    yellow           INTEGER,
    red              INTEGER,
    saves            INTEGER,
    conceded         INTEGER,
    reep_id          VARCHAR,
    tm_player_id     VARCHAR,
    match_how        VARCHAR,                    -- reep | birth_date | name_in_club
    source_url       VARCHAR,
    fetched_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (api_player_id, season, league_id, team_id)
);

CREATE TABLE IF NOT EXISTS injuries (
    player_id       VARCHAR NOT NULL,
    club_id         VARCHAR,
    source          VARCHAR NOT NULL,
    snapshot_date   DATE NOT NULL,
    status          VARCHAR,                  -- available | doubtful | injured | suspended | unavailable | loan
    reason          VARCHAR,
    chance_next     INTEGER,                  -- 0-100, FPL chance_of_playing_next_round
    news            VARCHAR,
    news_added      TIMESTAMPTZ,
    expected_return DATE,
    fetched_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (player_id, source, snapshot_date)
);

CREATE TABLE IF NOT EXISTS transfers (
    player_id    VARCHAR NOT NULL,
    date         DATE NOT NULL,
    from_club    VARCHAR,
    to_club      VARCHAR,
    fee_eur      DOUBLE,
    fee_text     VARCHAR,
    kind         VARCHAR,                     -- permanent | loan | free | end_of_loan
    source       VARCHAR NOT NULL,
    PRIMARY KEY (player_id, date, source)
);

-- Transfers since the transfermarkt-datasets snapshot, from Wikipedia's transfer lists (CC BY-SA). Names and
-- linked articles as listed; Transfermarkt ids via Wikidata. date is the completion date (table pages) or the cited
-- announcement date (by-club pages), NULL when the page gives neither.
CREATE TABLE IF NOT EXISTS wiki_transfers (
    transfer_id   VARCHAR PRIMARY KEY,
    page          VARCHAR NOT NULL,
    date          DATE,
    player_name   VARCHAR NOT NULL,
    player_title  VARCHAR,
    from_club     VARCHAR,
    from_title    VARCHAR,
    to_club       VARCHAR,
    to_title      VARCHAR,
    kind          VARCHAR NOT NULL,            -- permanent | loan | end_of_loan | free | released
    fee_text      VARCHAR,
    fee_native    DOUBLE,
    currency      VARCHAR,
    fee_eur       DOUBLE,                      -- ECB reference rate of the transfer date
    loan_end      DATE,
    tm_player_id  VARCHAR,
    tm_from_club  VARCHAR,
    tm_to_club    VARCHAR,
    fetched_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS fx_rates (
    date         DATE NOT NULL,
    currency     VARCHAR NOT NULL,
    per_eur      DOUBLE NOT NULL,             -- units of currency per euro (ECB reference rate)
    source       VARCHAR NOT NULL,
    PRIMARY KEY (date, currency, source)
);

CREATE TABLE IF NOT EXISTS market_values (
    player_id    VARCHAR NOT NULL,
    date         DATE NOT NULL,
    value_eur    DOUBLE NOT NULL,
    source       VARCHAR NOT NULL,
    PRIMARY KEY (player_id, date, source)
);

CREATE TABLE IF NOT EXISTS contracts (
    player_id    VARCHAR NOT NULL,
    club_id      VARCHAR,
    expires      DATE,
    source       VARCHAR NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (player_id, source)
);

CREATE TABLE IF NOT EXISTS news_items (
    news_id      VARCHAR PRIMARY KEY,         -- sha256(canonical url)[:16]
    source       VARCHAR NOT NULL,
    outlet       VARCHAR,
    url          VARCHAR NOT NULL,
    title        VARCHAR,
    published_at TIMESTAMPTZ,
    summary      VARCHAR,
    body_text    VARCHAR,                     -- extracted locally for NLI; never republished
    club_ids     VARCHAR[],
    source_tier  INTEGER NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL
);

-- The Guardian Rumour Mill back-catalogue (adapter rumour_archive): one row per paragraph that names a player.
-- body_text is kept locally to find the linked clubs; never republished.
CREATE TABLE IF NOT EXISTS rumours (
    rumour_id    VARCHAR PRIMARY KEY,         -- = news_items.news_id of the same paragraph
    published_at TIMESTAMPTZ NOT NULL,
    url          VARCHAR NOT NULL,            -- the Guardian column
    paragraph    INTEGER NOT NULL,
    players      VARCHAR[] NOT NULL,          -- as the column names them (bold)
    outlet       VARCHAR NOT NULL,            -- the outlet the column credits, else The Guardian
    body_text    VARCHAR NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL
);

-- Each rumour resolved to (player, linked club) pairs and labelled with what happened (`dugout-models rumours`):
-- Transfermarkt ids by normalised name; joined_<n>m = he joined that club within n months (NULL: not yet known).
CREATE TABLE IF NOT EXISTS rumour_links (
    link_id        VARCHAR PRIMARY KEY,       -- <rumour_id>:<tm_player_id>:<tm_club_id>
    rumour_id      VARCHAR NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL,
    outlet         VARCHAR NOT NULL,
    player_name    VARCHAR NOT NULL,          -- as the column names him
    tm_player_id   VARCHAR NOT NULL,
    club_name      VARCHAR NOT NULL,          -- the linked club, as matched in the text
    tm_club_id     VARCHAR NOT NULL,
    from_tm_club   VARCHAR,                   -- his club on the day (Transfermarkt transfer history)
    joined_3m      BOOLEAN,
    joined_6m      BOOLEAN,
    joined_12m     BOOLEAN,
    joined_on      DATE,                      -- first move to the linked club after the rumour, if any
    labelled_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS standings (
    league       VARCHAR NOT NULL,
    season       VARCHAR NOT NULL,
    club_id      VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    position     INTEGER,
    played       INTEGER,
    won          INTEGER,
    drawn        INTEGER,
    lost         INTEGER,
    goals_for    INTEGER,
    goals_against INTEGER,
    points       INTEGER,
    form         VARCHAR,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (league, season, club_id, source)
);

CREATE TABLE IF NOT EXISTS elo (
    club_id      VARCHAR NOT NULL,
    date         DATE NOT NULL,
    elo          DOUBLE NOT NULL,
    source       VARCHAR NOT NULL,
    PRIMARY KEY (club_id, date, source)
);

CREATE TABLE IF NOT EXISTS historical_results (
    league       VARCHAR NOT NULL,
    season       VARCHAR NOT NULL,
    date         DATE NOT NULL,
    home         VARCHAR NOT NULL,            -- club slug where mappable, else raw name
    away         VARCHAR NOT NULL,
    home_goals   INTEGER,
    away_goals   INTEGER,
    odds_home    DOUBLE,                      -- closing odds (Pinnacle/market avg where available)
    odds_draw    DOUBLE,
    odds_away    DOUBLE,
    odds_source  VARCHAR,
    source       VARCHAR NOT NULL,
    PRIMARY KEY (league, date, home, away)
);

CREATE TABLE IF NOT EXISTS weather (
    match_id     VARCHAR PRIMARY KEY,
    temp_c       DOUBLE,
    precip_mm    DOUBLE,
    wind_kmh     DOUBLE,
    source       VARCHAR NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS set_piece_notes (
    club_id      VARCHAR NOT NULL,
    note         VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (club_id, note, source)
);

CREATE TABLE IF NOT EXISTS provenance (
    sha256       VARCHAR NOT NULL,            -- of the response body
    source       VARCHAR NOT NULL,
    url          VARCHAR NOT NULL,            -- redacted
    status       INTEGER NOT NULL,
    bytes        INTEGER NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, url, fetched_at)
);

CREATE TABLE IF NOT EXISTS source_runs (
    run_id       VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    ok           BOOLEAN,
    rows_written INTEGER,
    message      VARCHAR,
    PRIMARY KEY (run_id, source)
);

-- Head-coach spells per club (Wikidata P286 with start/end qualifiers). Used to attribute team style to managers.
CREATE TABLE IF NOT EXISTS manager_tenures (
    manager_id   VARCHAR NOT NULL,            -- wikidata:<QID>
    name         VARCHAR NOT NULL,
    club_id      VARCHAR NOT NULL,
    start_date   DATE,
    end_date     DATE,                        -- NULL = current
    source       VARCHAR NOT NULL,
    PRIMARY KEY (manager_id, club_id, start_date)
);

-- Current outputs of dugout's own models (`dugout-models publish`). Agents cite these through the evidence ledger
-- with a `sql` query, so the Stat Auditor can re-read them. `context` disambiguates (match id, target club...).
CREATE TABLE IF NOT EXISTS model_outputs (
    model        VARCHAR NOT NULL,            -- predictor | lineup | injury | roles | fingerprint | valuation | realism | opportunity | scout
    version      VARCHAR NOT NULL,            -- model version + training cut-off
    entity_type  VARCHAR NOT NULL,            -- match | club | player | manager
    entity_id    VARCHAR NOT NULL,
    metric       VARCHAR NOT NULL,
    context      VARCHAR NOT NULL DEFAULT '',
    value_num    DOUBLE,
    value_text   VARCHAR,
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (model, entity_type, entity_id, metric, context)
);

-- Every forecast as published (append-only), so the scoreboard compares *live* predictions with results.
CREATE TABLE IF NOT EXISTS prediction_log (
    match_id     VARCHAR NOT NULL,
    as_of        TIMESTAMPTZ NOT NULL,       -- when the prediction was published (before kick-off)
    kickoff_utc  TIMESTAMPTZ,
    model        VARCHAR NOT NULL,
    p_home       DOUBLE NOT NULL,
    p_draw       DOUBLE NOT NULL,
    p_away       DOUBLE NOT NULL,
    PRIMARY KEY (match_id, as_of, model)
);
