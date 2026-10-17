import type { Metadata } from "next";
import { SectionProse } from "@/components/Claims";
import MetaNum from "@/components/MetaNum";
import Num from "@/components/Num";
import Shortlists from "@/components/Shortlists";
import { ordinal, plainReason, when } from "@/lib/format";
import { clubSlugs, dossier, firstStat, leagueOf } from "@/lib/data";
import { gaps, shortlists } from "@/lib/shortlist";

export function generateStaticParams() {
  return clubSlugs().map((slug) => ({ slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const d = dossier(slug);
  return { title: `${d.club.name} — dugout dossier`, description: `Verified sporting-director dossier on ${d.club.name}.` };
}

const ORDER = ["club", "match", "lineup", "news", "transfer", "scout"];

export default async function ClubPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const d = dossier(slug);
  const pos = firstStat(d, "league_position", slug);
  const v = d.verification;
  const sections = ORDER.map((a) => d.sections.find((s) => s.agent === a)).filter((s) => s !== undefined);
  const targets = shortlists(d);
  const need = d.key_stats?.scout?.find((r) => r.label === "squad need, priority one")?.display;
  return (
    <article>
      <section className="grid12 relative overflow-x-clip pt-10 pb-6">
        <p className="hang mono-label">Confidential · scouting dossier · <span data-name>{leagueOf(d).name}</span> · season <span data-meta>{d.club.season}</span></p>
        <h1 className="hang mt-3 font-display text-[clamp(3.5rem,11vw,9rem)] leading-[0.9]" data-name>{d.club.name}</h1>
        {pos && (
          <div className="col-span-12 -mt-4 sm:-mt-10">
            <p className="bleed text-ink/90" aria-label="League position">
              <Num stat={pos[1]} display={pos[1].display === String(pos[1].value) ? ordinal(Number(pos[1].value)) : pos[1].display} />
            </p>
          </div>
        )}
        <div className="hang mt-6 flex flex-wrap items-center gap-6">
          <span data-meta className="stamp text-lg">Verified ×2</span>
          <p className="mono-label max-w-xl">
            Every figure checked against its source · <MetaNum n={v.stat_auditor.numbers_total} note="figures on this page, each re-checked against its source" /> figures ·{" "}
            <MetaNum n={v.stat_auditor.numbers_untraceable} note="figures without a traceable source (always zero: anything else is removed)" /> unsourced · updated <span data-meta>{when(d.data_as_of)}</span>
          </p>
        </div>
      </section>

      <section className="grid12 mt-8">
        <div className="hang border-t border-ink pt-6 sm:col-span-7 sm:col-start-2">
          <h2 className="mono-label">The sporting director, in brief</h2>
          <div className="mt-3 font-display text-2xl leading-snug sm:text-3xl">
            <SectionProse section={d.summary} d={d} />
          </div>
        </div>
      </section>

      {sections.map((s, i) => (
        <section key={s.agent} className="grid12 mt-16" aria-labelledby={`h-${s.agent}`}>
          <div className={`col-span-12 sm:col-span-3 ${i % 2 ? "sm:col-start-9 sm:row-start-1" : "sm:col-start-2"}`}>
            <p className="mono-label"><span data-meta>0{i + 1}</span> · {s.agent}</p>
            <h2 id={`h-${s.agent}`} className="mt-1 font-display text-4xl leading-none">{s.title}</h2>
            <p className="mt-3 text-sm italic text-pencil">“{s.question}”</p>
          </div>
          <div className={`col-span-12 mt-4 sm:col-span-6 sm:mt-0 ${i % 2 ? "sm:col-start-2 sm:row-start-1" : "sm:col-start-6"}`}>
            <SectionProse section={s} d={d} />
            {s.agent === "scout" && targets.length > 0 && (
              <div className="mt-6">
                <Shortlists
                  targets={targets}
                  buyer={d.club.name}
                  gaps={gaps(d)}
                  n={(x) => <Num stat={x.stat} display={x.display} />}
                />
              </div>
            )}
            {s.withheld.length > 0 && (
              <details className="mt-3">
                <summary className="mono-label cursor-pointer">Removed in checking (<span data-meta>{s.withheld.length}</span>)</summary>
                <ul className="mt-2 space-y-1 text-sm text-pencil">
                  {s.withheld.map((w, j) => (
                    <li key={j}>
                      <span className="line-through">{w.text}</span> — <span data-meta>{plainReason(w.status, w.reason)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </section>
      ))}
    </article>
  );
}
