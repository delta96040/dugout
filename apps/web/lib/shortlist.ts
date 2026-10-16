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
        pSold: fact("chance his club sells him"),
        chance: fact("chance the move happens"),
        projected: fact("projected market value"),
      });
    }
  }
  return out;
}
