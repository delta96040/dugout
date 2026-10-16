import type { Metadata } from "next";
import { Instrument_Serif, JetBrains_Mono, Newsreader } from "next/font/google";
import Link from "next/link";
import Tips from "@/components/Tips";
import "./globals.css";

const display = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-instrument-serif" });
const body = Newsreader({ subsets: ["latin"], variable: "--font-newsreader" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains-mono" });

export const metadata: Metadata = {
  title: "dugout — an open-source sporting director",
  description:
    "A free, open-source AI sporting director for the Premier League. Every number links to its source, timestamp and verification status.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 bg-bone px-3 py-2">
          Skip to content
        </a>
        <header className="grid12 relative z-20 items-baseline pt-5 pb-3">
          <Link href="/" className="col-span-12 font-display text-3xl italic sm:col-span-3">
            dugout
          </Link>
          <nav aria-label="Main" className="col-span-12 mt-2 flex flex-wrap gap-x-5 gap-y-1 sm:col-span-9 sm:mt-0 sm:justify-end">
            <Link className="mono-label hover:text-ink" href="/clubs/">Clubs</Link>
            <Link className="mono-label hover:text-ink" href="/scoreboard/">Scoreboard</Link>
            <Link className="mono-label hover:text-ink" href="/methods/">How it works</Link>
          </nav>
        </header>
        <main id="main">{children}</main>
        <Tips />
        <footer className="grid12 mt-24 pb-10">
          <p className="hang mono-label">
            Open source (<span data-meta>MIT</span>). Data: Fantasy Premier League, football-data.org, API-Football, Wikidata, transfermarkt-datasets (<span data-meta>CC0</span>),
            the Guardian, StatsBomb, SkillCorner, Wyscout (Pappalardo et al. <span data-meta>2019</span>), official club sites. Every number links to its
            source, timestamp and verification status.
          </p>
        </footer>
      </body>
    </html>
  );
}
