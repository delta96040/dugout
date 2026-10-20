import type { Stat } from "@/lib/data";
import type { XY } from "@/lib/formations";
import type { RumourBrief } from "@/lib/rumourBrief";
import type { Gap, InjuryCoverage } from "@/lib/shortlist";

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
  /** which leagues' players have an injury tier (docs/backtests/injury.json) */
  injury: InjuryCoverage;
  signings: { title: string; url: string | null }[];
  /** the scout's three lists for the top need: dream, realistic, hidden gem (up to three names each) */
  targets: Target[];
  /** transfer talk the Fact-Check confirmed in the Guardian's Rumour Mill (attributed, unverified as fact), each
   *  with its brief (source credibility, heat, our estimate, track record), most credible and most reported first */
  rumours: RumourBrief[];
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
  /** the price's likely range (four in five past fees landed inside: docs/backtests/valuation.md) */
  priceLow?: StatRef;
  priceHigh?: StatRef;
  contract?: StatRef;
  marketValue?: StatRef;
  /** expected share of the buying club's minutes */
  minutesThere?: StatRef;
  /** share of his current club's minutes over the last year */
  minutesNow?: StatRef;
  /** minutes in the past year's matches on record */
  minutesLastYear?: StatRef;
  /** last season's league minutes as Transfermarkt lists them (candidate pools such as the Championship) */
  leagueMinutes?: StatRef;
  /** league goals and assists per full match, and his rank for both at his position in his league */
  goals?: StatRef;
  assists?: StatRef;
  gaRank?: StatRef;
  gaLeague?: string;
  foot?: string;
  height?: StatRef;
  caps?: StatRef;
  /** positions he starts at (Transfermarkt line-ups) */
  positions?: string;
  /** chance his club sells him in a window */
  pSold?: StatRef;
  /** Rumour Mill reports linking him with this club in the past year, and how many times likelier they make
   *  the move (shown only when they moved the odds) */
  pressReports?: StatRef;
  pressLift?: StatRef;
  /** chance this move happens in a window */
  chance?: StatRef;
  projected?: StatRef;
  /** injury-risk tier (low / moderate / elevated / high), where his league is scored */
  injury?: StatRef;
  /** how he plays (docs/backtests/roles_api.md): the season and the players he is measured against */
  style?: Style;
  /** the feature he adds most over the starter he would replace: his percentile and the starter's */
  adds?: { feature: string; mine: StatRef; theirs?: StatRef };
  /** players closest to him in style */
  similar?: string;
}

/** A player's style: role (when one clearly leads), his strongest features as top shares ("top 10%"), against
 *  a named pool ("full-backs in Europe's biggest leagues") in one season. */
export interface Style {
  season: StatRef;
  pool: string;
  role?: string;
  tops: { feature: string; top: StatRef }[];
}
