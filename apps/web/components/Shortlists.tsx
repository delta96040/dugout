import type { ReactNode } from "react";
import type { StatRef, Target } from "@/components/story/types";
import { KINDS } from "@/lib/shortlist";

export const LIST_LABEL: Record<Target["kind"], string> = {
  best: "Dream targets · whatever the cost",
  realistic: "Realistic targets · gettable",
  gem: "Hidden gems · young, rising",
};

/** Why this name is on this list, in words, with every number a verified figure. */
const RANK = ["The most", "The second most", "The third most", "The fourth most", "The fifth most"];

function Why({
  t,
  rank,
  buyer,
  need,
  n,
}: {
  t: Target;
  rank: number;
  buyer: string;
  need?: string;
  n: (s: StatRef) => ReactNode;
}) {
  // club names can carry digits ("Parma Calcio 1913"): marked as names for the site's number check
  const seller = t.club ? <span data-name>{t.club}</span> : "his club";
  if (t.kind === "best")
    return (
      <>
        {RANK[rank] ?? "One of the most"} valuable regular starter we found{need ? ` at ${need}` : ""}
        {t.marketValue && <> (market value {n(t.marketValue)})</>}.
        {t.minutesThere && <> We expect him to play {n(t.minutesThere)} of {buyer}&apos;s minutes.</>}
      </>
    );
  if (t.kind === "realistic")
    return (
      <>
        {seller} sell players like him more often than most
        {t.pSold && <> ({n(t.pSold)} chance in a window)</>}, players tend to move up to richer clubs like {buyer}, and
        he would be an upgrade on {buyer}&apos;s current option.
        {t.chance && <> Chance this move happens: {n(t.chance)}.</>}
      </>
    );
  return (
    <>
      Young and already a regular
      {t.minutesNow && <> ({n(t.minutesNow)} of {seller}&apos;s minutes over the last year)</>}, and cheaper today than{" "}
      {buyer}&apos;s current option.
      {t.projected && <> Projected to be worth {n(t.projected)} in a few seasons.</>}
    </>
  );
}

/** The scout's three lists for the top need. Native <details> (one open at a time) keeps it short on a phone. */
export default function Shortlists({
  targets,
  buyer,
  need,
  n,
  compact = false,
}: {
  targets: Target[];
  buyer: string;
  need?: string;
  n: (s: StatRef) => ReactNode;
  compact?: boolean;
}) {
  return (
    <div className="space-y-2">
      {KINDS.map((kind, i) => {
        const list = targets.filter((t) => t.kind === kind);
        if (!list.length) return null;
        return (
          <details key={kind} name={`shortlist-${buyer}`} open={i === 0} className="border-t-2 border-ink pt-1">
            <summary className={`mono-label cursor-pointer ${kind === "realistic" ? "text-red" : ""}`}>
              {LIST_LABEL[kind]}
            </summary>
            <ol className={`mt-2 ${compact ? "space-y-2" : "space-y-4"}`}>
              {list.map((t, j) => (
                <li key={t.name}>
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
                  {compact ? (
                    <details className="mt-0.5 text-sm">
                      <summary className="mono-label cursor-pointer">Why him</summary>
                      <p className="mt-0.5">
                        <Why t={t} rank={j} buyer={buyer} need={need} n={n} />
                      </p>
                    </details>
                  ) : (
                    <p className="mt-1 text-sm">
                      <span className="mono-label">Why · </span>
                      <Why t={t} rank={j} buyer={buyer} need={need} n={n} />
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </details>
        );
      })}
      <p className="mono-label text-pencil">
        Price is our fair fee for {buyer} today. Wages are not shown: no free source publishes them.
      </p>
    </div>
  );
}
