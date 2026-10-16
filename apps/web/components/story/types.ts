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
  /** the scout's three lists for the top need: dream, realistic, hidden gem (up to three names each) */
  targets: Target[];
  /** transfer talk the Fact-Check confirmed in the Guardian's Rumour Mill (attributed, unverified as fact) */
  /** with how often reports like it came true (one time in N), when the item links a player with this club */
  rumours: { text: string; url: string | null; record?: StatRef }[];
  verifiedSamples: StatRef[];
  numbersTotal: number;
  untraceable: number;
  generatedAt: string;
  prediction?: { home: string; away: string; kickoff?: StatRef; pHome?: StatRef; pDraw?: StatRef; pAway?: StatRef };
  scene: { scatter: XY[]; home: XY[]; away: XY[]; candidateSlot: number; gap: XY };
}

/** One name on one of the scout's lists, with the verified numbers behind "why him" (absent ones are omitted). */
export interface Target {
  kind: "best" | "realistic" | "gem";
  name: string;
  club?: string;
  age?: StatRef;
  /** fair fee if this club bought him today */
  price?: StatRef;
  contract?: StatRef;
  marketValue?: StatRef;
  /** expected share of the buying club's minutes */
  minutesThere?: StatRef;
  /** share of his current club's minutes over the last year */
  minutesNow?: StatRef;
  /** chance his club sells him in a window */
  pSold?: StatRef;
  /** chance this move happens in a window */
  chance?: StatRef;
  projected?: StatRef;
}
