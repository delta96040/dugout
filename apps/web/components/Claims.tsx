import type { Claim, Dossier, Section } from "@/lib/data";
import Num from "./Num";

export function ClaimText({ claim, d }: { claim: Claim; d: Dossier }) {
  return (
    <>
      {claim.segments.map((seg, i) =>
        "stat" in seg ? (
          <Num key={i} stat={d.stats[seg.stat]} display={seg.display} />
        ) : (
          <span key={i}>{withNames(seg.text, d.names ?? [])}</span>
        ),
      )}
    </>
  );
}

/** Mark proper names that contain digits ("Parma Calcio 1913") so they are not mistaken for statistics. */
function withNames(text: string, names: string[]) {
  if (!names.length) return text;
  const re = new RegExp(`(${names.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`);
  return text.split(re).map((part, i) => (i % 2 ? <span key={i} data-name>{part}</span> : part));
}

/** A published section: verified claims as prose; analysis marked; sources listed for news claims.
 *  `skip`: claims shown elsewhere on the page (the rumour cards). */
export function SectionProse({ section, d, skip }: { section: Section; d: Dossier; skip?: (c: Claim) => boolean }) {
  if (section.status !== "published") {
    return <p className="text-pencil">Nothing in this section passed verification today.</p>;
  }
  return (
    <div className="space-y-3">
      {section.claims.filter((c) => !skip?.(c)).map((c, i) => (
        <p key={i} className={c.status === "analysis" ? "border-l-2 border-pencil pl-3 italic text-ink/80" : ""}>
          {c.status === "analysis" && <span className="mono-label mr-2 not-italic">Our view</span>}
          <ClaimText claim={c} d={d} />
          {c.sources.length > 0 && (
            <span className="ml-2 font-mono text-xs text-pencil">
              [
              {c.sources.map((s, j) => (
                <span key={j}>
                  {j > 0 && ", "}
                  {s.url ? (
                    <a className="underline" href={s.url} target="_blank" rel="noopener noreferrer">
                      {s.outlet}
                    </a>
                  ) : (
                    s.outlet
                  )}
                  {s.official && " · official"}
                </span>
              ))}
              ]
            </span>
          )}
        </p>
      ))}
    </div>
  );
}
