import Link from "next/link";
import Num from "@/components/Num";
import { firstStat, type Dossier } from "@/lib/data";

/** A league's clubs in table order, each with its story and dossier (plain links: works without JavaScript). */
export default function ClubList({ clubs }: { clubs: Dossier[] }) {
  const rows = clubs
    .map((d) => ({ d, pos: firstStat(d, "league_position", d.club.slug), form: firstStat(d, "league_form", d.club.slug) }))
    .sort((a, b) => Number(a.pos?.[1].value ?? 99) - Number(b.pos?.[1].value ?? 99));
  return (
    <ol className="hang mt-6">
      {rows.map(({ d, pos, form }) => (
        <li key={d.club.slug} className="ruled grid grid-cols-12 items-baseline gap-3 py-3">
          <span className="col-span-2 font-mono text-sm text-pencil sm:col-span-1">
            {pos ? <Num stat={pos[1]} display={String(pos[1].display)} /> : "—"}
          </span>
          <Link
            href={`/story/${d.club.slug}/`}
            className="col-span-10 font-display text-4xl leading-none hover:italic hover:text-red sm:col-span-8 sm:text-6xl"
          >
            <span data-name>{d.club.name}</span>
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
  );
}
