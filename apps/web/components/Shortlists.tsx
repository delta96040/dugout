import type { ReactNode } from "react";
import type { StatRef, Target } from "@/components/story/types";
import { type Gap, KINDS } from "@/lib/shortlist";

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

/** Why this name is on this list: how he plays and produces, how he compares with the player he would replace, and
 *  the list's own reason (the best available, a gettable move, a rising player). Every number is a verified figure. */
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
  const lead =
    t.kind === "best" ? (
      <>
        {MOST[rank] ? `${MOST[rank]} valuable regular starter` : "One of the most valuable regular starters"} we found
        {need ? ` at ${need}` : ""}
        {t.marketValue && <> (market value {n(t.marketValue)})</>}.
      </>
    ) : t.kind === "realistic" ? (
      <>
        A move that can happen: {seller} sell players like him more often than most
        {t.pSold && <> ({n(t.pSold)} chance in a window)</>}, and players tend to move up to richer clubs like {buyer}.
      </>
    ) : (
      <>
        {t.age ? <>Aged {n(t.age)} and</> : "Young and"} already a regular
        {t.minutesNow && <> ({n(t.minutesNow)} of {seller}&apos;s minutes)</>}
        {t.projected && <>, projected to be worth {n(t.projected)} in a few seasons</>}.
      </>
    );
  return (
    <>
      {lead}
      <Output t={t} need={need} seller={seller} n={n} />
      <Complement t={t} gap={gap} buyer={buyer} n={n} />
      {t.kind === "best" && t.minutesThere && (
        <>
          {" "}
          We expect him to play {n(t.minutesThere)} of {buyer}&apos;s minutes.
        </>
      )}
      {contract}
      {t.kind === "realistic" && t.chance && (
        <>
          {" "}
          {LIKELIEST[rank] ?? "One of the likeliest"} move of those we checked ({n(t.chance)} in a window; any one move
          is a long shot).
        </>
      )}
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
        {t.price && (
          <div className="flex gap-1">
            <dt className="text-pencil">Price</dt>
            <dd>{n(t.price)}</dd>
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
  n,
  compact = false,
}: {
  targets: Target[];
  buyer: string;
  /** the needs in order: their positions and who a signing has to beat */
  gaps: Gap[];
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
        Price is our fair fee for {buyer} today. Wages are not shown: no free source publishes them.
      </p>
    </div>
  );
}
