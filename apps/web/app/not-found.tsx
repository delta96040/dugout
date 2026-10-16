import Link from "next/link";

export default function NotFound() {
  return (
    <section className="grid12 pt-16">
      <p className="hang mono-label">Not in the file</p>
      <h1 className="hang mt-3 font-display text-[clamp(3rem,9vw,7rem)] leading-[0.9]">
        This page was <span className="italic text-red">never scouted.</span>
      </h1>
      <p className="hang mt-6">
        <Link className="underline" href="/clubs/">Back to the leagues</Link>
      </p>
    </section>
  );
}
