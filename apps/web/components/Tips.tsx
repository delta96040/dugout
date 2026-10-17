"use client";

import { useEffect } from "react";

/** Keep an open card inside the viewport: a number near the right edge of a phone would push its card off screen. */
export function place(tip: Element | null | undefined) {
  const card = tip?.querySelector<HTMLElement>(":scope > .tip-card");
  if (!card) return;
  card.style.translate = "";
  card.style.top = card.style.bottom = card.style.marginTop = card.style.marginBottom = "";
  // measured at once: the card is already shown (hover, focus or data-open), and reading its box forces layout
  let r = card.getBoundingClientRect();
  if (!r.width) return; // not shown
  const pad = 8;
  // no room below (a number near the bottom of the screen, or of a fixed story panel): open it upwards
  if (r.bottom > innerHeight - pad && r.top - r.height - 24 > pad) {
    Object.assign(card.style, { top: "auto", bottom: "100%", marginTop: "0", marginBottom: "0.5rem" });
    r = card.getBoundingClientRect();
  }
  let dx = 0;
  if (r.right > innerWidth - pad) dx = innerWidth - pad - r.right;
  if (r.left + dx < pad) dx = pad - r.left;
  card.style.translate = dx ? `${Math.round(dx)}px 0` : "";
}

/** One page-wide listener for the CSS provenance cards: tap toggles a card, tapping elsewhere or Escape closes it. */
export default function Tips() {
  useEffect(() => {
    const tips = () => document.querySelectorAll<HTMLElement>(".tip[data-open], .tip[data-closed]");
    const onClick = (e: MouseEvent) => {
      const btn = (e.target as Element).closest(".tip > button");
      const tip = btn?.parentElement;
      for (const t of tips()) if (t !== tip) t.removeAttribute("data-open");
      if (tip) tip.toggleAttribute("data-open");
      place(tip);
    };
    const onEnter = (e: Event) => place((e.target as Element).closest?.(".tip"));
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      for (const t of document.querySelectorAll<HTMLElement>(".tip:hover, .tip:focus-within, .tip[data-open]")) {
        t.removeAttribute("data-open");
        t.setAttribute("data-closed", "");
      }
    };
    // reopenable once the pointer or focus has really left the number (not just moved onto its card)
    const onLeave = (e: MouseEvent | FocusEvent) => {
      const tip = (e.target as Element).closest?.(".tip");
      if (tip && !tip.contains(e.relatedTarget as Node | null)) tip.removeAttribute("data-closed");
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mouseout", onLeave);
    document.addEventListener("focusout", onLeave);
    document.addEventListener("mouseover", onEnter);
    document.addEventListener("focusin", onEnter);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mouseout", onLeave);
      document.removeEventListener("focusout", onLeave);
      document.removeEventListener("mouseover", onEnter);
      document.removeEventListener("focusin", onEnter);
    };
  }, []);
  return null;
}
