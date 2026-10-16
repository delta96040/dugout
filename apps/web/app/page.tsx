import Link from "next/link";

// The front door: what dugout is and how it checks itself; every club's story is one click away (/clubs/).
export default function Home() {
  return (
    <article className="grid12 pt-10 pb-6">
      <p className="hang mono-label">Open-source sporting director · Premier League</p>
      <h1 className="hang mt-3 max-w-5xl font-display text-[clamp(3.4rem,10vw,8.5rem)] leading-[0.88]">
        Every club, <span className="italic">read in full.</span>
      </h1>
      <p className="hang mt-8 max-w-2xl text-lg">
        Every night dugout reads free football data, refits its backtested models and writes a scouting dossier for each
        Premier League club: how the side plays, the next match, the likely XI, the gap in the squad and who could fill
        it, from the dream target to the realistic one and the hidden gem.
      </p>
      <p className="hang mt-8">
        <Link
          href="/clubs/"
          className="inline-block border-2 border-ink px-6 py-3 font-display text-3xl leading-none hover:bg-ink hover:text-bone focus-visible:bg-ink focus-visible:text-bone"
        >
          Choose your club <span aria-hidden="true">→</span>
        </Link>
      </p>

      <section className="col-span-12 mt-20 grid gap-8 border-t border-ink pt-6 sm:col-span-10 sm:col-start-2 sm:grid-cols-3" aria-label="How it works">
        <div>
          <h2 className="font-display text-2xl italic">The model never writes a number.</h2>
          <p className="mt-2 text-pencil">
            The writer uses fact codes; code fills every figure from an evidence ledger, and the Stat Auditor re-runs each
            one against its source.
          </p>
        </div>
        <div>
          <h2 className="font-display text-2xl italic">Two verifiers, or it is withheld.</h2>
          <p className="mt-2 text-pencil">
            Fact-Check needs an official source or two independent ones. Anything that fails is struck out with the
            reason, in public.
          </p>
        </div>
        <div>
          <h2 className="font-display text-2xl italic">Every number has a receipt.</h2>
          <p className="mt-2 text-pencil">
            Hover or tap any figure for its source, fetch time and status. The models&apos; backtests, weak results included,
            are on the methods page.
          </p>
        </div>
      </section>
      <p className="hang mono-label mt-10 flex flex-wrap gap-x-6 gap-y-2">
        <Link className="underline" href="/scoreboard/">Every prediction against the result</Link>
        <Link className="underline" href="/methods/">How the models are tested</Link>
        <a className="underline" href="https://github.com/delta96040/dugout" target="_blank" rel="noopener noreferrer">
          Source code (<span data-meta>MIT</span>)
        </a>
      </p>
    </article>
  );
}
