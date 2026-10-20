import type { ReactNode } from "react";
import type { StatRef, Style, Target } from "@/components/story/types";
import { type Gap, type InjuryCoverage, KINDS } from "@/lib/shortlist";

export const LIST_LABEL: Record<Target["kind"], string> = {
  best: "Dream targets · whatever the cost",
  realistic: "Realistic targets · gettable",
  gem: "Hidden gems · young, rising",
};

const ATTACKING = new Set(["centre-forward", "winger", "central midfield"]);

/** What he produces, in the numbers that matter for the position (free data has goals, assists and minutes for
 *  every league; no passing or defending figures). */
function Output({ t, need, seller, n }: { t: Target; need?: string; seller: ReactNode; n: (s: StatRef) => ReactNode }) {
  const league = t.gaLeague ? <span data-name>{t.gaLeague}</span> : "his league";
  const r = t.gaRank ? Number(t.gaRank.stat.value) : NaN;
  // a ranking is a selling point only above the middle; below it, say so plainly
  const rank =
    t.gaRank &&
    (r >= 0.5 ? (
      <>
        , more goal involvement than {n(t.gaRank)} of players at his position in {league}
      </>
    ) : (
      <>
        ; for goals and assists he is behind most players at his position in {league} (ahead of {n(t.gaRank)})
      </>
    ));
  const some = (s?: StatRef) => !!s && Number(s.stat.value) > 0;
  if (need && ATTACKING.has(need) && (some(t.goals) || some(t.assists)))
    return (
      <>
        {" "}
        He scores {n(t.goals!)} and sets up {n(t.assists!)} league goals per full match{rank}.
      </>
    );
  if (need === "full-back" && t.assists)
    return some(t.assists) ? (
      <>
        {" "}
        From full-back he sets up {n(t.assists)} league goals per full match{rank}.
      </>
    ) : (
      <> He has not set up a league goal in the matches on record{rank}.</>
    );
  if (t.minutesLastYear)
    return (
      <>
        {" "}
        A regular: {n(t.minutesLastYear)} minutes for {seller} in the past year&apos;s matches on record
        {t.caps && Number(t.caps.stat.value) > 0 ? <>, and {n(t.caps)} international caps</> : null}.
      </>
    );
  return null;
}

/** How he compares with the player he would replace, and what he adds to the side. */
function Complement({ t, gap, buyer, n }: { t: Target; gap?: Gap; buyer: string; n: (s: StatRef) => ReactNode }) {
  if (!gap?.barPlayer) return null;
  const mine = Number(t.goals?.stat.value ?? NaN) + Number(t.assists?.stat.value ?? NaN);
  const theirs = Number(gap.barGa?.stat.value ?? NaN);
  const bar = <span data-name>{gap.barPlayer}</span>;
  if (mine === mine && theirs === theirs && gap.barGa && mine > theirs * 1.2 + 0.02)
    return (
      <>
        {" "}
        That is more than {bar}, the last of {buyer}&apos;s regular starters there ({n(gap.barGa)} goals and assists per
        full match), so he would add end product.
      </>
    );
  return (
    <>
      {" "}
      He would compete with {bar}
      {gap.barValue && <> ({n(gap.barValue)})</>} for a starting place.
    </>
  );
}


const article = (word: string) => (/^[aeiou]/i.test(word) ? "an" : "a");
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** How he plays: his role (when one clearly leads) and his strongest features as top shares, against a named pool
 *  in one season ("A creative full-back: in the top 5% of full-backs in Europe's biggest leagues for chances
 *  created and the top 10% for shots, 2024-25 league season."). */
function StyleLine({ s, n }: { s: Style; n: (s: StatRef) => ReactNode }) {
  const [first, ...rest] = s.tops;
  const tops = first && (
    <>
      in the top {n(first.top)} of {s.pool} for {first.feature}
      {rest.map((x, i) => (
        <span key={x.feature}>
          {i === rest.length - 1 ? " and" : ","} the top {n(x.top)} for {x.feature}
        </span>
      ))}
    </>
  );
  return (
    <>
      {s.role ? (
        <>
          {cap(article(s.role))} {s.role}
          {tops ? <>: {tops}</> : <> among {s.pool}</>}
        </>
      ) : (
        <>In style he is {tops}</>
      )}{" "}
      ({n(s.season)} league season).
    </>
  );
}

/** What he would bring next to the player he would replace: the feature he adds most (both percentiles), else
 *  how that starter plays, else goal involvement against the starter's. */
function Fit({ t, gap, buyer, n }: { t: Target; gap?: Gap; buyer: string; n: (s: StatRef) => ReactNode }) {
  if (!gap?.barPlayer) return null;
  const bar = <span data-name>{gap.barPlayer}</span>;
  const role = gap.barStyle?.role;
  const who = (
    <>
      {bar}, the last of {buyer}&apos;s regular starters there{role && <> ({article(role)} {role})</>}
    </>
  );
  if (t.adds && t.style)
    return (
      <>
        {" "}
        Next to {who}, he would add {t.adds.feature}: he is ahead of {n(t.adds.mine)} of {t.style.pool} for it
        {t.adds.theirs && (
          <>
            , {bar} ahead of {n(t.adds.theirs)}
          </>
        )}
        .
      </>
    );
  if (t.style && gap.barStyle) {
    // what the side lacks there: the starter's weakest feature
    const low = gap.barLows[0];
    return (
      <>
        {" "}
        He would compete with {who}, for a starting place
        {low && (
          <>
            ; {bar} is ahead of only {n(low.pct)} of {gap.barStyle.pool} for {low.feature}
          </>
        )}
        .
      </>
    );
  }
  // a style line already says how he plays: goal involvement against the starter's only for players without one
  if (t.style)
    return (
      <>
        {" "}
        He would compete with {bar}
        {gap.barValue && <> ({n(gap.barValue)})</>} for a starting place.
      </>
    );
  return <Complement t={t} gap={gap} buyer={buyer} n={n} />;
}

/** Why this name is on this list, in the order a sporting director reads it: how he plays, what he adds next to the
 *  player he would replace, the list's own reason (the best available, a gettable move, a rising player), and the
 *  money last. Every number is a verified figure. */
const MOST = ["The most", "The second most", "The third most", "The fourth most", "The fifth most"];
const LIKELIEST = ["The likeliest", "The second likeliest", "The third likeliest", "The fourth likeliest"];

function Why({
  t,
  rank,
  buyer,
  need,
  gap,
  n,
}: {
  t: Target;
  rank: number;
  buyer: string;
  need?: string;
  gap?: Gap;
  n: (s: StatRef) => ReactNode;
}) {
  // club names can carry digits ("Parma Calcio 1913"): marked as names for the site's number check
  const seller = t.club ? <span data-name>{t.club}</span> : "his club";
  const months = t.contract ? Number(t.contract.stat.value) : NaN;
  const contract =
    t.contract && months <= 18 ? (
      <> Only {n(t.contract)} months are left on his contract, which brings the price down.</>
    ) : null;
  // press talk: how often the Rumour Mill linked him with this club, and (realistic list) how far it moved the odds
  const reports = t.pressReports ? Number(t.pressReports.stat.value) : 0;
  const press =
    t.pressReports && reports > 0 ? (
      <>
        {" "}
        The Guardian&apos;s Rumour Mill has linked him with {buyer} in {n(t.pressReports)}{" "}
        {reports === 1 ? "report" : "reports"} over the past year
        {t.kind === "realistic" && t.pressLift && (
          <>, which makes this move {n(t.pressLift)} times as likely as it would be without {reports === 1 ? "it" : "them"}</>
        )}
        .
      </>
    ) : null;
  const reason =
    t.kind === "best" ? (
      <>
        {" "}
        {MOST[rank] ? `${MOST[rank]} valuable regular starter` : "One of the most valuable regular starters"} we found
        {need ? ` at ${need}` : ""}.
      </>
    ) : t.kind === "realistic" ? (
      <>
        {" "}
        A move that can happen: {seller} sell players like him more often than most
        {t.pSold && <> ({n(t.pSold)} chance in a window)</>}, and players tend to move up to richer clubs like {buyer}.
      </>
    ) : (
      <>
        {" "}
        {t.age ? <>Aged {n(t.age)} and</> : "Young and"} already a regular
        {t.minutesNow && <> ({n(t.minutesNow)} of {seller}&apos;s minutes)</>}
        {t.projected && <>, projected to be worth {n(t.projected)} in a few seasons</>}.
      </>
    );
  return (
    <>
      {t.style ? (
        <StyleLine s={t.style} n={n} />
      ) : (
        // no style figures for him yet (his league's clubs are still being fetched): what he produces instead
        <Output t={t} need={need} seller={seller} n={n} />
      )}
      <Fit t={t} gap={gap} buyer={buyer} n={n} />
      {t.similar && (
        <>
          {" "}
          Closest in style: <span data-name>{t.similar}</span>.
        </>
      )}
      {reason}
      {!t.style && t.leagueMinutes && <> Transfermarkt lists {n(t.leagueMinutes)} league minutes for him last season.</>}
      {t.kind === "best" && t.minutesThere && (
        <>
          {" "}
          We expect him to play {n(t.minutesThere)} of {buyer}&apos;s minutes.
        </>
      )}
      {press}
      {t.kind === "realistic" && t.chance && (
        <>
          {" "}
          {LIKELIEST[rank] ?? "One of the likeliest"} move of those we checked ({n(t.chance)} in a window; any one move
          is a long shot).
        </>
      )}
      {contract}
      {t.kind === "best" && t.marketValue && <> Market value {n(t.marketValue)}.</>}
    </>
  );
}

/** League names (some carry digits: "Ligue 1"), joined as prose. */
function Names({ list }: { list: string[] }) {
  return (
    <>
      {list.map((x, i) => (
        <span key={x}>
          {i > 0 && (i === list.length - 1 ? " and " : ", ")}
          <span data-name>{x}</span>
        </span>
      ))}
    </>
  );
}

/** One line on how he plays: where he starts, his stronger foot, height, caps. */
function Profile({ t, n }: { t: Target; n: (s: StatRef) => ReactNode }) {
  const bits: ReactNode[] = [];
  if (t.positions) bits.push(<span key="p">Plays {t.positions}</span>);
  if (t.foot) bits.push(<span key="f">{t.foot === "both" ? "two-footed" : `${t.foot}-footed`}</span>);
  if (t.height)
    bits.push(
      <span key="h">
        {n(t.height)} <span data-meta>cm</span>
      </span>,
    );
  if (t.caps && Number(t.caps.stat.value) > 0)
    bits.push(
      <span key="c">
        {n(t.caps)} <span data-meta>caps</span>
      </span>,
    );
  if (t.injury) bits.push(<span key="i">injury risk {n(t.injury)}</span>);
  if (!bits.length) return null;
  return (
    <p className="mt-0.5 font-mono text-xs text-pencil">
      {bits.map((b, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {b}
        </span>
      ))}
    </p>
  );
}

function Pick({
  t,
  rank,
  buyer,
  gap,
  n,
  compact,
}: {
  t: Target;
  rank: number;
  buyer: string;
  gap?: Gap;
  n: (s: StatRef) => ReactNode;
  compact: boolean;
}) {
  const need = gap ? String(gap.need.stat.value) : undefined;
  return (
    <li>
      <p className={`font-display leading-tight ${compact ? "text-xl" : "text-2xl"}`}>{t.name}</p>
      <dl className="mt-0.5 flex flex-wrap gap-x-4 font-mono text-xs sm:text-sm">
        {t.club && (
          <div className="flex gap-1">
            <dt className="sr-only">Club</dt>
            <dd data-name>{t.club}</dd>
          </div>
        )}
        {t.age && (
          <div className="flex gap-1">
            <dt className="text-pencil">Age</dt>
            <dd>{n(t.age)}</dd>
          </div>
        )}
        {t.contract && (
          <div className="flex gap-1">
            <dt className="text-pencil">Contract</dt>
            <dd>
              {n(t.contract)} <span data-meta>months left</span>
            </dd>
          </div>
        )}
        {t.price && (
          <div className="flex gap-1">
            <dt className="text-pencil">Price</dt>
            <dd>
              {n(t.price)}
              {t.priceLow && t.priceHigh && (
                <span className="text-pencil">
                  {" "}
                  (likely {n(t.priceLow)}–{n(t.priceHigh)})
                </span>
              )}
            </dd>
          </div>
        )}
      </dl>
      <Profile t={t} n={n} />
      {compact ? (
        <details className="mt-0.5 text-sm">
          <summary className="mono-label cursor-pointer">Why him</summary>
          <p className="mt-0.5">
            <Why t={t} rank={rank} buyer={buyer} need={need} gap={gap} n={n} />
          </p>
        </details>
      ) : (
        <p className="mt-1 text-sm">
          <span className="mono-label">Why · </span>
          <Why t={t} rank={rank} buyer={buyer} need={need} gap={gap} n={n} />
        </p>
      )}
    </li>
  );
}

/** The scout's three lists for each of the club's two biggest needs. Native <details> (one open at a time across
 *  both positions) keeps it short on a phone. */
export default function Shortlists({
  targets,
  buyer,
  gaps,
  injury,
  n,
  compact = false,
}: {
  targets: Target[];
  buyer: string;
  /** the needs in order: their positions and who a signing has to beat */
  gaps: Gap[];
  /** leagues whose players have an injury tier */
  injury?: InjuryCoverage;
  n: (s: StatRef) => ReactNode;
  compact?: boolean;
}) {
  const needs = ([0, 1] as const).filter((k) => targets.some((t) => t.need === k));
  return (
    <div className="space-y-4">
      {needs.map((k) => {
        const gap = gaps[k];
        return (
          <section key={k} className="space-y-1">
            {gap && (
              <p className="font-display text-xl leading-tight sm:text-2xl">
                {k === 0 ? "Biggest need" : "Second need"}: <span className="italic">{String(gap.need.stat.value)}</span>
              </p>
            )}
            {KINDS.map((kind, i) => {
              const list = targets.filter((t) => t.need === k && t.kind === kind);
              if (!list.length) return null;
              return (
                <details
                  key={kind}
                  name={`shortlist-${buyer}`}
                  open={k === 0 && i === 0}
                  className="border-t-2 border-ink pt-1"
                >
                  <summary className={`mono-label cursor-pointer ${kind === "realistic" ? "text-red" : ""}`}>
                    {LIST_LABEL[kind]}
                  </summary>
                  <ol className={`mt-2 ${compact ? "space-y-2" : "space-y-4"}`}>
                    {list.map((t, j) => (
                      <Pick key={t.name} t={t} rank={j} buyer={buyer} gap={gap} n={n} compact={compact} />
                    ))}
                  </ol>
                </details>
              );
            })}
          </section>
        );
      })}
      <p className="mono-label text-pencil">
        Price is our fair fee for {buyer} today; the likely range is where most fees for players like him have
        landed. Wages are not shown: no free source publishes them.
      </p>
      {injury && injury.scored.length > 0 && (
        <p className="mono-label text-pencil">
          Injury risk is rated for players in <Names list={injury.scored} />
          {injury.unscored.length > 0 && (
            <>
              ; <Names list={injury.unscored} /> {injury.unscored.length > 1 ? "have" : "has"} too little injury data to
              rate
            </>
          )}
          ; other leagues have none.
        </p>
      )}
    </div>
  );
}
