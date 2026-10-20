import Link from "next/link";
import type { ReactNode } from "react";
import Num from "@/components/Num";
import type { StatRef } from "@/components/story/types";
import type { RumourBrief } from "@/lib/rumourBrief";

// what each source label means, in words (dugout_models.rumours.credibility_label)
const MEANING: Record<string, string> = {
  reliable: "its reports come true more often than similar reports do, by more than luck explains",
  "about average": "its reports come true about as often as similar reports from anyone",
  speculative: "its reports come true less often than similar reports do, by more than luck explains",
  unproven: "too few of its reports traced to judge",
};

const many = (s: StatRef | undefined, one: string, more: string) => (Number(s?.stat.value) === 1 ? one : more);

function sourceName(b: RumourBrief): string {
  const outlet = b.credibility?.stat.entity_id ?? "The Guardian";
  return outlet === "The Guardian" ? "The Rumour Mill's own round-up" : outlet;
}

/** The source's label; its record is on hover (raw rate, reports traced, what similar reports did). */
function Credibility({ b }: { b: RumourBrief }) {
  const c = b.credibility;
  if (!c) return <span className="text-pencil">no record yet</span>;
  const label = String(c.stat.value);
  return (
    <Num
      stat={c.stat}
      display={label}
      className="font-body font-semibold"
      detail={
        <>
          {MEANING[label] ?? ""}.
          {b.traced && b.cameTrue && b.record && (
            <>
              {" "}Of {b.traced.display} reports credited to it that we traced, {b.cameTrue.display} came true within half a
              year ({b.record.display})
              {b.similar && <>; similar reports from anyone came true {b.similar.display} of the time</>}.
            </>
          )}
        </>
      }
    />
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] gap-x-3 sm:grid-cols-[7rem_1fr]">
      <dt className="mono-label pt-0.5">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Rumours as a sporting director reads them: who reports it and how reliable they are, how widely it is
 *  carried, what we think of the move, and what became of the same source's earlier talk. Most credible and most
 *  widely reported first (lib/rumourBrief.briefScore). `n` renders a verified figure (Num, or Scramble in the story). */
export default function RumourBriefs({
  briefs,
  n,
  showClub = false,
  compact = false,
}: {
  briefs: RumourBrief[];
  n: (s: StatRef) => ReactNode;
  showClub?: boolean;
  compact?: boolean;
}) {
  return (
    <ul className={compact ? "mt-2 space-y-3 text-sm" : "mt-3 space-y-5"}>
      {briefs.map((b) => {
        const seller = b.seller ? String(b.seller.stat.value) : undefined;
        return (
          <li key={`${b.slug}:${b.player}`} className="border-l-4 border-red pl-3">
            <p className={compact ? "font-display text-lg leading-tight" : "font-display text-2xl leading-tight"}>
              {showClub ? (
                <>
                  <Link className="underline" href={`/club/${b.slug}/`}>
                    <span data-name>{b.buyer}</span>
                  </Link>{" "}
                  <span className="italic">want</span> <span data-name>{b.player}</span>
                </>
              ) : (
                <span data-name>{b.player}</span>
              )}
              {seller && (
                <span className="font-body text-base text-pencil">
                  {" "}· <span data-name>{seller}</span>
                </span>
              )}
            </p>
            <p className="mt-1">
              {b.text}{" "}
              {b.url && (
                <a className="font-mono text-xs underline" href={b.url} target="_blank" rel="noopener noreferrer">
                  The Guardian
                </a>
              )}
            </p>
            <dl className="mt-2 space-y-1">
              <Row label="Source">
                <span data-name>{sourceName(b)}</span>: <Credibility b={b} />
              </Row>
              {b.outlets && (
                <Row label="Heat">
                  {n(b.outlets)} independent {many(b.outlets, "outlet", "outlets")}
                  {b.reports && (
                    <>
                      , {n(b.reports)} {many(b.reports, "report", "reports")}
                    </>
                  )}
                  {b.since && <> since {n(b.since)}</>}
                  {!compact && b.outletNames && (
                    <span className="text-pencil">
                      {" "}(<span data-name>{String(b.outletNames.stat.value)}</span>)
                    </span>
                  )}
                </Row>
              )}
              {(b.estimate || b.contract || b.sellerSales) && (
                <Row label="Reality">
                  {b.estimate && (
                    <>
                      We put this move at {n(b.estimate)} in a window
                      {!compact && b.sale && b.thisBuyer && (
                        <>
                          {" "}({seller ? <span data-name>{seller}</span> : "his club"} selling him {n(b.sale)},{" "}
                          <span data-name>{b.buyer}</span> the buyer if he goes {n(b.thisBuyer)})
                        </>
                      )}
                      .{" "}
                    </>
                  )}
                  {b.contract && <>{n(b.contract)} months left on his contract. </>}
                  {!compact && b.sellerSales && seller && (
                    <>
                      <span data-name>{seller}</span> have sold {n(b.sellerSales)} players for a fee
                      {b.sellerSince && <> since {n(b.sellerSince)}</>}.
                    </>
                  )}
                </Row>
              )}
              {b.earlier.length > 0 && (
                <Row label="Track record">
                  <ul className="space-y-0.5">
                    {b.earlier.slice(0, compact ? 1 : 2).map((e) => {
                      const came = String(e.outcome.stat.value).startsWith("joined");
                      return (
                        <li key={e.outcome.id} className={came ? "" : "text-ink/75"}>
                          <span aria-hidden="true" className={`mr-1 font-mono ${came ? "text-red" : "text-pencil"}`}>
                            {came ? "✓" : "✗"}
                          </span>
                          {e.date && <>{n(e.date)}: </>}
                          the same source linked{" "}
                          {e.player ? <span data-name>{String(e.player.stat.value)}</span> : "a player"} with{" "}
                          {e.club ? <span data-name>{String(e.club.stat.value)}</span> : "the club"}; he {n(e.outcome)}.
                        </li>
                      );
                    })}
                  </ul>
                </Row>
              )}
            </dl>
          </li>
        );
      })}
    </ul>
  );
}
