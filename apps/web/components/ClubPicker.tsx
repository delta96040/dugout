"use client";

import { useRouter } from "next/navigation";

/** Choose whose story to read: every club with a verified dossier (no JavaScript: plain links). */
export default function ClubPicker({ clubs, current }: { clubs: { slug: string; name: string }[]; current: string }) {
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
        className="border-b border-ink bg-transparent pb-0.5 font-display text-xl"
      >
        {clubs.map((c) => (
          <option key={c.slug} value={c.slug}>
            {c.name}
          </option>
        ))}
      </select>
      <noscript>
        <span className="font-mono text-xs">
          {clubs.map((c) => (
            <a key={c.slug} className="mr-2 underline" href={href(c.slug)}>
              {c.name}
            </a>
          ))}
        </span>
      </noscript>
    </div>
  );
}
