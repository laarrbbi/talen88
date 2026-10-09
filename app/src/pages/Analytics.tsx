import { useEffect, useState } from "react";
import {
  api, AnalyticsFilters, Turnover, Drivers, Managers, Fairness, Cost,
  Compensation, Engagement, Forecast, HistBin, SurvivalPoint,
} from "../api/client";
import { SkeletonChartCard, SkeletonKpis } from "../components/ui";
import { NameTag } from "../components/NameTag";
import { Icon } from "../components/icons";
import { useAuth } from "../App";
import { usd, titleCase } from "../lib/format";

/**
 * ANALYTICS — deeper, exploratory views over the GOLD layer, a sibling of the Dashboard.
 *
 * Every panel is fed by the scoped + audited /analytics/* endpoints (token-only aggregates
 * with server-side minimum-segment suppression). Each response carries a `data_status` so a
 * gated view renders a friendly "connect a source / appears as data accrues" card instead of
 * a broken chart — the data-maturity ladder, made visible. Charts are hand-coded SVG in the
 * same style as the Dashboard's Risk × Value matrix (no charting dependency).
 */

const TABS = [
  ["turnover", "Turnover & Retention", "users"],
  ["drivers", "Risk Drivers", "alert"],
  ["compensation", "Compensation", "comp"],
  ["engagement", "Engagement", "pulse"],
  ["managers", "Managers & Teams", "graph"],
  ["fairness", "Fairness & Bias Audit", "shield"],
  ["cost", "Cost & Scenario", "target"],
  ["forecast", "Forecast", "path"],
] as const;
type TabKey = (typeof TABS)[number][0];

const TENURE_BANDS = ["<2", "2-5", "5-10", "10+"];
const TIME_RANGES: [string, string][] = [
  ["", "All time"],
  ["2026-01-01:2026-06-30", "2026 YTD"],
  ["2026-04-01:2026-06-30", "Last quarter"],
];

// Loading state shared by every tab — optional KPI row + the two-up panel grid
// + a wide panel, so the tab reserves its real shape while the endpoint responds.
function TabSkeleton({ kpis = 0, grid = true, wide = true }: { kpis?: number; grid?: boolean; wide?: boolean }) {
  return (
    <>
      {kpis > 0 && <SkeletonKpis n={kpis} />}
      {grid && (
        <div className="an-grid2">
          <SkeletonChartCard h={208} />
          <SkeletonChartCard h={208} />
        </div>
      )}
      {wide && <SkeletonChartCard h={grid ? 150 : 220} />}
    </>
  );
}

export function Analytics() {
  const { me } = useAuth();
  const [tab, setTab] = useState<TabKey>("turnover");
  const [filters, setFilters] = useState<AnalyticsFilters>({});
  const [divisions, setDivisions] = useState<string[]>([]);

  useEffect(() => {
    api.divisions().then((d) => setDivisions(d.map((x) => x.name))).catch(() => setDivisions([]));
  }, []);

  const asOf = new Date().toLocaleDateString("en-US", { day: "numeric", month: "short" });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Workforce Analytics</div>
          <div className="page-sub">
            <span className="an-mono">as of {asOf}</span>
            <span className="dot-sep" />
            <span>{me.division ? me.division : "Enterprise · All divisions"}</span>
            <span className="dot-sep" />
            <span>Token-only aggregates · scoped &amp; audited</span>
          </div>
        </div>
      </div>

      <FilterBar filters={filters} setFilters={setFilters} divisions={divisions} />

      <div className="an-tabs" role="tablist">
        {TABS.map(([key, label, ic]) => (
          <button key={key} role="tab" aria-selected={tab === key}
                  className={`an-tab${tab === key ? " active" : ""}`} onClick={() => setTab(key as TabKey)}>
            <Icon name={ic} size={14} /> {label}
          </button>
        ))}
      </div>

      <div className="an-body">
        {tab === "turnover" && <TurnoverTab filters={filters} />}
        {tab === "drivers" && <DriversTab filters={filters} />}
        {tab === "managers" && <ManagersTab filters={filters} />}
        {tab === "fairness" && <FairnessTab />}
        {tab === "cost" && <CostTab filters={filters} />}
        {tab === "compensation" && <CompensationTab filters={filters} />}
        {tab === "engagement" && <EngagementTab filters={filters} />}
        {tab === "forecast" && <ForecastTab filters={filters} />}
      </div>

      <AskAnalytics filters={filters} />
    </div>
  );
}

// ---- Ask analytics ----------------------------------------------------------
// A thin natural-language box that reuses the EXISTING Agents dispatcher (api.chat).
// No new agent: the question is routed server-side and scoped to the current division
// filter. Names never leave the browser — only tokens flow, rendered via <NameTag>.
function AskAnalytics({ filters }: { filters: AnalyticsFilters }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<{ answer: string; matched: { token: string }[]; note: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const ask = () => {
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setErr(null);
    api.chat({ question, division: filters.division || undefined })
      .then((r) => setReply({ answer: r.answer, matched: r.matched, note: r.note }))
      .catch(() => setErr("Couldn't reach the analytics agent. Try again."))
      .finally(() => setBusy(false));
  };

  return (
    <div className="card an-ask">
      <div className="an-ask-head">
        <Icon name="spark2" size={15} />
        <span>Ask analytics</span>
        <span className="an-ask-sub">routed through the audited agent · token-only</span>
      </div>
      <div className="an-ask-row">
        <input className="an-ask-input" placeholder="e.g. which division has the highest flight risk?"
               value={q} disabled={busy}
               onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") ask(); }} />
        <button className="btn primary" onClick={ask} disabled={busy || !q.trim()}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </div>
      {err && <div className="an-ask-err">{err}</div>}
      {reply && (
        <div className="an-ask-reply">
          <div className="an-ask-answer">{reply.answer}</div>
          {reply.matched.length > 0 && (
            <div className="an-ask-people">
              {reply.matched.slice(0, 12).map((m) => (
                <a key={m.token} href={`#/profile/${m.token}`} className="an-ask-chip">
                  <NameTag token={m.token} />
                </a>
              ))}
              {reply.matched.length > 12 && <span className="an-ask-more">+ {reply.matched.length - 12} more</span>}
            </div>
          )}
          {reply.note && <div className="an-ask-note">{reply.note}</div>}
        </div>
      )}
    </div>
  );
}

// ---- filter bar -------------------------------------------------------------
function FilterBar({ filters, setFilters, divisions }:
  { filters: AnalyticsFilters; setFilters: (f: AnalyticsFilters) => void; divisions: string[] }) {
  const set = (k: keyof AnalyticsFilters, v: string) =>
    setFilters({ ...filters, [k]: v === "" ? undefined : (k === "level" ? Number(v) : v) });
  return (
    <div className="an-filters">
      <select className="chip-filter" value={filters.division ?? ""} onChange={(e) => set("division", e.target.value)}>
        <option value="">All divisions</option>
        {divisions.map((d) => <option key={d} value={d}>{titleCase(d)}</option>)}
      </select>
      <select className="chip-filter" value={filters.level ?? ""} onChange={(e) => set("level", e.target.value)}>
        <option value="">All levels</option>
        {[1, 2, 3, 4, 5, 6, 7, 8].map((l) => <option key={l} value={l}>Level {l}</option>)}
      </select>
      <select className="chip-filter" value={filters.tenure_band ?? ""} onChange={(e) => set("tenure_band", e.target.value)}>
        <option value="">Any tenure</option>
        {TENURE_BANDS.map((b) => <option key={b} value={b}>{b} yrs</option>)}
      </select>
      <input className="chip-filter an-loc" placeholder="Location" value={filters.location ?? ""}
             onChange={(e) => set("location", e.target.value)} />
      <select className="chip-filter" value={filters.date_range ?? ""} onChange={(e) => set("date_range", e.target.value)}>
        {TIME_RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

// ============================================================================
// Shared presentational pieces — KPI, card frame, locked state, CSV, charts.
// ============================================================================
function Kpi({ icon, label, value, note, color }:
  { icon: string; label: string; value: string | number; note: string; color?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label"><Icon name={icon} size={13} /> {label}</div>
      <div className="kpi-val tnum" style={{ color }}>{value}</div>
      <div className="kpi-foot"><span className="kpi-note">{note}</span></div>
    </div>
  );
}

function Panel({ title, icon, ann, children, right }:
  { title: string; icon: string; ann?: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title"><Icon name={icon} size={16} style={{ color: "var(--text-3)" }} /> {title}
          {ann && <span className="ann">{ann}</span>}</div>
        {right}
      </div>
      <div className="card-body">{children}</div>
    </div>
  );
}

export function LockedCard({ title, status }: { title: string; status: string }) {
  const insufficient = status.startsWith("insufficient");
  const source = status.startsWith("needs_source") ? status.split(":")[1] : null;
  const field = status.startsWith("needs_field") ? status.split(":")[1] : null;
  return (
    <div className="card an-locked">
      <div className="an-locked-ico"><Icon name="lock" size={20} /></div>
      <div className="an-locked-title">{title}</div>
      <div className="an-locked-msg">
        {insufficient && "Trends appear here as monthly snapshots accrue."}
        {source && <>Connect a <b>{titleCase(source)}</b> source to unlock this view.</>}
        {field && <>Awaiting the <b>{field}</b> field — showing the available fallback meanwhile.</>}
        {!insufficient && !source && !field && "Not available for the current scope."}
      </div>
      <div className="an-locked-badge">{status}</div>
    </div>
  );
}

function csvExport(name: string, rows: readonly object[]) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const body = [cols.join(","), ...rows.map((r) =>
    cols.map((c) => esc((r as Record<string, unknown>)[c])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = `analytics_${name}.csv`; a.click();
  URL.revokeObjectURL(url);
}

function ExportBtn({ name, rows }: { name: string; rows: readonly object[] }) {
  return (
    <button className="btn ghost" style={{ height: 28, padding: "0 9px", fontSize: 12 }}
            onClick={() => csvExport(name, rows)} disabled={!rows.length}>
      <Icon name="download" size={13} /> CSV
    </button>
  );
}

// ---- hand-SVG charts (Dashboard style: margin object + x()/y() transforms) --
const C_HIGH = "var(--risk-high)", C_MED = "var(--risk-med)", C_LOW = "var(--risk-low)", C_ACCENT = "var(--accent)";

function HBars({ rows, max, color }:
  { rows: { label: React.ReactNode; value: number; display: string; href?: string }[]; max: number; color?: string }) {
  const top = Math.max(max, 1);
  return (
    <div className="bars">
      {rows.map((r, i) => {
        const inner = (
          <>
            <div className="bl">{r.label}</div>
            <div className="bt"><div className="seg-h" style={{ width: `${(r.value / top) * 100}%`, background: color ?? C_HIGH }} /></div>
            <div className="bv tnum">{r.display}</div>
          </>
        );
        return r.href
          ? <a key={i} href={r.href} className="bar-row" style={{ cursor: "pointer" }}>{inner}</a>
          : <div key={i} className="bar-row">{inner}</div>;
      })}
      {!rows.length && <div className="subtle">No segments in scope (small groups suppressed).</div>}
    </div>
  );
}

function Histogram({ bins, fmt, color }: { bins: HistBin[]; fmt?: (n: number) => string; color?: string }) {
  const W = 520, H = 200, m = { t: 12, r: 12, b: 34, l: 30 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const maxC = Math.max(1, ...bins.map((b) => b.count));
  const bw = iw / Math.max(1, bins.length);
  const f = fmt ?? ((n: number) => String(n));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="distribution histogram">
      {[0, 0.5, 1].map((g) => {
        const yy = m.t + ih - g * ih;
        return <line key={g} x1={m.l} y1={yy} x2={m.l + iw} y2={yy} stroke="var(--grid)" />;
      })}
      {bins.map((b, i) => {
        const h = (b.count / maxC) * ih;
        return (
          <g key={i}>
            <rect x={m.l + i * bw + 3} y={m.t + ih - h} width={bw - 6} height={h}
                  fill={color ?? C_ACCENT} fillOpacity={0.75} rx={2}><title>{`${f(b.lo)}–${f(b.hi)}: ${b.count}`}</title></rect>
            {b.count > 0 && <text x={m.l + i * bw + bw / 2} y={m.t + ih - h - 4} textAnchor="middle"
                  fontSize="9.5" fontFamily="var(--mono)" fill="var(--text-3)">{b.count}</text>}
            <text x={m.l + i * bw + bw / 2} y={m.t + ih + 14} textAnchor="middle"
                  fontSize="9" fontFamily="var(--mono)" fill="var(--text-4)">{f(b.lo)}</text>
          </g>
        );
      })}
      <line x1={m.l} y1={m.t + ih} x2={m.l + iw} y2={m.t + ih} stroke="var(--border)" />
    </svg>
  );
}

function SurvivalCurve({ points }: { points: SurvivalPoint[] }) {
  const W = 520, H = 220, m = { t: 14, r: 14, b: 34, l: 38 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const maxT = Math.max(1, ...points.map((p) => p.t));
  const x = (t: number) => m.l + (t / maxT) * iw;
  const y = (s: number) => m.t + ih - s * ih;
  // Step path (Kaplan-Meier is a step function).
  let d = "";
  points.forEach((p, i) => {
    if (i === 0) { d = `M ${x(p.t)} ${y(p.survival)}`; return; }
    d += ` H ${x(p.t)} V ${y(p.survival)}`;
  });
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="survival curve">
      {[0, 0.25, 0.5, 0.75, 1].map((g) => (
        <g key={g}>
          <line x1={m.l} y1={y(g)} x2={m.l + iw} y2={y(g)} stroke="var(--grid)" />
          <text x={m.l - 6} y={y(g) + 3} textAnchor="end" fontSize="9" fontFamily="var(--mono)" fill="var(--text-4)">{g.toFixed(2)}</text>
        </g>
      ))}
      <path d={d} fill="none" stroke={C_ACCENT} strokeWidth={2} />
      <text x={m.l + iw / 2} y={H - 4} textAnchor="middle" fontSize="10" fontWeight="600" fill="var(--text-3)">TENURE (YEARS) →</text>
    </svg>
  );
}

// ============================================================================
// Tab 1 — Turnover & Retention  ✅
// ============================================================================
function TurnoverTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Turnover | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsTurnover(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load turnover analytics.</div>;
  if (!d) return <TabSkeleton kpis={4} />;
  const h = d.headline;
  return (
    <>
      <div className="kpi-row an-kpi4">
        <Kpi icon="users" label="Headcount" value={h.headcount} note="employees in scope" />
        <Kpi icon="alert" label="Attrition" value={`${(h.attrition_rate * 100).toFixed(1)}%`} note={`${h.leavers} leavers`} color="var(--risk-high)" />
        <Kpi icon="arrowdown" label="Voluntary" value={h.voluntary} note={`${h.involuntary} involuntary`} />
        <Kpi icon="award" label="Regretted" value={h.regretted} note="regretted exits" color="var(--risk-high)" />
      </div>
      <div className="an-grid2">
        <Panel title="Attrition by Division" icon="grid" ann="rate"
               right={<ExportBtn name="turnover_segments" rows={d.by_segment} />}>
          <HBars max={Math.max(...d.by_segment.map((s) => s.rate), 0.01)}
                 rows={d.by_segment.map((s) => ({ label: titleCase(s.segment), value: s.rate,
                   display: `${(s.rate * 100).toFixed(1)}%`, href: "#/watchlist" }))} />
          {d.suppressed_segments > 0 && <div className="an-suppress"><Icon name="lock" size={12} /> {d.suppressed_segments} small segment(s) suppressed (&lt; 5)</div>}
        </Panel>
        <Panel title="Retention / Survival Curve" icon="pulse" ann="Kaplan–Meier">
          {d.survival.length > 1
            ? <SurvivalCurve points={d.survival} />
            : <div className="subtle">Not enough tenure data to plot a survival curve.</div>}
        </Panel>
      </div>
      <Panel title="Attrition Trend" icon="path" ann="monthly">
        {d.trend.data_status === "ok"
          ? <HBars max={Math.max(...d.trend.points.map((p) => p.leavers), 1)}
                   rows={d.trend.points.map((p) => ({ label: p.month, value: p.leavers, display: String(p.leavers) }))} color={C_MED} />
          : <LockedCard title="Attrition trend" status={d.trend.data_status} />}
      </Panel>
    </>
  );
}

// ============================================================================
// Tab 2 — Risk Drivers  ✅
// ============================================================================
function DriversTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Drivers | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsDrivers(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load risk drivers.</div>;
  if (!d) return <TabSkeleton />;
  const rv = d.risk_vs_attrition;
  const maxW = Math.max(...d.top_drivers.map((t) => t.mean_weight), 0.01);
  return (
    <>
      <div className="an-grid2">
        <Panel title="Top Risk Drivers" icon="alert" ann="mean weight"
               right={<ExportBtn name="drivers" rows={d.top_drivers} />}>
          <div className="bars">
            {d.top_drivers.map((t, i) => (
              <div key={i} className="bar-row">
                <div className="bl" title={t.label}>{titleCase(t.label)}</div>
                <div className="bt"><div className="seg-h" style={{ width: `${(t.mean_weight / maxW) * 100}%`,
                     background: t.direction === "increases" ? C_HIGH : C_LOW }} /></div>
                <div className="bv tnum">{t.mean_weight.toFixed(2)}</div>
              </div>
            ))}
            {!d.top_drivers.length && <div className="subtle">No drivers in scope.</div>}
          </div>
        </Panel>
        <Panel title="Flight-Risk Distribution" icon="pulse" ann="0–100">
          <Histogram bins={d.distribution} />
          <div className="an-statrow">
            <div><span className="an-stat-k">Leavers' mean risk</span><span className="an-stat-v" style={{ color: "var(--risk-high)" }}>{rv.leaver_mean_flight_risk ?? "—"}</span></div>
            <div><span className="an-stat-k">Stayers' mean risk</span><span className="an-stat-v">{rv.stayer_mean_flight_risk ?? "—"}</span></div>
          </div>
        </Panel>
      </div>
    </>
  );
}

// ============================================================================
// Tab 5 — Managers & Teams  ✅
// ============================================================================
function ManagersTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Managers | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsManagers(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load manager analytics.</div>;
  if (!d) return <TabSkeleton />;
  return (
    <>
      <div className="an-grid2">
        <Panel title="Team Turnover by Manager" icon="users" ann="rate"
               right={<ExportBtn name="managers" rows={d.by_manager} />}>
          <div className="bars">
            {d.by_manager.map((mr) => (
              <a key={mr.manager_token} href={`#/profile/${mr.manager_token}`} className="bar-row" style={{ cursor: "pointer" }}>
                <div className="bl"><NameTag token={mr.manager_token} /></div>
                <div className="bt"><div className="seg-h" style={{ width: `${Math.min(100, (mr.team_turnover_rate ?? 0) * 100)}%` }} /></div>
                <div className="bv tnum">{((mr.team_turnover_rate ?? 0) * 100).toFixed(0)}%</div>
              </a>
            ))}
            {!d.by_manager.length && <div className="subtle">No managers in scope (small teams suppressed).</div>}
          </div>
          {d.suppressed_managers > 0 && <div className="an-suppress"><Icon name="lock" size={12} /> {d.suppressed_managers} small team(s) suppressed (span &lt; 5)</div>}
        </Panel>
        <Panel title="Span of Control" icon="grid" ann="distribution">
          <Histogram bins={d.span_distribution} color={C_ACCENT} />
        </Panel>
      </div>
      <Panel title="Resignation Contagion" icon="alert" ann="peers departed (3mo)">
        <div className="bars">
          {d.contagion.map((c) => (
            <a key={c.manager_token} href={`#/profile/${c.manager_token}`} className="bar-row" style={{ cursor: "pointer" }}>
              <div className="bl"><NameTag token={c.manager_token} /></div>
              <div className="bt"><div className="seg-h" style={{ width: `${Math.min(100, (c.peers_departed_recently ?? 0) * 20)}%`, background: C_MED }} /></div>
              <div className="bv tnum">{c.peers_departed_recently}</div>
            </a>
          ))}
          {!d.contagion.length && <div className="subtle">No recent peer-departure clusters in scope.</div>}
        </div>
      </Panel>
    </>
  );
}

// ============================================================================
// Tab 6 — Fairness & Bias Audit  ✅ (admin-only)
// ============================================================================
function FairnessTab() {
  const [metric, setMetric] = useState("flight_risk");
  const [d, setD] = useState<Fairness | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsFairness(metric).then(setD).catch(() => setErr(true)); }, [metric]);
  if (err) return <div className="flash err">Could not load fairness audit.</div>;
  if (!d) return <TabSkeleton />;
  if (d.restricted) return <LockedCard title="Fairness & Bias Audit" status="needs_source:admin_access" />;
  return (
    <>
      <div className="an-fairhead">
        <div className="seg">
          <button className={metric === "flight_risk" ? "on" : ""} onClick={() => setMetric("flight_risk")}>Flight risk</button>
          <button className={metric === "value_score" ? "on" : ""} onClick={() => setMetric("value_score")}>Value score</button>
        </div>
        <div className="subtle">Adverse-impact ratio uses the 4/5ths rule · cohorts &lt; 5 suppressed · walled traits, audit-only</div>
      </div>
      <div className="an-grid2">
        {d.traits.map((t) => (
          <Panel key={t.trait} title={titleCase(t.trait)} icon="shield"
                 ann={t.adverse_impact_ratio !== null ? `4/5ths ${t.four_fifths_pass ? "pass" : "FLAG"}` : undefined}>
            <div className="bars">
              {t.cohorts.map((c) => (
                <div key={c.cohort} className="bar-row">
                  <div className="bl">{titleCase(String(c.cohort))} <span className="an-cohort-n">n={c.count}</span></div>
                  <div className="bt"><div className="seg-h" style={{ width: `${c.mean ?? 0}%`, background: C_ACCENT }} /></div>
                  <div className="bv tnum">{c.mean ?? "—"}</div>
                </div>
              ))}
              {!t.cohorts.length && <div className="subtle">No cohort large enough to report.</div>}
            </div>
            {t.adverse_impact_ratio !== null && (
              <div className="an-air" style={{ color: t.four_fifths_pass ? "var(--risk-low)" : "var(--risk-high)" }}>
                adverse-impact ratio {t.adverse_impact_ratio.toFixed(2)}
              </div>
            )}
          </Panel>
        ))}
      </div>
    </>
  );
}

// ============================================================================
// Tab 7 — Cost & Scenario  ✅ (intervention-ROI locked)
// ============================================================================
function CostTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Cost | null>(null);
  const [err, setErr] = useState(false);
  const [n, setN] = useState(10);
  useEffect(() => { setD(null); setErr(false); api.analyticsCost(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load cost analytics.</div>;
  if (!d) return <TabSkeleton kpis={3} />;
  const scenarioSlice = d.scenario.slice(0, n);
  const scenarioSum = scenarioSlice.reduce((a, b) => a + b.cost_at_risk, 0);
  return (
    <>
      <div className="kpi-row an-kpi3">
        <Kpi icon="comp" label="$ at risk" value={usd(d.total_at_risk)} note="Σ cost-to-lose × flight risk" color="var(--risk-high)" />
        <Kpi icon="alert" label="Top cohort" value={d.by_division[0] ? titleCase(d.by_division[0].segment) : "—"}
             note={d.by_division[0] ? usd(d.by_division[0].cost_at_risk) : "no segments"} />
        <Kpi icon="users" label="Scenario" value={usd(scenarioSum)} note={`if top ${scenarioSlice.length} leave`} />
      </div>
      <div className="an-grid2">
        <Panel title="Cost at Risk by Division" icon="grid" ann="USD"
               right={<ExportBtn name="cost_by_division" rows={d.by_division} />}>
          <HBars max={Math.max(...d.by_division.map((s) => s.cost_at_risk), 1)}
                 rows={d.by_division.map((s) => ({ label: titleCase(s.segment), value: s.cost_at_risk, display: usd(s.cost_at_risk) }))} />
        </Panel>
        <Panel title="Cost-to-Lose Distribution" icon="comp" ann="USD">
          <Histogram bins={d.distribution} fmt={(v) => v >= 1000 ? `${Math.round(v / 1000)}k` : String(v)} color={C_HIGH} />
        </Panel>
      </div>
      <Panel title={`Lose-Scenario — top ${scenarioSlice.length}`} icon="target"
             right={<div className="row" style={{ gap: 8 }}>
               <select className="chip-filter" value={n} onChange={(e) => setN(Number(e.target.value))}>
                 {[5, 10, 20].map((x) => <option key={x} value={x}>Top {x}</option>)}
               </select>
               <ExportBtn name="cost_scenario" rows={scenarioSlice} />
             </div>}>
        <div className="bars">
          {scenarioSlice.map((s) => (
            <a key={s.token} href={`#/profile/${s.token}`} className="bar-row" style={{ cursor: "pointer" }}>
              <div className="bl"><NameTag token={s.token} /> <span className="an-cohort-n">risk {s.flight_risk}</span></div>
              <div className="bt"><div className="seg-h" style={{ width: `${(s.cost_at_risk / (scenarioSlice[0]?.cost_at_risk || 1)) * 100}%` }} /></div>
              <div className="bv tnum">{usd(s.cost_at_risk)}</div>
            </a>
          ))}
        </div>
      </Panel>
      <Panel title="Intervention ROI" icon="path" ann="spend → save">
        <LockedCard title="ROI of a retention spend" status={d.intervention_roi.data_status} />
      </Panel>
    </>
  );
}

// ============================================================================
// Tab 3 — Compensation & Pay Equity  ✅ / ⚠️ market / 🔧 compa-ratio
//   Pay distribution + percentile render live; comp-vs-market is source-gated
//   (live only when the benchmark feed has rows for the cohort), compa-ratio is
//   capability-gated and falls back to the percentile.
// ============================================================================
function CompensationTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Compensation | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsCompensation(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load compensation analytics.</div>;
  if (!d) return <TabSkeleton />;
  if (d.data_status !== "ok") return <LockedCard title="Compensation" status={d.data_status} />;
  const maxMedian = Math.max(...d.by_level.map((b) => b.box?.median ?? 0), 1);
  return (
    <>
      <div className="an-grid2">
        <Panel title="Pay by Level" icon="comp" ann="median base · USD"
               right={<ExportBtn name="comp_by_level" rows={d.by_level.map((b) => ({ segment: b.segment, headcount: b.headcount, ...(b.box ?? {}) }))} />}>
          <div className="bars">
            {d.by_level.map((b) => (
              <div key={b.segment} className="bar-row">
                <div className="bl">{b.segment} <span className="an-cohort-n">n={b.headcount}</span></div>
                <div className="bt"><div className="seg-h" style={{ width: `${((b.box?.median ?? 0) / maxMedian) * 100}%`, background: C_ACCENT }} /></div>
                <div className="bv tnum">{b.box ? usd(b.box.median) : "—"}</div>
              </div>
            ))}
            {!d.by_level.length && <div className="subtle">No level large enough to report.</div>}
          </div>
        </Panel>
        <Panel title="Pay Percentile in Role" icon="pulse" ann="distribution">
          <Histogram bins={d.percentile_distribution} color={C_ACCENT} />
        </Panel>
      </div>
      <div className="an-grid2">
        <Panel title="Comp vs Market Gap" icon="globe" ann="licensed benchmark">
          {d.market.data_status === "ok"
            ? <div className="an-bigstat">
                <div className="an-bigstat-v" style={{ color: (d.market.mean_gap_vs_market ?? 0) < 0 ? "var(--risk-high)" : "var(--risk-low)" }}>
                  {d.market.mean_gap_vs_market !== null && d.market.mean_gap_vs_market !== undefined ? `${(d.market.mean_gap_vs_market * 100).toFixed(1)}%` : "—"}
                </div>
                <div className="subtle">mean gap vs market · {d.market.n} benchmarked</div>
              </div>
            : <LockedCard title="Comp vs market" status={d.market.data_status} />}
        </Panel>
        <Panel title="Compa-Ratio" icon="target" ann="salary ÷ band-midpoint">
          <LockedCard title="Compa-ratio" status={d.compa_ratio.data_status} />
        </Panel>
      </div>
    </>
  );
}

// ============================================================================
// Tab 4 — Engagement  ⚠️ (source-gated on a survey feed)
// ============================================================================
function EngagementTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Engagement | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsEngagement(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load engagement analytics.</div>;
  if (!d) return <TabSkeleton kpis={3} grid={false} />;
  if (d.data_status !== "ok") return <LockedCard title="Engagement" status={d.data_status} />;
  const h = d.headline;
  return (
    <>
      <div className="kpi-row an-kpi3">
        <Kpi icon="pulse" label="Engagement" value={h.engagement_score ?? "—"} note="latest score" />
        <Kpi icon="award" label="eNPS" value={h.enps_score ?? "—"} note="−100…100" />
        <Kpi icon="users" label="Response rate" value={h.response_rate !== null && h.response_rate !== undefined ? `${(h.response_rate * 100).toFixed(0)}%` : "—"} note="survey participation" />
      </div>
      <Panel title="Engagement & eNPS Trend" icon="path" ann="monthly">
        {d.trend.data_status === "ok"
          ? <Trend points={d.trend.points} />
          : <LockedCard title="Engagement trend" status={d.trend.data_status} />}
      </Panel>
    </>
  );
}

// Dual-line trend (engagement + eNPS) — hand SVG, Dashboard style.
function Trend({ points }: { points: { month: string; engagement_score: number | null; enps_score: number | null }[] }) {
  const W = 560, H = 220, m = { t: 16, r: 16, b: 34, l: 34 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (i: number) => m.l + (points.length <= 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const y = (v: number) => m.t + ih - (v / 100) * ih;
  const line = (key: "engagement_score" | "enps_score") => points.map((p, i) => {
    const v = p[key]; if (v === null) return null;
    // eNPS is −100..100; normalize to 0..100 for the shared axis.
    const nv = key === "enps_score" ? (v + 100) / 2 : v;
    return `${i === 0 ? "M" : "L"} ${x(i)} ${y(nv)}`;
  }).filter(Boolean).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="engagement trend">
      {[0, 0.5, 1].map((g) => <line key={g} x1={m.l} y1={m.t + ih - g * ih} x2={m.l + iw} y2={m.t + ih - g * ih} stroke="var(--grid)" />)}
      <path d={line("engagement_score")} fill="none" stroke={C_ACCENT} strokeWidth={2} />
      <path d={line("enps_score")} fill="none" stroke={C_MED} strokeWidth={2} strokeDasharray="4 3" />
      {points.map((p, i) => <text key={i} x={x(i)} y={m.t + ih + 16} textAnchor="middle" fontSize="9" fontFamily="var(--mono)" fill="var(--text-4)">{p.month.slice(5)}</text>)}
      <g fontFamily="var(--mono)" fontSize="9.5">
        <rect x={m.l} y={2} width={9} height={9} fill={C_ACCENT} /><text x={m.l + 13} y={10} fill="var(--text-3)">engagement</text>
        <rect x={m.l + 92} y={2} width={9} height={9} fill={C_MED} /><text x={m.l + 105} y={10} fill="var(--text-3)">eNPS</text>
      </g>
    </svg>
  );
}

// ============================================================================
// Tab 8 — Forecast  🔧 (basic expected-leavers ships labeled an estimate;
//   seasonal forecast stays insufficient_history)
// ============================================================================
function ForecastTab({ filters }: { filters: AnalyticsFilters }) {
  const [d, setD] = useState<Forecast | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { setD(null); setErr(false); api.analyticsForecast(filters).then(setD).catch(() => setErr(true)); }, [JSON.stringify(filters)]);
  if (err) return <div className="flash err">Could not load forecast.</div>;
  if (!d) return <TabSkeleton kpis={3} />;
  return (
    <>
      <div className="kpi-row an-kpi3">
        <Kpi icon="path" label="Expected leavers" value={d.expected_leavers.value} note="Σ flight-risk probability · estimate" color="var(--risk-high)" />
        <Kpi icon="users" label="Top division" value={d.by_division[0] ? titleCase(d.by_division[0].segment) : "—"}
             note={d.by_division[0] ? `${d.by_division[0].expected_leavers} expected` : "no segments"} />
        <Kpi icon="grid" label="Segments" value={d.by_division.length} note="divisions in scope" />
      </div>
      <div className="an-est-note"><Icon name="alert" size={13} /> Expected-leavers is a point estimate (sum of current flight-risk probabilities), not a seasonal forecast.</div>
      <div className="an-grid2">
        <Panel title="Projected Attrition by Division" icon="grid" ann="expected leavers"
               right={<ExportBtn name="forecast_by_division" rows={d.by_division} />}>
          <HBars max={Math.max(...d.by_division.map((s) => s.expected_leavers), 1)}
                 rows={d.by_division.map((s) => ({ label: titleCase(s.segment), value: s.expected_leavers, display: String(s.expected_leavers) }))} color={C_MED} />
        </Panel>
        <Panel title="Seasonal Forecast" icon="path" ann="time-series">
          <LockedCard title="Seasonal forecast" status={d.seasonal.data_status} />
        </Panel>
      </div>
    </>
  );
}
