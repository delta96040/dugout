import type { Metadata } from "next";
import Link from "next/link";
import ClubList from "@/components/ClubList";
import { leagueSlug, leagues } from "@/lib/data";

export function generateStaticParams() {
  return leagues().map((l) => ({ league: leagueSlug(l) }));
}

function find(slug: string) {
  const l = leagues().find((x) => leagueSlug(x) === slug);
  if (!l) throw new Error(`unknown league ${slug}`);
  return l;
}

export async function generateMetadata({ params }: { params: Promise<{ league: string }> }): Promise<Metadata> {
  const { league } = await params;
  return { title: `${find(league).name}: choose your club — dugout` };
}

export default async function LeagueClubsPage({ params }: { params: Promise<{ league: string }> }) {
  const { league } = await params;
  const l = find(league);
  return (
    <section className="grid12 pt-10">
      <p className="hang mono-label">
        <Link href="/clubs/" className="underline hover:text-red">
          All leagues
        </Link>{" "}
        · <span data-name>{l.name}</span> · {l.country}
      </p>
      <h1 className="hang mt-3 font-display text-[clamp(2.8rem,7vw,5.5rem)] leading-[0.9]">
        Choose <span className="italic">your club.</span>
      </h1>
      <p className="hang mt-3 max-w-xl text-pencil">
        The story walks through the club in six chapters; the dossier is the full verified file.
      </p>
      <ClubList clubs={l.clubs} />
      <p className="hang mono-label mt-6">A club appears here once its dossier passes verification.</p>
    </section>
  );
}
