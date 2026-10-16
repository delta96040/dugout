import type { Stat } from "@/lib/data";
import type { XY } from "@/lib/formations";

export interface StatRef {
  id: string;
  stat: Stat;
  display: string;
}

export interface StoryData {
  club: string;
  slug: string;
  manager?: string;
  position?: StatRef;
  formation?: StatRef;
  threat?: StatRef;
  defActions?: StatRef;
  need?: StatRef;
  needGroup?: string;
  signings: { title: string; url: string | null }[];
  /** the scout's three lists for the top need: best fit, realistic, hidden gem (first name of each) */
  targets: Target[];
  /** transfer talk the Fact-Check confirmed in the Guardian's Rumour Mill (attributed, unverified as fact) */
  rumours: { text: string; url: string | null }[];
  verifiedSamples: StatRef[];
  numbersTotal: number;
  untraceable: number;
  generatedAt: string;
  prediction?: { home: string; away: string; kickoff?: StatRef; pHome?: StatRef; pDraw?: StatRef; pAway?: StatRef };
  scene: { scatter: XY[]; home: XY[]; away: XY[]; candidateSlot: number; gap: XY };
}

export interface Target {
  kind: "best" | "realistic" | "gem";
  name: string;
  club?: string;
  age?: StatRef;
  /** the number the list is about: market value, chance of the move, projected value */
  figure?: StatRef;
  figureLabel: string;
}
