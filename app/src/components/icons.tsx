/**
 * Inline SVG icon set + logomark, ported from the design handoff
 * (design_reference/.../app-vanilla.js ICONS). Stroke icons on a 24px grid.
 * A single <path> renders all sub-strokes (multiple M-commands in one d).
 */
import { CSSProperties } from "react";

const ICONS: Record<string, string> = {
  dashboard: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  watchlist: "M3 6h18M3 12h18M3 18h12",
  profile: "M12 12a4 4 0 100-8 4 4 0 000 8zm-7 8a7 7 0 0114 0",
  division: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  report: "M14 3v5h5M9 13h6M9 17h6M7 21h10a2 2 0 002-2V8l-5-5H7a2 2 0 00-2 2v14a2 2 0 002 2z",
  settings:
    "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 13a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-2.9 1.2v.1a2 2 0 11-4 0v-.1A1.7 1.7 0 005 17l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.7 1.7 0 003 12a1.7 1.7 0 00-1.8-1H1a2 2 0 110-4h.1A1.7 1.7 0 003 5.4l-.1-.1a2 2 0 112.8-2.8l.1.1A1.7 1.7 0 008 3V3a2 2 0 114 0v.1A1.7 1.7 0 0017 5l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.4 1.9v0a1.7 1.7 0 001.6 1H23a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
  search: "M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.3-4.3",
  bell: "M18 8a6 6 0 00-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0",
  sun: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
  filter: "M22 3H2l8 9.5V19l4 2v-8.5L22 3z",
  chevdown: "M6 9l6 6 6-6",
  chevright: "M9 6l6 6-6 6",
  arrowup: "M12 19V5M5 12l7-7 7 7",
  arrowdown: "M12 5v14M5 12l7 7 7-7",
  arrowleft: "M19 12H5M12 19l-7-7 7-7",
  sort: "M8 9l4-4 4 4M8 15l4 4 4-4",
  users: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8zM23 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8",
  pulse: "M22 12h-4l-3 9L9 3l-3 9H2",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  target: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 18a6 6 0 100-12 6 6 0 000 12zM12 14a2 2 0 100-4 2 2 0 000 4z",
  alert: "M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0zM12 9v4M12 17h0",
  comp: "M12 1v22M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6",
  path: "M6 3v12a4 4 0 004 4h4M18 21a3 3 0 100-6 3 3 0 000 6zM6 3a3 3 0 100 6 3 3 0 000-6z",
  talk: "M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z",
  lock: "M5 11h14a2 2 0 012 2v7a2 2 0 01-2 2H5a2 2 0 01-2-2v-7a2 2 0 012-2zM8 11V7a4 4 0 018 0v4",
  check: "M20 6L9 17l-5-5",
  clock: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",
  grid: "M3 9h18M9 21V9M3 5a2 2 0 012-2h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2z",
  map: "M9 3L3 6v15l6-3 6 3 6-3V3l-6 3-6-3zM9 3v15M15 6v15",
  close: "M18 6L6 18M6 6l12 12",
  sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
  graph: "M5 6a2 2 0 100-4 2 2 0 000 4zM19 22a2 2 0 100-4 2 2 0 000 4zM5 18a2 2 0 100-4 2 2 0 000 4zM19 6a2 2 0 100-4 2 2 0 000 4zM5 6v8M19 6v8M7 4h10M7 20h10M7 16l10-12",
  survey: "M9 11l3 3L22 4M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11",
  send: "M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z",
  file: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8l-6-6zM14 2v6h6",
  plus: "M12 5v14M5 12h14",
  zoomin: "M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.3-4.3M11 8v6M8 11h6",
  zoomout: "M11 19a8 8 0 100-16 8 8 0 000 16zm10 2l-4.3-4.3M8 11h6",
  spark2: "M9.94 14.34A8 8 0 1118 8M12 12l9-3-3 9-2-4-4-2z",
  globe: "M12 22a10 10 0 100-20 10 10 0 000 20zM2 12h20M12 2a15 15 0 010 20 15 15 0 010-20z",
  award: "M12 15a7 7 0 100-14 7 7 0 000 14zM8.2 13.9L7 22l5-3 5 3-1.2-8.1",
  eye: "M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 15a3 3 0 100-6 3 3 0 000 6z",
  enter: "M9 10L4 15l5 5M4 15h11a5 5 0 005-5V4",
};

export function Icon({
  name, size = 18, className, style,
}: { name: string; size?: number; className?: string; style?: CSSProperties }) {
  const d = ICONS[name] ?? "";
  return (
    <svg
      viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"
      className={className} style={style} aria-hidden="true"
    >
      <path d={d} />
    </svg>
  );
}

/** Stacked T over 88 in an accent tile — the Talent88 logomark. */
export function LogoMark({ size = 34 }: { size?: number }) {
  return (
    <svg className="t88-mark" width={size} height={size} viewBox="0 0 100 100" aria-label="Talent88">
      <rect width="100" height="100" rx="24" fill="var(--accent)" />
      <rect x="23" y="20" width="54" height="11" rx="3" fill="var(--accent-fg)" />
      <rect x="44.5" y="20" width="11" height="31" rx="3" fill="var(--accent-fg)" />
      <g fill="none" stroke="var(--accent-fg)" strokeWidth="6.5">
        <circle cx="36" cy="63" r="7.4" /><circle cx="36" cy="78.5" r="9" />
        <circle cx="64" cy="63" r="7.4" /><circle cx="64" cy="78.5" r="9" />
      </g>
    </svg>
  );
}
