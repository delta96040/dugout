// The scout's three lists for the top squad need, built from the dossier's audited key stats (agents/tools.py
// shortlist: labels name what each number measures).
import type { StatRef, Target } from "@/components/story/types";
import { displays, type Dossier } from "@/lib/data";

export const KINDS: Target["kind"][] = ["best", "realistic", "gem"];

export function shortlists(d: Dossier): Target[] {
  const disp = displays(d);
  const ref = (id: string): StatRef => ({ id, stat: d.stats[id], display: disp[id] ?? d.stats[id].display });
  const rows = (d.key_stats?.scout ?? []).filter((r) => d.stats[r.stat]);
  const out: Target[] = [];
  for (const kind of KINDS) {
    for (const age of rows.filter((r) => r.label === `age (${kind} target)`)) {
      const fact = (label: string) => {
        const r = rows.find((x) => x.subject === age.subject && x.label.startsWith(label));
        return r ? ref(r.stat) : undefined;
      };
      const club = fact("current club");
      out.push({
        kind,
        name: age.subject,
        club: club ? String(club.stat.value) : undefined,
        age: ref(age.stat),
        price: fact("estimated fair fee"),
        contract: fact("months left on his contract"),
        marketValue: fact("market value"),
        minutesThere: fact("expected share of minutes at the club"),
        minutesNow: fact("share of his club's minutes"),
        minutesLastYear: fact("minutes played in the past year"),
        pSold: fact("chance his club sells him"),
        chance: fact("chance the move happens"),
        projected: fact("projected market value"),
      });
    }
  }
  return out;
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
    });
  }
  return out;
}
