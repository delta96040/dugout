// The rumour brief: what a sporting director needs next to a rumour, built from a verified dossier. Each rumour
// the Fact-Check confirmed in the Guardian's Rumour Mill comes with its key stats (agents tools.rumour_brief,
// labels "rumour ..."): the source's credibility and record, how many independent outlets carry the link and since
// when, our own estimate of the move, his contract, how often his club sells, and what became of the source's
// earlier reports about this club. Absent figures are left out; nothing is shown that the dossier does not verify.
import type { StatRef } from "@/components/story/types";
import { claimText, displays, section, type Dossier } from "@/lib/data";

export interface EarlierReport {
  outcome: StatRef;
  date?: StatRef;
  player?: StatRef;
  club?: StatRef;
}

export interface RumourBrief {
  /** the verified sentence, without "According to The Guardian, " */
  text: string;
  url: string | null;
  player: string;
  /** the buying club (the dossier's club) */
  buyer: string;
  slug: string;
  credibility?: StatRef;
  record?: StatRef;
  traced?: StatRef;
  cameTrue?: StatRef;
  similar?: StatRef;
  outlets?: StatRef;
  outletNames?: StatRef;
  reports?: StatRef;
  since?: StatRef;
  estimate?: StatRef;
  sale?: StatRef;
  thisBuyer?: StatRef;
  contract?: StatRef;
  seller?: StatRef;
  sellerSales?: StatRef;
  sellerSince?: StatRef;
  earlier: EarlierReport[];
}

// how a source's label weighs in the order (dugout_models.rumours.CREDIBILITY_WEIGHT; keep in step)
const WEIGHT: Record<string, number> = { reliable: 1.5, "about average": 1, unproven: 0.8, speculative: 0.5 };

/** Credibility x heat (independent outlets), ties broken by reports: dugout_models.rumours.priority. */
export function briefScore(b: RumourBrief): number {
  const w = WEIGHT[String(b.credibility?.stat.value ?? "")] ?? 1;
  const outlets = Math.max(Number(b.outlets?.stat.value ?? 1) || 1, 1);
  const reports = Math.min(Number(b.reports?.stat.value ?? 0) || 0, 99);
  return w * outlets + 0.01 * reports;
}

export function rumourBriefs(d: Dossier): RumourBrief[] {
  const disp = displays(d);
  const scout = section(d, "scout");
  if (!scout || scout.status !== "published") return [];
  const rows = (d.key_stats?.scout ?? []).filter((k) => k.label.startsWith("rumour ") && d.stats[k.stat]);
  const ref = (id: string, label: string): StatRef => ({ id, stat: d.stats[id], display: disp[id] ?? d.stats[id].display, label });
  const briefs = scout.claims
    .filter((c) => c.type === "attributed" && !c.sources.some((s) => s.official))
    .map((c): RumourBrief | null => {
      const text = claimText(c).replace(/^According to [^,]+, /, "");
      const linked = / have been linked with (.+)\.$/.exec(text);
      if (!linked) return null; // outgoing talk ("... linked with a move away") has no brief
      const player = linked[1];
      const mine = rows.filter((k) => k.subject === player);
      // a label can carry two rows (a value and its date): pick by metric
      const get = (label: string, metric?: (m: string) => boolean) => {
        const k = mine.find((r) => r.label === `rumour ${label}` && (!metric || metric(d.stats[r.stat].metric)));
        return k ? ref(k.stat, k.label) : undefined;
      };
      const earlier: EarlierReport[] = [];
      for (const n of [1, 2]) {
        const outcome = get(`earlier report ${n}`, (m) => m.endsWith("_outcome"));
        if (!outcome) continue;
        earlier.push({
          outcome,
          date: get(`earlier report ${n}`, (m) => m.endsWith("_date")),
          player: get(`earlier report ${n} player`),
          club: get(`earlier report ${n} club`),
        });
      }
      return {
        text,
        url: c.sources[0]?.url ?? null,
        player,
        buyer: d.club.name,
        slug: d.club.slug,
        credibility: get("source credibility"),
        record: get("source record"),
        traced: get("source reports traced"),
        cameTrue: get("source reports came true"),
        similar: get("similar reports record"),
        outlets: get("independent outlets"),
        outletNames: get("outlets reporting"),
        reports: get("reports"),
        since: get("first reported"),
        estimate: get("move estimate"),
        sale: get("sale likelihood"),
        thisBuyer: get("this buyer"),
        contract: get("contract months"),
        seller: get("seller"),
        sellerSales: get("seller fee sales", (m) => m === "fee_sales"),
        sellerSince: get("seller fee sales", (m) => m === "fee_sales_since"),
        earlier,
      };
    })
    .filter((b): b is RumourBrief => b !== null);
  // stable: the dossier already lists them credibility x heat first; dossiers from before the brief keep their order
  return briefs.sort((a, b) => briefScore(b) - briefScore(a));
}

/** Claims the rumour cards show, so the prose does not repeat them (the old "one time in N" line included). */
export function isRumourClaim(text: string): boolean {
  return /^According to The Guardian, .+ have been linked with /.test(text) || /^Past reports like this one about /.test(text);
}
