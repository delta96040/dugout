import Link from "next/link";
import Num from "@/components/Num";
import { allDossiers, firstStat } from "@/lib/data";

export const metadata = { title: "Choose your club — dugout" };

export default function ClubsPage() {
  const clubs = allDossiers()
    .map((d) => ({ d, pos: firstStat(d, "league_position", d.club.slug), form: firstStat(d, "league_form", d.club.slug) }))
    .sort((a, b) => Number(a.pos?.[1].value ?? 99) - Number(b.pos?.[1].value ?? 99));
  return (
    <section className="grid12 pt-10">
      <p className="hang mono-label">Premier League · clubs with a verified dossier</p>
      <h1 className="hang mt-3 font-display text-[clamp(2.8rem,7vw,5.5rem)] leading-[0.9]">
        Choose <span className="italic">your club.</span>
      </h1>
      <p className="hang mt-3 max-w-xl text-pencil">
        The story walks through the club in six chapters; the dossier is the full verified file.
      </p>
      <ol className="hang mt-6">
        {clubs.map(({ d, pos, form }) => (
          <li key={d.club.slug} className="ruled grid grid-cols-12 items-baseline gap-3 py-3">
            <span className="col-span-2 font-mono text-sm text-pencil sm:col-span-1">
              {pos ? <Num stat={pos[1]} display={String(pos[1].display)} /> : "—"}
            </span>
            <Link
              href={`/story/${d.club.slug}/`}
              className="col-span-10 font-display text-4xl leading-none hover:italic hover:text-red sm:col-span-8 sm:text-6xl"
            >
              {d.club.name}
            </Link>
            <span className="col-span-12 flex flex-col items-end gap-1 sm:col-span-3">
              <span className="font-mono text-sm tracking-[0.3em]">
                {form ? <Num stat={form[1]} display={form[1].display.split("").join(" ")} /> : null}
              </span>
              <span className="flex gap-4">
                <Link href={`/story/${d.club.slug}/`} className="mono-label underline hover:text-red">
                  Story
                </Link>
                <Link href={`/club/${d.club.slug}/`} className="mono-label underline hover:text-red">
                  Dossier
                </Link>
              </span>
            </span>
          </li>
        ))}
      </ol>
      {clubs.length < 20 && (
        <p className="hang mono-label mt-6">
          Other clubs appear here once their dossier passes verification (run <code>dugout setup-club &lt;club&gt; --build</code>).
        </p>
      )}
    </section>
  );
}
