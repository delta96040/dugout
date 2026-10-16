import Link from "next/link";
import Num from "@/components/Num";
import { REPO } from "./constants";
import Scramble from "./Scramble";
import Stars from "./Stars";
import type { StatRef, StoryData } from "./types";

function N({ s, className }: { s?: StatRef; className?: string }) {
  return s ? <Num stat={s.stat} display={s.display} className={className} /> : <span className="text-pencil">n/a</span>;
}

const TARGET_LABEL = { best: "Best fit · dream target", realistic: "Realistic · gettable", gem: "Hidden gem · young" };

/** The six chapters' copy, rendered on the server. Scrambled numbers resolve when their chapter is on screen
 *  (Scramble reads the active chapter from context), so this needs no client state. */
export function chapterContent(d: StoryData) {
  return [
    {
      kicker: "Chapter one · read the club",
      body: (
        <>
          <h2 className="font-display text-[clamp(2.4rem,5.6vw,5.2rem)] leading-[0.9]">
            <span className="block">{d.club},</span>
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
          <h2 className="font-display text-[clamp(2.4rem,5.6vw,5.2rem)] leading-[0.9]">
            The gap is at <span className="italic text-red">{d.need ? <N s={d.need} className="font-display not-italic" /> : "—"}</span>.
          </h2>
          <p className="mt-6 max-w-md">
            Every position is compared with the six squads closest in strength: depth, the best option&apos;s value, ageing.
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
          <h2 className="font-display text-[clamp(2.2rem,5vw,4.4rem)] leading-[0.9]">
            Three ways <span className="italic">to fix it.</span>
          </h2>
          <ul className="mt-5 grid max-w-2xl gap-4 sm:grid-cols-3">
            {d.targets.map((t) => (
              <li key={t.kind} className="border-t-2 border-ink pt-2">
                <p className={`mono-label ${t.kind === "realistic" ? "text-red" : ""}`}>{TARGET_LABEL[t.kind]}</p>
                <p className="mt-1 font-display text-2xl leading-tight">{t.name}</p>
                {t.club && <p className="mono-label">{t.club}</p>}
                <dl className="mt-2 font-mono text-sm">
                  {t.age && (
                    <div className="flex justify-between gap-3">
                      <dt className="text-pencil">Age</dt>
                      <dd>
                        <Scramble s={t.age} chapter={2} />
                      </dd>
                    </div>
                  )}
                  {t.figure && (
                    <div className="flex justify-between gap-3">
                      <dt className="text-pencil">{t.figureLabel}</dt>
                      <dd>
                        <Scramble s={t.figure} chapter={2} />
                      </dd>
                    </div>
                  )}
                </dl>
              </li>
            ))}
          </ul>
          {d.rumours.length > 0 && (
            <div className="mt-5 max-w-2xl">
              <p className="mono-label">Linked in the press · rumours, not facts</p>
              <ul className="mt-1 space-y-0.5 text-sm">
                {d.rumours.slice(0, 3).map((r) => (
                  <li key={r.text}>
                    {r.text}{" "}
                    {r.url && (
                      <a className="font-mono text-xs underline" href={r.url} target="_blank" rel="noopener noreferrer">
                        The Guardian
                      </a>
                    )}
                  </li>
                ))}
              </ul>
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
                  {s.stat.metric.replaceAll("_", " ")} · {s.stat.entity_name}
                </span>
                <Scramble s={s} chapter={3} />
              </li>
            ))}
          </ul>
          <p className="mt-5 flex flex-wrap items-center gap-5">
            <span data-meta className="stamp text-2xl">Verified ×2</span>
            <span className="mono-label">
              Stat Auditor: every query re-run · Fact-Check: two independent sources or one official · hover any number
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
            MIT-licensed, built only on free data and open-weight models. Clone it, point it at your club, check every number yourself.
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
      <p className="mono-label">Model: Elo + xG Dixon–Coles stack, beats Elo on both test seasons; bookmakers are still better.</p>
    </div>
  );
}
