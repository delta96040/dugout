"use client";

import { useRouter } from "next/navigation";

type Group = { league: string; clubs: { slug: string; name: string }[] };

/** Choose whose story to read: every club with a verified dossier, grouped by league (no JavaScript: plain links). */
export default function ClubPicker({ groups, current }: { groups: Group[]; current: string }) {
  const router = useRouter();
  const href = (slug: string) => `/story/${slug}/`;
  return (
    <div className="hang flex flex-wrap items-baseline gap-3 pt-4">
      <label htmlFor="club-picker" className="mono-label">
        Read the story of
      </label>
      <select
        id="club-picker"
        value={current}
        onChange={(e) => router.push(href(e.target.value))}
        className="max-w-full border-b border-ink bg-transparent pb-0.5 font-display text-xl"
        data-name
      >
        {groups.map((g) => (
          <optgroup key={g.league} label={g.league}>
            {g.clubs.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <noscript>
        <span className="font-mono text-xs">
          {groups.flatMap((g) =>
            g.clubs.map((c) => (
              <a key={c.slug} className="mr-2 underline" href={href(c.slug)}>
                {c.name}
              </a>
            )),
          )}
        </span>
      </noscript>
    </div>
  );
}
