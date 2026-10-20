import Num from "@/components/Num";
import RumourBriefs from "@/components/RumourBriefs";
import { allDossiers, readData } from "@/lib/data";
import { briefScore, rumourBriefs } from "@/lib/rumourBrief";
import { int, pct, recordStat, type Outlet, type RumourRecord, RUMOURS_DOC } from "@/lib/rumours";

export const metadata = {
  title: "Rumour track record — dugout",
  description:
    "Transfer rumours as a sporting director needs them: how reliable the source is, how widely the link is reported, our own estimate of the move, and what became of the source's earlier talk.",
};

const SHOWN_RECENT = 12;
const LABELS: [string, string][] = [
  ["reliable", "its reports came true more often than similar reports from anyone, by more than luck explains"],
  ["about average", "no clear difference from similar reports: most outlets, and the Rumour Mill's own round-ups"],
  ["speculative", "its reports came true less often than similar reports, by more than luck explains"],
  ["unproven", "too few of its reports traced to judge"],
];

export default function RumoursPage() {
  const r = readData<RumourRecord>("rumours.json");
  // the rumours on the club pages: confirmed against the column by Fact-Check, each with its brief
  const recent = allDossiers()
    .flatMap(rumourBriefs)
    .sort((a, b) => briefScore(b) - briefScore(a))
    .slice(0, SHOWN_RECENT);
  if (!r) {
    return (
      <section className="grid12 pt-10">
        <p className="hang">The rumour track record is written by the nightly pipeline (`dugout-models publish rumours`).</p>
      </section>
    );
  }
  const at = r.generated_at;
  const S = (value: number, display: string, metric: string, subject: string) => (
    <Num stat={recordStat(value, display, metric, subject, at)} display={display} />
  );
  const b = r.backtest;
  const B = (value: number | null, display: (v: number) => string, metric: string) =>
    value === null ? (
      <span className="text-pencil">n/a</span>
    ) : (
      <Num stat={recordStat(value, display(value), metric, "rumour track record test", b.generated_at ?? at, "backtest")} display={display(value)} />
    );
  // an outlet's label, with the record behind it on hover (data from before the labels: the shrunk record)
  const Label = ({ o }: { o: Outlet }) =>
    o.label ? (
      <Num
        stat={{ ...recordStat(o.raw, o.label, "source credibility, from its record", o.outlet, at) }}
        display={o.label}
        className="font-body"
        detail={
          <>
            Of {int(o.links)} reports traced, {int(o.came_true)} came true within half a year ({pct(o.raw)})
            {o.expected_rate !== undefined && <>; similar reports from anyone came true {pct(o.expected_rate)} of the time</>}.
          </>
        }
      />
    ) : (
      S(o.shrunk, pct(o.shrunk), "record, pulled towards the average", o.outlet)
    );
  const peak = Math.max(...r.seasons.map((s) => s.rate));
  return (
    <article className="grid12 pt-10">
      <p className="hang mono-label">Rumour track record · what became of the transfer talk</p>
      <h1 className="hang mt-3 font-display text-[clamp(3rem,9vw,7rem)] leading-[0.9]">
        Which rumours <span className="italic">to take seriously.</span>
      </h1>
      <p className="hang mt-6 max-w-2xl">
        Every rumour on a club page comes with four things a sporting director asks before picking up the phone:{" "}
        <b>who says it</b> and how their earlier talk turned out, <b>how hot it is</b> (independent outlets carrying the
        same link, counted once per media group, and since when), <b>whether the move is realistic</b> (our own estimate
        of this player joining this club in a window, his contract, how often his club sells) and{" "}
        <b>the source&apos;s track record</b> on the same club. The most credible and most widely reported come first.
      </p>
      <p className="hang mt-4 max-w-2xl">
        The record behind it: every Guardian Rumour Mill column since its first, each player it linked with a club checked
        against the moves that actually happened. Of {S(r.overall.links, int(r.overall.links), "rumours traced", "all seasons")}{" "}
        rumours we could trace since <span data-meta>{r.first_season}</span>,{" "}
        {S(r.overall.came_true, int(r.overall.came_true), "came true within half a year", "all seasons")} came true within half a
        year.
        {r.overall.rate_modern !== null && (
          <>
            {" "}Since <span data-meta>{r.modern_season}</span>, when the column became shorter and more selective,{" "}
            {S(r.overall.rate_modern, pct(r.overall.rate_modern), "share that came true", `since ${r.modern_season}`)} of{" "}
            {S(r.overall.links_modern, int(r.overall.links_modern), "rumours traced", `since ${r.modern_season}`)} did.
          </>
        )}
      </p>

      {recent.length > 0 && (
        <section className="hang mt-10 max-w-3xl">
          <h2 className="mono-label">Linked in the press now · most credible and most reported first · rumours, not facts</h2>
          <RumourBriefs briefs={recent} n={(x) => <Num stat={x.stat} display={x.display} />} showClub />
        </section>
      )}

      <section className="hang mt-12 max-w-3xl">
        <h2 className="mono-label">How we rate a source</h2>
        <p className="mt-2">
          A source is judged against what similar reports did, not against an average: a paper that only repeats deadline-day
          certainties would otherwise look brilliant. For each outlet we add up how often the rumours it was credited with
          would have come true from anyone (from what each rumour shows: repeat reports, how many clubs it names, the
          window, the player&apos;s value), and compare that with how many did. Hover a label for the record behind it.
        </p>
        <dl className="mt-3 space-y-1">
          {LABELS.map(([label, meaning]) => (
            <div key={label} className="grid grid-cols-[8rem_1fr] gap-3">
              <dt className="font-semibold">{label}</dt>
              <dd>{meaning}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3">
          Honest caveats: most items are the column&apos;s own round-ups, so most rumours today carry its label, about average;
          an outlet is known only when the column links to it or names it. Very few outlets differ from the rest by more than
          luck explains, and too few of their later reports exist yet to confirm the labels hold.{" "}
          <a className="underline" href={RUMOURS_DOC}>How it is tested</a>.
        </p>
      </section>

      <section className="hang mt-10 max-w-3xl">
        <h2 className="mono-label">By outlet · where the column credits one</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[420px] border-collapse font-mono text-sm">
            <caption className="sr-only">Outlets: rumours traced and the label their record earns</caption>
            <thead>
              <tr className="ruled text-left">
                <th scope="col" className="mono-label py-2 font-normal">Outlet</th>
                <th scope="col" className="mono-label py-2 text-right font-normal">Rumours traced</th>
                <th scope="col" className="mono-label py-2 text-right font-normal">Source</th>
              </tr>
            </thead>
            <tbody>
              {r.outlets.map((o) => (
                <tr key={o.outlet} className="border-b border-ink/15">
                  <td data-name className="py-1.5 pr-3 font-body text-base">
                    {o.outlet === "The Guardian" ? "The Rumour Mill itself" : o.outlet}
                  </td>
                  <td className="py-1.5 text-right">{S(o.links, int(o.links), "rumours traced", o.outlet)}</td>
                  <td className="py-1.5 text-right">
                    <Label o={o} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-sm">
          The estimate behind &ldquo;similar reports&rdquo; was tested on the latest season: a Brier score of{" "}
          {B(b.brier, (v) => v.toFixed(3), "Brier score, rumour track record")} against{" "}
          {B(b.brier_recent_base, (v) => v.toFixed(3), "Brier score, recent base rate")} for the recent average (lower is
          better), and the rumours it rated likelier did come true more often.
        </p>
      </section>

      <section className="hang mt-10 max-w-3xl">
        <h2 className="mono-label">By season · share that came true within half a year</h2>
        <ul className="mt-3 space-y-1 font-mono text-sm">
          {r.seasons.map((s) => (
            <li key={s.season} className="grid grid-cols-[5.5rem_1fr_4rem_5rem] items-center gap-3">
              <span data-meta className="text-pencil">{s.season}</span>
              <span className="block h-2 bg-ink/10" aria-hidden="true">
                <span className="block h-2 bg-red" style={{ width: `${(s.rate / peak) * 100}%` }} />
              </span>
              <span className="text-right">{S(s.rate, pct(s.rate), "share that came true", s.season)}</span>
              <span className="text-right text-pencil">{S(s.links, int(s.links), "rumours traced", s.season)}</span>
            </li>
          ))}
        </ul>
        <p className="mono-label mt-4">
          Before <span data-meta>2012-13</span> fewer moves are on record, so early seasons are probably understated. A move counts
          only if Transfermarkt&apos;s open dataset or Wikipedia&apos;s transfer lists have it.
        </p>
      </section>
    </article>
  );
}
