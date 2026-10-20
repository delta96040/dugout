// The scout's three lists for the top squad need, built from the dossier's audited key stats (agents/tools.py
// shortlist: labels name what each number measures).
import type { StatRef, Style, Target } from "@/components/story/types";
import { displays, readBacktest, type Dossier } from "@/lib/data";

export const KINDS: Target["kind"][] = ["best", "realistic", "gem"];

const text = (s?: StatRef) => (s ? String(s.stat.value) : undefined);

export function shortlists(d: Dossier): Target[] {
  const disp = displays(d);
  const ref = (id: string): StatRef => ({ id, stat: d.stats[id], display: disp[id] ?? d.stats[id].display });
  const rows = (d.key_stats?.scout ?? []).filter((r) => d.stats[r.stat]);
  const out: Target[] = [];
  for (const need of [0, 1] as const) {
   for (const kind of KINDS) {
    const suffix = need === 0 ? "" : ", second need";
    for (const age of rows.filter((r) => r.label === `age (${kind} target${suffix})`)) {
      const fact = (label: string) => {
        const r = rows.find((x) => x.subject === age.subject && x.label.startsWith(label));
        return r ? ref(r.stat) : undefined;
      };
      const club = fact("current club");
      const exact = (label: string) => {
        const r = rows.find((x) => x.subject === age.subject && x.label === label);
        return r ? ref(r.stat) : undefined;
      };
      // agents/tools.py _candidate_style: "top share for <feature>" (strongest first), and the feature he adds most
      const mine = rows.filter((x) => x.subject === age.subject);
      const adds = mine.find((x) => x.label.startsWith("his percentile for ") && x.label.endsWith(", where he adds most"));
      const addsFeature = adds?.label.slice("his percentile for ".length, -", where he adds most".length);
      out.push({
        kind,
        need,
        name: age.subject,
        club: club ? String(club.stat.value) : undefined,
        age: ref(age.stat),
        price: fact("estimated fair fee"),
        // agents/dossier.py RANGE_LOW / RANGE_HIGH
        priceLow: fact("likely low end of the estimated fair fee"),
        priceHigh: fact("likely high end of the estimated fair fee"),
        contract: fact("months left on his contract"),
        marketValue: fact("market value"),
        minutesThere: fact("expected share of minutes at the club"),
        minutesNow: fact("share of his club's minutes"),
        minutesLastYear: fact("minutes played in the past year"),
        leagueMinutes: fact("league minutes last season"),
        goals: fact("league goals per full match"),
        assists: fact("league assists per full match"),
        gaRank: fact("share of players at his position in his league"),
        gaLeague: text(fact("league of that comparison")),
        foot: text(fact("stronger foot")),
        height: fact("height in centimetres"),
        caps: fact("international caps"),
        positions: text(fact("positions he starts at")),
        pSold: fact("chance his club sells him"),
        chance: fact("chance the move happens"),
        pressReports: fact("Rumour Mill reports linking him"),
        pressLift: fact("how many times likelier those reports"),
        projected: fact("projected market value"),
        injury: fact("injury-risk tier"),
        style: style(
          exact("style season"),
          text(exact("style comparison")),
          text(exact("role")),
          mine.filter((x) => x.label.startsWith("top share for ")).map((x) => ({
            feature: x.label.slice("top share for ".length),
            top: ref(x.stat),
          })),
        ),
        adds:
          adds && addsFeature
            ? { feature: addsFeature, mine: ref(adds.stat), theirs: exact(`the starter's percentile for ${addsFeature}`) }
            : undefined,
        similar: text(exact("most similar players")),
      });
    }
   }
  }
  return out;
}

function style(season?: StatRef, pool?: string, role?: string, tops: Style["tops"] = []): Style | undefined {
  return season && pool && (role || tops.length) ? { season, pool, role, tops } : undefined;
}

/** Leagues whose players get an injury tier, and leagues with too little injury data (docs/backtests/injury.md). */
export interface InjuryCoverage {
  scored: string[];
  unscored: string[];
}

export function injuryCoverage(): InjuryCoverage {
  const j = readBacktest<{ scored_leagues?: string[]; unscored_leagues?: string[] }>("injury");
  return { scored: j?.scored_leagues ?? [], unscored: j?.unscored_leagues ?? [] };
}

/** The club's two biggest squad needs, each with the numbers behind it (agents/tools.py shortlist, "(need one)"). */
export interface Gap {
  need: StatRef;
  depth?: StatRef;
  wanted?: StatRef;
  bestPlayer?: string;
  bestValue?: StatRef;
  peerValue?: StatRef;
  veterans?: StatRef;
  /** the last regular starter there: who a signing has to beat to start */
  barPlayer?: string;
  barValue?: StatRef;
  /** the last regular starter's league goals and assists per full match */
  barGa?: StatRef;
  /** how the last regular starter plays, and his weakest features (what the side lacks there) */
  barStyle?: Style;
  barLows: { feature: string; pct: StatRef }[];
}

export function gaps(d: Dossier): Gap[] {
  const disp = displays(d);
  const ref = (id: string): StatRef => ({ id, stat: d.stats[id], display: disp[id] ?? d.stats[id].display });
  const rows = (d.key_stats?.scout ?? []).filter((r) => d.stats[r.stat]);
  const out: Gap[] = [];
  for (const [word, tag] of [
    ["one", "need one"],
    ["two", "need two"],
  ]) {
    const need = rows.find((r) => r.label === `squad need, priority ${word}`);
    if (!need) continue;
    const fact = (label: string) => {
      const r = rows.find((x) => x.label === `${label} (${tag})`);
      return r ? ref(r.stat) : undefined;
    };
    const best = fact("best option there");
    const tagged = (prefix: string) =>
      rows
        .filter((x) => x.label.startsWith(prefix) && x.label.endsWith(` (${tag})`))
        .map((x) => ({ feature: x.label.slice(prefix.length, -` (${tag})`.length), stat: ref(x.stat) }));
    const bar = fact("last regular starter there");
    out.push({
      need: ref(need.stat),
      depth: fact("players for the position, veterans aside"),
      wanted: fact("players a squad carries there"),
      bestPlayer: best ? String(best.stat.value) : undefined,
      bestValue: fact("best option's market value"),
      peerValue: fact("similar squads' best option's market value"),
      veterans: fact("share of the options who are veterans"),
      barPlayer: bar ? String(bar.stat.value) : undefined,
      barValue: fact("last regular starter's market value"),
      barGa: fact("last regular starter's goals and assists per full match"),
      barStyle: style(
        fact("last regular starter's style season"),
        text(fact("last regular starter's style comparison")),
        text(fact("last regular starter's role")),
        tagged("last regular starter's top share for ").map((x) => ({ feature: x.feature, top: x.stat })),
      ),
      barLows: tagged("last regular starter's percentile for ").map((x) => ({ feature: x.feature, pct: x.stat })),
    });
  }
  return out;
}
