// The scroll story's data for one club (/story/<club>/), built from its verified dossier.
import type { StatRef, StoryData } from "@/components/story/types";
import { claimText, displays, section, type Dossier } from "@/lib/data";
import { shortlists } from "@/lib/shortlist";
import { FORMATION_SLOTS, GAP_ZONES, opposition, scatter, slotPositions } from "@/lib/formations";


// which slot a position-group gap occupies, by formation
const GAP_SLOTS: Record<string, string[]> = {
  goalkeeper: ["GK"],
  "centre-back": ["CB"],
  "full-back": ["RB", "RWB"],
  "defensive midfield": ["DM", "CM"],
  "central midfield": ["CM", "AM"],
  winger: ["RW", "RM", "AM"],
  "centre-forward": ["CF"],
};

export function storyData(d: Dossier): StoryData {
  const disp = displays(d);
  const ref = (id: string): StatRef => ({ id, stat: d.stats[id], display: disp[id] ?? d.stats[id].display });
  // key_stats keep each section's facts in priority order (need one before need two, the top candidate first)
  const ordered = [...Object.values(d.key_stats ?? {}).flat().map((k) => k.stat), ...Object.keys(d.stats)];
  const find = (metric: string, pred: (e: string) => boolean = () => true) => {
    const id = ordered.find((i) => d.stats[i] && d.stats[i].metric === metric && pred(d.stats[i].entity_id));
    return id ? ref(id) : undefined;
  };
  const formation = find("predicted_formation");
  const slots = FORMATION_SLOTS[String(formation?.stat.value ?? "4-3-3")] ?? FORMATION_SLOTS["4-3-3"];
  const need = find("need");
  const needGroup = need ? String(need.stat.value) : undefined;
  const candidateSlot = needGroup ? slots.findIndex((s) => (GAP_SLOTS[needGroup] ?? []).includes(s)) : -1;
  const home = slotPositions(slots);

  const scout = section(d, "scout");
  const targets = shortlists(d);
  const attributed = (scout?.claims ?? []).filter((c) => c.type === "attributed");

  const match = find("p_home_win") ?? find("p_away_win");
  const [homeName, awayName] = (match?.stat.entity_name ?? " v ").split(" v ");
  const matchId = match?.stat.entity_id;
  // six numbers with different metrics, cross-checked ones first, then re-checked single-source and model outputs
  const rank: Record<string, number> = { verified: 0, single_source: 1, model_output: 2 };
  const labels: Record<string, string> = {};
  for (const r of Object.values(d.key_stats ?? {}).flat()) labels[r.stat] ??= r.label;
  const verifiedSamples = ordered
    .filter((id, i) => d.stats[id] && ordered.indexOf(id) === i && d.stats[id].status in rank)
    .sort((a, b) => rank[d.stats[a].status] - rank[d.stats[b].status])
    .filter((id, i, all) => all.findIndex((j) => d.stats[j].metric === d.stats[id].metric) === i)
    .slice(0, 6)
    .map((id) => ({ ...ref(id), label: labels[id] }));

  return {
    club: d.club.name,
    slug: d.club.slug,
    manager: find("appointed")?.stat.entity_name ?? find("pl_matches")?.stat.entity_name,
    position: find("league_position", (e) => e === d.club.slug),
    formation,
    threat: find("threat_share"),
    defActions: find("def_actions"),
    need,
    needGroup,
    // announced signings come from the club's own site (official); press talk is a rumour, listed separately
    signings: attributed
      .filter((c) => c.sources.some((s) => s.official))
      .map((c) => ({ title: claimText(c).replace(/^According to [^,]+, /, ""), url: c.sources[0]?.url ?? null })),
    rumours: attributed
      .filter((c) => !c.sources.some((s) => s.official))
      .map((c) => ({ text: claimText(c).replace(/^According to [^,]+, /, ""), url: c.sources[0]?.url ?? null })),
    targets,
    verifiedSamples,
    numbersTotal: d.verification.stat_auditor.numbers_total,
    untraceable: d.verification.stat_auditor.numbers_untraceable,
    generatedAt: d.generated_at,
    prediction: match
      ? {
          home: homeName,
          away: awayName,
          kickoff: find("kickoff_utc", (e) => e === matchId),
          pHome: find("p_home_win", (e) => e === matchId),
          pDraw: find("p_draw", (e) => e === matchId),
          pAway: find("p_away_win", (e) => e === matchId),
        }
      : undefined,
    scene: {
      scatter: scatter(22),
      home,
      away: opposition(),
      candidateSlot,
      gap: GAP_ZONES[needGroup ?? ""] ?? [0, 0],
    },
  };
}
