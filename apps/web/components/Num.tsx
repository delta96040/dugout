import { useId } from "react";
import type { Stat } from "@/lib/data";
import { metricName, sourceName, when } from "@/lib/format";

const STATUS: Record<string, string> = {
  verified: "Verified",
  single_source: "Single source, re-checked",
  model_output: "dugout estimate, re-checked",
  disputed: "Sources disagree",
};

/** A number on the site: shows its value; hover, focus or tap reveals where it came from and how it was checked.
 *  No client state: the card is CSS (`.tip` in globals.css) plus one page-wide listener (components/Tips.tsx). */
export default function Num({ stat, display, className = "" }: { stat: Stat; display: string; className?: string }) {
  const id = useId();
  const verified = stat.status === "verified";
  return (
    <span data-num className="tip relative inline-block">
      <button
        type="button"
        aria-describedby={id}
        className={`cursor-help ${className.includes("font-") ? "" : "font-mono"} underline decoration-dotted decoration-1 underline-offset-4 ${
          verified ? "decoration-ink" : "decoration-pencil"
        } ${className}`}
      >
        {display}
      </button>
      <span
        id={id}
        role="tooltip"
        className="tip-card absolute left-0 top-full z-40 mt-2 w-72 border-l-4 border-red bg-chalk p-3 text-left font-mono text-xs leading-relaxed text-ink"
        style={{ boxShadow: "6px 6px 0 rgba(20,20,20,0.85)" }}
      >
        <CardBody stat={stat} />
      </span>
    </span>
  );
}

/** What a provenance card says: metric and entity, source (linked) and tier, fetch time, status, cross-checks. */
export function CardBody({ stat }: { stat: Stat }) {
  const verified = stat.status === "verified";
  return (
    <>
    <span className="block text-[0.65rem] uppercase tracking-widest text-pencil">
      {metricName(stat.metric)} · {stat.entity_name}
    </span>
    <span className="mt-1 block">
      Source:{" "}
      {stat.source_url ? (
        <a className="underline" href={stat.source_url} target="_blank" rel="noopener noreferrer">
          {sourceName(stat.source)}
        </a>
      ) : (
        sourceName(stat.source)
      )}{" "}
      (tier {stat.source_tier})
    </span>
    <span className="block">Fetched: {when(stat.fetched_at)}</span>
    <span className={`block ${verified ? "text-red" : ""}`}>Status: {STATUS[stat.status] ?? stat.status}</span>
    {stat.cross_checks.length > 0 && (
      <span className="block text-pencil">
        Cross-checked with {stat.cross_checks.map((c) => `${c.source} (${c.agrees ? "agrees" : "differs"})`).join(", ")}
      </span>
    )}
    </>
  );
}
