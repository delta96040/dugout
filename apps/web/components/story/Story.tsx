"use client";

import Lenis from "lenis";
import dynamic from "next/dynamic";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { ActiveChapter } from "./active";
import ChalkCursor from "./ChalkCursor";
import { CHAPTERS, chapterAt, story } from "./store";
import type { StoryData } from "./types";

const Scene = dynamic(() => import("./Scene"), { ssr: false });

function canAnimate(): boolean {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  // keep weak or data-saving devices on the static story (same text, SVG pitch)
  const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } };
  if (nav.connection?.saveData || (nav.deviceMemory !== undefined && nav.deviceMemory <= 4)) return false;
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

/** The scroll story: one persistent WebGL canvas pinned behind six chapters of real, verified data.
 *  The chapters and the SVG pitch are rendered on the server and passed in, so only this shell hydrates. */
export default function Story({
  scene,
  pitch,
  chapters,
}: {
  scene: StoryData["scene"];
  pitch: ReactNode;
  chapters: { kicker: string; body: ReactNode }[];
}) {
  const [mode, setMode] = useState<"pending" | "webgl" | "static">("pending");
  const [chapter, setChapter] = useState(0);
  const [local, setLocal] = useState(0);
  const root = useRef<HTMLDivElement>(null);

  // The static story (full text + SVG pitch) paints first; the WebGL scene loads on the first interaction, so the
  // three.js cost is paid only by visitors who engage with the story.
  useEffect(() => {
    if (!canAnimate()) {
      setMode("static");
      return;
    }
    const events = ["wheel", "touchstart", "pointermove", "keydown", "scroll"] as const;
    const go = () => {
      events.forEach((e) => window.removeEventListener(e, go));
      setMode("webgl");
    };
    events.forEach((e) => window.addEventListener(e, go, { once: true, passive: true }));
    return () => events.forEach((e) => window.removeEventListener(e, go));
  }, []);

  useEffect(() => {
    if (mode !== "webgl") return;
    // Lenis smooth scroll; our own scrub replaces GSAP ScrollTrigger (GSAP's licence is not open source)
    const lenis = new Lenis({ lerp: 0.09 });
    let raf = 0;
    const frame = (t: number) => {
      lenis.raf(t);
      const el = root.current;
      if (el) {
        const r = el.getBoundingClientRect();
        const span = r.height - window.innerHeight;
        story.progress = Math.min(Math.max(-r.top / span, 0), 1);
        const c = chapterAt(story.progress);
        setChapter((prev) => (prev === c.index ? prev : c.index));
        setLocal((prev) => (Math.abs(prev - c.local) < 0.01 ? prev : c.local)); // re-render only on visible change
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    const move = (e: PointerEvent) => {
      story.mouse = [(e.clientX / window.innerWidth) * 2 - 1, -((e.clientY / window.innerHeight) * 2 - 1)];
    };
    window.addEventListener("pointermove", move);
    return () => {
      cancelAnimationFrame(raf);
      lenis.destroy();
      window.removeEventListener("pointermove", move);
    };
  }, [mode]);

  if (mode === "static" || mode === "pending") {
    // reduced motion / no WebGL (and the server render): the same text over a static SVG pitch
    return (
      <div className="grid12 pt-6">
        <div className="hang">{pitch}</div>
        {chapters.map((c, i) => (
          <section key={i} className="hang mt-20" aria-label={c.kicker}>
            <p className="mono-label">{c.kicker}</p>
            <div className="mt-3">{c.body}</div>
          </section>
        ))}
      </div>
    );
  }

  return (
    <ActiveChapter.Provider value={chapter}>
      <div ref={root} className="relative" style={{ height: `${CHAPTERS * 150}vh` }}>
        <div className="sticky top-0 h-screen w-full overflow-hidden" style={{ cursor: "none" }}>
          <div className="absolute inset-0">
            <Scene {...scene} />
          </div>
          <ChalkCursor />
          {chapters.map((c, i) => {
            const visible = i === chapter;
            // reveal on entry, drift upward as the chapter is scrolled (parallax), follow the cursor slightly
            const reveal = visible ? (i === 0 ? 1 : Math.min(local / 0.15, 1)) : 0; // the first chapter is there on arrival
            const lift = visible ? (local - 0.5) * -40 : 0;
            return (
              <section
                key={i}
                aria-label={c.kicker}
                className="grid12 pointer-events-none absolute inset-x-0 top-[14vh]"
                style={{
                  opacity: reveal,
                  transform: `translate3d(${story.mouse[0] * -8}px, ${lift + (1 - reveal) * 30 + story.mouse[1] * 6}px, 0)`,
                  visibility: visible ? "visible" : "hidden",
                }}
              >
                <div className={`pointer-events-auto max-w-3xl bg-bone/85 p-5 sm:p-7 ${i % 2 ? "col-span-12 sm:col-span-6 sm:col-start-7" : "hang sm:col-span-6"}`}>
                  <p className="mono-label">{c.kicker}</p>
                  <div className="mt-3">{c.body}</div>
                </div>
              </section>
            );
          })}
          <p className="scroll-hint mono-label absolute bottom-4 left-1/2 -translate-x-1/2" aria-hidden="true">
            {chapter < CHAPTERS - 1 ? "scroll" : ""}
          </p>
        </div>
      </div>
    </ActiveChapter.Provider>
  );
}
