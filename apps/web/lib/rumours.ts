// The rumour track record page's data (data/rumours.json, written by `dugout-models publish rumours`) and the
// provenance of its numbers. No file access, so client components can use it too.
import type { Stat } from "./data";

const REPO_URL = "https://github.com/delta96040/dugout";
export const RUMOURS_DOC = `${REPO_URL}/blob/main/docs/backtests/rumours.md`;

export interface Season {
  season: string;
  links: number;
  came_true: number;
  rate: number;
}

export interface Outlet {
  outlet: string;
  links: number;
  came_true: number;
  raw: number;
  shrunk: number;
  /** reliable / about average / speculative / unproven: its record against what similar reports did */
  label?: string;
  /** share of similar reports (the rumour model, which never sees the outlet) that came true */
  expected_rate?: number;
  ratio?: number;
}

export interface RumourRecord {
  generated_at: string;
  horizon_months: number;
  first_season: string;
  modern_season: string;
  overall: { links: number; came_true: number; rate: number; links_modern: number; rate_modern: number | null };
  seasons: Season[];
  outlets: Outlet[];
  prior_rate: number;
  backtest: {
    generated_at: string | null;
    test_season: string | null;
    test_links: number | null;
    brier: number | null;
    brier_recent_base: number | null;
    auc: number | null;
    mean_forecast: number | null;
    test_rate: number | null;
  };
}

export const pct = (p: number) => `${Math.round(p * 100)}%`;
export const int = (n: number) => n.toLocaleString("en-GB");

/** A number on the page as a ledger-style stat: what it counts, from the archive's labelled rumours, and when. */
export function recordStat(
  value: number,
  display: string,
  metric: string,
  subject: string,
  fetchedAt: string,
  kind: "record" | "backtest" = "record",
): Stat {
  return {
    value,
    display,
    unit: null,
    metric,
    entity_type: kind === "record" ? "rumours" : "backtest",
    entity_id: subject,
    entity_name: subject,
    source:
      kind === "record"
        ? "dugout rumour track record: the Guardian Rumour Mill archive against Transfermarkt and Wikipedia transfers"
        : "dugout backtest (docs/backtests/rumours.json)",
    source_url: RUMOURS_DOC,
    source_tier: 3,
    fetched_at: fetchedAt,
    query_kind: kind,
    status: "model_output",
    cross_checks: [],
  };
}
