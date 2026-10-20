import Link from "next/link";
import Num from "@/components/Num";
import RumourBriefs from "@/components/RumourBriefs";
import Shortlists from "@/components/Shortlists";
import { metricName } from "@/lib/format";
import { REPO } from "./constants";
import Scramble from "./Scramble";
import Stars from "./Stars";
import type { Gap } from "@/lib/shortlist";
import type { StatRef, StoryData } from "./types";

function N({ s, className }: { s?: StatRef; className?: string }) {
  return s ? <Num stat={s.stat} display={s.display} className={className} /> : <span className="text-pencil">n/a</span>;
}

const num = (s?: StatRef) => (s ? Number(s.stat.value) : NaN);

/** Why a position is a need, in plain words: say which problem it is (too few players, a best option worth less
 *  than at similar clubs, an ageing group), then the figures behind it. Only the reasons that apply are given. */
function GapReason({ g, club, first }: { g: Gap; club: string; first: boolean }) {
  const thin = num(g.depth) < num(g.wanted);
  const weak = num(g.bestValue) < num(g.peerValue);
  const ageing = num(g.veterans) > 0;
  const verdict = [thin && "too few players", weak && "below its rivals", ageing && "ageing"].filter(Boolean);
  return (
    <>
      <span className="mono-label">
        {first ? "Biggest need" : "Second need"} · {String(g.need.stat.value)}
        {verdict.length > 0 && <> · {verdict.join(", ")}</>}
      </span>
      <br />
      {thin && g.depth && g.wanted && (
        <>
          Counted by where they start, its options add up to only <N s={g.depth} /> players, veterans aside,
          where a squad carries <N s={g.wanted} />.{" "}
        </>
      )}
      {weak && g.bestValue && g.peerValue && (
        <>
          The best of them{g.bestPlayer ? <>, <span data-name>{g.bestPlayer}</span>,</> : ""} is worth{" "}
          <N s={g.bestValue} />; at the clubs closest to {club} in strength the best option there is typically worth{" "}
          <N s={g.peerValue} />.{" "}
        </>
      )}
      {!thin && !weak && g.bestValue && (
        <>
          {club} have the numbers and the level there today
          {g.bestPlayer ? (
            <>
              {" "}
              (<span data-name>{g.bestPlayer}</span>, <N s={g.bestValue} />)
            </>
          ) : null}
          , so the need is about the years ahead.{" "}
        </>
      )}
      {ageing && g.veterans && (
        <>
          <N s={g.veterans} /> of the options are veterans, so the position needs renewing soon.
        </>
      )}
    </>
  );
}

/** The six chapters' copy, rendered on the server. Scrambled numbers resolve when their chapter is on screen
 *  (Scramble reads the active chapter from context), so this needs no client state. */
export function chapterContent(d: StoryData) {
  return [
    {
      kicker: "Chapter one · read the club",
      body: (
        <>
          <h2 className="font-display text-[clamp(2.4rem,5.6vw,5.2rem)] leading-[0.9]">
            <span className="block" data-name>{d.club},</span>
            <span className="block italic">read in full.</span>
          </h2>
          <p className="mt-6 max-w-md">
            {d.manager ? `${d.manager}'s side` : "The side"}
            {d.formation && (
              <>
                {" "}line up in a <N s={d.formation} />
              </>
            )}
            {d.position && (
              <>
                {d.formation ? " and" : ""} sit <N s={d.position} /> in the table
              </>
            )}
            .
            {d.threat && (
              <>
                {" "}His teams take <N s={d.threat} /> of the threat in their matches
                {d.defActions ? (
                  <>
                    {" "}and average <N s={d.defActions} /> defensive actions
                  </>
                ) : null}
                .
              </>
            )}
          </p>
        </>
      ),
    },
    {
      kicker: "Chapter two · find the gap",
      body: (
        <>
          <h2 className="font-display text-[clamp(2.2rem,5vw,4.6rem)] leading-[0.9]">
            {d.gaps.length > 1 ? "The gaps are at " : "The gap is at "}
            {d.gaps.map((g, i) => (
              <span key={g.need.id}>
                {i > 0 && " and "}
                <span className={`italic ${i === 0 ? "text-red" : ""}`}>
                  <N s={g.need} className="font-display not-italic" />
                </span>
              </span>
            ))}
            {d.gaps.length === 0 && "—"}.
          </h2>
          <ol className="mt-5 max-w-lg space-y-3 text-sm sm:text-base">
            {d.gaps.map((g, i) => (
              <li key={g.need.id}>
                <GapReason g={g} club={d.club} first={i === 0} />
              </li>
            ))}
          </ol>
          <p className="mt-4 max-w-md text-sm">
            Every position is compared with the six squads in its league closest in strength: depth, the best option&apos;s value, ageing.
            Players count where they actually start, not where they are listed.
            {d.signings.length > 0 && " Signings the club has already announced count before anything is recommended:"}
          </p>
          {d.signings.length > 0 && (
            <ul className="mt-2 max-w-md font-mono text-sm">
              {d.signings.map((s) => (
                <li key={s.title}>
                  {s.url ? (
                    <a className="underline" href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.title}
                    </a>
                  ) : (
                    s.title
                  )}{" "}
                  <span className="text-pencil">· official</span>
                </li>
              ))}
            </ul>
          )}
        </>
      ),
    },
    {
      kicker: "Chapter three · scout the fix",
      body: d.targets.length ? (
        <>
          <h2 className="font-display text-[clamp(2rem,5vw,4.4rem)] leading-[0.9]">
            Three ways <span className="italic">to fix it.</span>
          </h2>
          <div className="mt-4 max-w-2xl">
            <Shortlists
              targets={d.targets}
              buyer={d.club}
              gaps={d.gaps}
              injury={d.injury}
              n={(s) => <Scramble s={s} chapter={2} />}
              compact
            />
          </div>
          {d.rumours.length > 0 && (
            <div className="mt-4 max-w-2xl">
              <p className="mono-label">
                Linked in the press · most credible first · rumours, not facts ·{" "}
                <Link className="underline" href="/rumours/">
                  how sources compare
                </Link>
              </p>
              <RumourBriefs briefs={d.rumours.slice(0, 2)} n={(s) => <Scramble s={s} chapter={2} />} compact />
            </div>
          )}
        </>
      ) : (
        <p>No shortlist passed verification today.</p>
      ),
    },
    {
      kicker: "Chapter four · check every number",
      body: (
        <>
          <h2 className="font-display text-[clamp(2.4rem,5.6vw,5.2rem)] leading-[0.9]">
            Every number <span className="italic">has a receipt.</span>
          </h2>
          <ul className="mt-6 max-w-lg space-y-1 font-mono text-sm">
            {d.verifiedSamples.map((s) => (
              <li key={s.id} className="flex justify-between gap-4 border-b border-ink/30 py-1">
                <span className="text-pencil">
                  <span data-meta>{s.label ?? metricName(s.stat.metric)}</span> · <span data-name>{s.stat.entity_name}</span>
                </span>
                <Scramble s={s} chapter={3} />
              </li>
            ))}
          </ul>
          <p className="mt-5 flex flex-wrap items-center gap-5">
            <span data-meta className="stamp text-2xl">Verified ×2</span>
            <span className="mono-label">
              Every figure re-checked against its source · news needs an official source or two independent ones · hover any number
            </span>
          </p>
        </>
      ),
    },
    {
      kicker: "Chapter five · predict Saturday",
      body: d.prediction ? (
        <>
          <h2 className="font-display text-[clamp(2.2rem,5vw,4.6rem)] leading-[0.9]">
            {d.prediction.home} <span className="italic">v</span> {d.prediction.away}
          </h2>
          <p className="mono-label mt-2">
            <N s={d.prediction.kickoff} />
          </p>
          <ChalkLines d={d} />
        </>
      ) : (
        <p>No upcoming fixture.</p>
      ),
    },
    {
      kicker: "Chapter six · fork it",
      body: (
        <>
          <h2 className="font-display text-[clamp(2.4rem,5.6vw,5.2rem)] leading-[0.9]">
            Open source. Free. <span className="italic text-red">Yours.</span>
          </h2>
          <p className="mt-6 max-w-md">
            Free and open source, built only on free data and open AI. Point it at your club and check every number yourself.
          </p>
          <p className="mt-4 flex flex-wrap items-center gap-5 font-mono text-sm">
            <a className="border-b-2 border-red pb-0.5" href={REPO} target="_blank" rel="noopener noreferrer">
              <span data-meta>github.com/delta96040/dugout</span>
            </a>
            <Stars />
            <Link className="underline" href={`/club/${d.slug}/`}>
              Read the full dossier
            </Link>
          </p>
        </>
      ),
    },
  ];
}

function ChalkLines({ d }: { d: StoryData }) {
  const p = d.prediction!;
  const rows: [string, StatRef | undefined][] = [
    [`${p.home} win`, p.pHome],
    ["Draw", p.pDraw],
    [`${p.away} win`, p.pAway],
  ];
  return (
    <div className="mt-6 max-w-xl space-y-3">
      {rows.map(([label, s], i) => {
        const v = s ? Number(s.stat.value) : 0;
        return (
          <div key={label}>
            <div className="flex justify-between font-mono text-sm">
              <span>{label}</span>
              <N s={s} />
            </div>
            {/* a chalk stroke whose length is the probability */}
            <svg viewBox="0 0 100 4" preserveAspectRatio="none" className="mt-1 h-2 w-full" aria-hidden="true">
              <path d={`M0 2 Q ${v * 50} ${1 + 0.7 * i}, ${v * 100} 2`} stroke="#141414" strokeWidth="2.2" strokeLinecap="round" fill="none" opacity="0.85" />
            </svg>
          </div>
        );
      })}
      <p className="mono-label">Forecast from team ratings and chance quality, tested on two full seasons: better than a classic rating system, not yet as sharp as the bookmakers.</p>
    </div>
  );
}
