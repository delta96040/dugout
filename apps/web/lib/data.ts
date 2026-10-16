// Build-time data access. The site reads only the verified JSON the pipeline commits under ../../data and the
// backtest reports under ../../docs/backtests. Nothing is fetched at runtime except the public GitHub star count.
import fs from "node:fs";
import path from "node:path";

export const REPO_ROOT = path.resolve(process.cwd(), "..", "..");
export const REPO_URL = "https://github.com/delta96040/dugout";

export type Segment = { text: string } | { stat: string; display: string };

export interface Source {
  outlet: string;
  tier: number;
  url: string | null;
  official: boolean;
}

export interface Claim {
  segments: Segment[];
  type: string;
  status: "verified" | "analysis";
  reason: string;
  sources: Source[];
}

export interface Section {
  agent: string;
  title: string;
  question: string;
  status: "published" | "withheld" | "empty";
  writer: string;
  claims: Claim[];
  withheld: { text: string; status: string; reason: string }[];
}

export interface CrossCheck {
  source: string;
  value: number | string;
  agrees: boolean;
}

export interface Stat {
  value: number | string | null;
  display: string;
  unit: string | null;
  metric: string;
  entity_type: string;
  entity_id: string;
  entity_name: string;
  source: string;
  source_url: string | null;
  source_tier: number;
  fetched_at: string;
  query_kind: string;
  status: "verified" | "single_source" | "model_output" | "disputed" | string;
  cross_checks: CrossCheck[];
}

export interface League {
  key: string;
  name: string;
  country: string;
}

export interface Dossier {
  schema: string;
  club: { slug: string; name: string; season: string };
  /** the club's league (dossiers written before leagues existed are Premier League) */
  league?: League;
  generated_at: string;
  data_as_of: string;
  llm: string | null;
  summary: Section;
  sections: Section[];
  stats: Record<string, Stat>;
  verification: {
    stat_auditor: { ok: boolean; numbers_total: number; numbers_untraceable: number };
    fact_check_claims: Record<string, number>;
    sections_published: number;
    sections_withheld: number;
  };
  methods: Record<string, string>;
  /** structured facts behind each section, each re-run by the Stat Auditor (for charts and the story) */
  key_stats?: Record<string, { stat: string; display: string; label: string; subject: string }[]>;
  /** names with digits in them ("Parma Calcio 1913"): proper names, not statistics (the Stat Auditor allows them) */
  names?: string[];
}

const CLUBS_DIR = path.join(REPO_ROOT, "data", "clubs");

export function clubSlugs(): string[] {
  if (!fs.existsSync(CLUBS_DIR)) return [];
  return fs
    .readdirSync(CLUBS_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.replace(/\.json$/, ""))
    .sort();
}

const cache = new Map<string, Dossier>(); // every story page lists every club: read each file once per build

export function dossier(slug: string): Dossier {
  let d = cache.get(slug);
  if (!d) {
    d = JSON.parse(fs.readFileSync(path.join(CLUBS_DIR, `${slug}.json`), "utf-8")) as Dossier;
    cache.set(slug, d);
  }
  return d;
}

const PREMIER_LEAGUE: League = { key: "premier_league", name: "Premier League", country: "England" };

export function leagueOf(d: Dossier): League {
  return d.league ?? PREMIER_LEAGUE;
}

/** Leagues with at least one verified dossier, in config order (data/leagues.json), each with its clubs. */
export function leagues(): (League & { clubs: Dossier[] })[] {
  const order = readData<League[]>("leagues.json") ?? [PREMIER_LEAGUE];
  const byKey = new Map<string, Dossier[]>();
  for (const d of allDossiers()) {
    const k = leagueOf(d).key;
    byKey.set(k, [...(byKey.get(k) ?? []), d]);
  }
  const known = order.filter((l) => byKey.has(l.key));
  const extra = [...byKey.keys()].filter((k) => !known.some((l) => l.key === k));
  return [...known, ...extra.map((k) => leagueOf(byKey.get(k)![0]))].map((l) => ({ ...l, clubs: byKey.get(l.key)! }));
}

/** URL segment for a league: premier_league -> premier-league. */
export function leagueSlug(l: League): string {
  return l.key.replace(/_/g, "-");
}

export function allDossiers(): Dossier[] {
  return clubSlugs().map(dossier);
}

export function readBacktest<T = unknown>(name: string): T | null {
  const p = path.join(REPO_ROOT, "docs", "backtests", `${name}.json`);
  return fs.existsSync(p) ? (JSON.parse(fs.readFileSync(p, "utf-8")) as T) : null;
}

export function readData<T = unknown>(rel: string): T | null {
  const p = path.join(REPO_ROOT, "data", rel);
  return fs.existsSync(p) ? (JSON.parse(fs.readFileSync(p, "utf-8")) as T) : null;
}

// ---- helpers over a dossier's cited stats -----------------------------------------------------------------
export function statsBy(d: Dossier, metric: string): [string, Stat][] {
  return Object.entries(d.stats).filter(([, s]) => s.metric === metric);
}

export function firstStat(d: Dossier, metric: string, entityHint?: string): [string, Stat] | undefined {
  const all = statsBy(d, metric);
  if (!entityHint) return all[0];
  return all.find(([, s]) => s.entity_id.includes(entityHint) || s.entity_name.includes(entityHint)) ?? all[0];
}

export function section(d: Dossier, agent: string): Section | undefined {
  return d.sections.find((s) => s.agent === agent);
}

/** The display string each cited stat was published with (the first occurrence in the prose). */
export function displays(d: Dossier): Record<string, string> {
  const out: Record<string, string> = {};
  for (const rows of Object.values(d.key_stats ?? {})) for (const r of rows) out[r.stat] ??= r.display;
  for (const s of [d.summary, ...d.sections]) {
    for (const c of s.claims) {
      for (const seg of c.segments) {
        if ("stat" in seg && !(seg.stat in out)) out[seg.stat] = seg.display;
      }
    }
  }
  return out;
}

export function claimText(c: Claim): string {
  return c.segments.map((s) => ("text" in s ? s.text : s.display)).join("");
}
