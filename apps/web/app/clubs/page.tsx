import Link from "next/link";
import { leagueSlug, leagues } from "@/lib/data";

export const metadata = { title: "Choose your league — dugout" };

// Step one of two: the league, then the club (/clubs/<league>/).
export default function LeaguesPage() {
  const all = leagues();
  return (
    <section className="grid12 pt-10">
      <p className="hang mono-label">Leagues with a verified dossier for their clubs</p>
      <h1 className="hang mt-3 font-display text-[clamp(2.8rem,7vw,5.5rem)] leading-[0.9]">
        Choose <span className="italic">your league.</span>
      </h1>
      <p className="hang mt-3 max-w-xl text-pencil">Then the club: its story in six chapters, or the full verified dossier.</p>
      <ol className="hang mt-6">
        {all.map((l) => (
          <li key={l.key} className="ruled grid grid-cols-12 items-baseline gap-3 py-3">
            <Link
              href={`/clubs/${leagueSlug(l)}/`}
              className="col-span-12 font-display text-4xl leading-none hover:italic hover:text-red sm:col-span-8 sm:text-6xl"
            >
              <span data-name>{l.name}</span>
            </Link>
            <span className="col-span-12 mono-label sm:col-span-4 sm:text-right">
              {l.country} · <span data-meta>{l.clubs.length}</span> clubs
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
