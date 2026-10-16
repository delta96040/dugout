import Link from "next/link";
import { useId } from "react";

/** A number *about* the verification (counts from the audit report), linked to how it is computed. */
export default function MetaNum({ n, note }: { n: number; note: string }) {
  const id = useId();
  return (
    <span data-meta className="tip relative inline-block">
      <button
        type="button"
        aria-describedby={id}
        className="cursor-help font-mono underline decoration-dotted underline-offset-4"
      >
        {n}
      </button>
      <span
        id={id}
        role="tooltip"
        className="tip-card absolute left-0 top-full z-40 mt-2 w-64 border-l-4 border-red bg-chalk p-3 font-mono text-xs normal-case tracking-normal text-ink"
        style={{ boxShadow: "6px 6px 0 rgba(20,20,20,0.85)" }}
      >
        {note}. Source: the checks run on this page. <Link className="underline" href="/methods/">How it works</Link>
      </span>
    </span>
  );
}
