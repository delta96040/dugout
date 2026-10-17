import type { Stat } from "@/lib/data";
import type { XY } from "@/lib/formations";
import type { Gap } from "@/lib/shortlist";

export interface StatRef {
  id: string;
  stat: Stat;
  display: string;
  /** what the number measures, in words (the dossier's key-stat label) */
  label?: string;
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
  /** the two biggest needs with their reasons */
  gaps: Gap[];
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
  /** which squad need the list is for: 0 = the biggest, 1 = the second */
  need: 0 | 1;
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
  /** minutes in the past year's matches on record */
  minutesLastYear?: StatRef;
  /** chance his club sells him in a window */
  pSold?: StatRef;
  /** chance this move happens in a window */
  chance?: StatRef;
  projected?: StatRef;
}
