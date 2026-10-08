import { CSSProperties, ReactNode, useEffect, useRef, useState } from "react";
import { ReasonCode } from "../api/client";
import { riskBand, riskColor, titleCase } from "../lib/format";

function useCountUp(target: number, duration = 700): number {
  const [val, setVal] = useState(0);
  const frame = useRef<number>();
  useEffect(() => {
    if (target === 0) return;
    let start: number | null = null;
    const step = (ts: number) => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / duration, 1);
      const eased = p < 0.5 ? 2 * p * p : -1 + (4 - 2 * p) * p;
      setVal(Math.round(eased * target));
      if (p < 1) frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
    return () => { if (frame.current) cancelAnimationFrame(frame.current); };
  }, [target, duration]);
  return val;
}

export function AnimNum({ value, duration = 700 }: { value: number; duration?: number }) {
  const n = useCountUp(value, duration);
  return <>{n}</>;
}

export function RiskBadge({ score, label }: { score: number | null | undefined; label?: boolean }) {
  const band = riskBand(score);
  if (band === "none") return <span className="code">no score</span>;
  const text = band === "med" ? "Medium" : band === "high" ? "High" : "Low";
  return (
    <span className={`band ${band}`}>
      <span className="bdot" />
      {label ? `${text} risk` : <>{score} · {text.toLowerCase()}</>}
    </span>
  );
}

export function ScoreBar({ score, label }: { score: number | null; label: string }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row between" style={{ marginBottom: 4 }}>
        <span>{titleCase(label)}</span>
        <span style={{ fontWeight: 600, color: score == null ? "var(--text-dim)" : riskColor(score) }}>
          {score == null ? "—" : score}
        </span>
      </div>
      <div className="bar">
        <span style={{ width: `${score ?? 0}%`, background: score == null ? "var(--border)" : riskColor(score) }} />
      </div>
    </div>
  );
}

export function ReasonCodes({ codes }: { codes: ReasonCode[] }) {
  if (!codes.length) return <p className="muted">No reason codes recorded.</p>;
  return (
    <div>
      {codes.map((c, i) => (
        <div className="reason" key={i}>
          <span>{titleCase(c.label)}</span>
          <span className={c.direction === "increases" ? "dir-inc" : "dir-dec"}>
            {c.direction === "increases" ? "▲" : "▼"} {(c.weight * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  );
}

export function Flash({ kind, children }: { kind: "err" | "ok"; children: ReactNode }) {
  return <div className={`flash ${kind}`}>{children}</div>;
}

// ============================================================================
// Skeleton loading — shimmer placeholders that reserve the real content's
// shape while data is in flight (replaces the old plain "Loading…" text).
// ============================================================================

/** A single shimmer block. Pass w/h as px numbers or any CSS length. */
export function Skeleton({ w, h, r, className = "", style }: {
  w?: number | string; h?: number | string; r?: number;
  className?: string; style?: CSSProperties;
}) {
  return (
    <span
      className={`sk ${className}`.trim()}
      aria-hidden="true"
      style={{ width: w, height: h, borderRadius: r, ...style }}
    />
  );
}

/** A row of KPI cards (real .kpi chrome, skeleton contents). */
export function SkeletonKpis({ n = 4, cols }: { n?: number; cols?: number }) {
  return (
    <div className="kpi-row sk-stagger" style={{ gridTemplateColumns: `repeat(${cols ?? n}, 1fr)` }}>
      {Array.from({ length: n }).map((_, i) => (
        <div className="kpi sk-kpi" key={i}>
          <Skeleton className="sk-line" w={70} h={11} />
          <Skeleton className="sk-val" />
          <Skeleton className="sk-line sm" w={100} h={9} />
        </div>
      ))}
    </div>
  );
}

/** A full table card matching .card > .tbl-wrap > .tbl. */
export function SkeletonTable({ rows = 8, cols = 6, head = true, widths }: {
  rows?: number; cols?: number; head?: boolean; widths?: (number | string)[];
}) {
  const fallback = ["42%", "55%", "48%", "40%", "58%", "46%", "52%", "44%"];
  const cellW = (i: number) => widths?.[i] ?? fallback[i % fallback.length];
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div className="tbl-wrap">
        <table className="tbl sk-tbl">
          {head && (
            <thead>
              <tr>
                {Array.from({ length: cols }).map((_, i) => (
                  <th key={i}><Skeleton className="sk-line sm" w={i === 0 ? 84 : 52} h={9} /></th>
                ))}
              </tr>
            </thead>
          )}
          <tbody className="sk-stagger">
            {Array.from({ length: rows }).map((_, r) => (
              <tr key={r}>
                {Array.from({ length: cols }).map((_, c) => (
                  <td key={c}><Skeleton className="sk-line" w={cellW(c)} /></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A card with a header strip and a chart-sized block — for SVG panels. */
export function SkeletonChartCard({ h = 220, head = true }: { h?: number; head?: boolean }) {
  return (
    <div className="card">
      {head && (
        <div className="card-head">
          <Skeleton className="sk-line" w={158} h={13} />
          <Skeleton className="sk-line sm" w={46} h={9} />
        </div>
      )}
      <div className="card-body">
        <Skeleton className="sk-chart" h={h} />
      </div>
    </div>
  );
}

/** Page-header skeleton: serif title + sub line, optional action button. */
export function SkeletonHead({ actions = false }: { actions?: boolean }) {
  return (
    <div className="page-head">
      <div>
        <Skeleton className="sk-line lg" w={236} h={25} style={{ marginBottom: 11 }} />
        <Skeleton className="sk-line" w={332} h={12} />
      </div>
      {actions && <Skeleton w={132} h={36} r={9} />}
    </div>
  );
}
