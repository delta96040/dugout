// Formatting shared by server and client components.
const SOURCE_NAMES: Record<string, string> = {
  fpl: "Fantasy Premier League",
  football_data_org: "football-data.org",
  transfermarkt: "Transfermarkt",
  transfermarkt_datasets: "Transfermarkt (transfermarkt-datasets, CC0)",
  wikidata: "Wikidata",
  api_football: "API-Football",
};

// dugout's own estimates, by what they are to a reader
const DUGOUT: Record<string, string> = {
  predictor: "dugout match forecast",
  lineup: "dugout predicted XI",
  injury: "dugout injury-risk estimate",
  roles: "dugout player-role analysis",
  fingerprint: "dugout style analysis",
  valuation: "dugout valuation",
  realism: "dugout transfer odds",
  scout: "dugout scouting",
  potential: "dugout development projection",
  fit: "dugout fit analysis",
  lookup: "dugout player profile",
  market: "dugout valuation",
};

export function sourceName(source: string): string {
  if (source.startsWith("dugout_model:")) {
    const [name, ...rest] = source.slice("dugout_model:".length).split(" ");
    const note = rest.join(" ").replace("walk-forward backtest", "replay of past matches");
    return `${DUGOUT[name] ?? "dugout analysis"}${note ? ` ${note}` : ""}`;
  }
  return SOURCE_NAMES[source] ?? source;
}

/** Why something was removed in checking, in plain words. */
export function plainReason(status: string, reason: string): string {
  if (status === "audit_error" || reason.includes("number")) return "it contained a figure we could not trace";
  if (reason.includes("does not support")) return "the source does not say this";
  if (reason.includes("contradicted")) return "a more reliable source says otherwise";
  if (reason.includes("need 2") || reason.includes("independent source")) return "only one source so far";
  if (reason.includes("no text from")) return "we could not find the source text";
  if (reason.includes("placed next to another name")) return "the figure could belong to someone else";
  if (reason.includes("stale") || reason.includes("old")) return "the news is too old";
  return "it could not be confirmed";
}

export function when(iso: string): string {
  const d = new Date(iso);
  return (
    d.toLocaleString("en-GB", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    }) + " UTC"
  );
}

export function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
}

/** A metric code in words: "league_goalsFor" -> "league goals for". */
const METRIC_NAMES: Record<string, string> = {
  need: "squad need",
  need_depth: "players for the position, veterans aside",
  need_wanted: "players a squad carries there",
  need_best_player: "best option there",
  need_best_value: "best option's market value",
  need_peer_best_value: "similar squads' best option's value",
  need_ageing_share: "share of the options who are veterans",
  need_bar_player: "last regular starter there",
  need_bar_value: "last regular starter's market value",
  minutes_last_year: "minutes in the past year's matches on record",
  minutes_share_now: "share of his club's minutes",
  p_sold: "chance his club sells him in a window",
  realism: "chance the move happens in a window",
  proj_value_4y: "projected market value in a few seasons",
  contract_months: "months left on his contract",
};

export function metricName(metric: string): string {
  if (METRIC_NAMES[metric]) return METRIC_NAMES[metric];
  return metric
    .replaceAll("_", " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();
}
