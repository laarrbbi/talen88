import { useEffect, useState } from "react";
import { api, Dashboard as Dash, TeamPulse } from "../api/client";
import { AnimNum, RiskBadge, SkeletonChartCard, SkeletonHead, SkeletonKpis } from "../components/ui";
import { NameTag } from "../components/NameTag";
import { Icon } from "../components/icons";
import { useAuth } from "../App";
import { riskColor, usd } from "../lib/format";

export function Dashboard() {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState(false);
  const [pulse, setPulse] = useState<TeamPulse | null>(null);
  const { me } = useAuth();

  useEffect(() => {
    api.dashboard().then(setD).catch(() => setErr(true));
    // Additive: the operational "Team Pulse" digest loads independently so a pulse
    // failure never affects the existing risk dashboard above.
    api.teamPulse(30).then(setPulse).catch(() => setPulse(null));
  }, []);

  if (err) return <div className="page"><p className="flash err">Could not load the dashboard.</p></div>;
  if (!d) return (
    <div className="page">
      <SkeletonHead />
      <SkeletonKpis n={4} />
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.55fr) minmax(0,1fr)", gap: "var(--gap)", alignItems: "start" }}>
        <SkeletonChartCard h={420} />
        <div className="stack-app">
          <SkeletonChartCard h={150} />
          <SkeletonChartCard h={188} />
        </div>
      </div>
    </div>
  );

  const h = d.headline;
  // Retention priorities: highest-stakes points (high risk + high value), token-only.
  const priorities = [...d.quadrant]
    .filter((p) => p.flight_risk >= 70 && p.value_score >= 60)
    .sort((a, b) => b.flight_risk * b.value_score - a.flight_risk * a.value_score)
    .slice(0, 6);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Workforce Risk Overview</div>
          <div className="page-sub">
            <span>{me.division ? me.division : "Enterprise · All divisions"}</span>
            <span className="dot-sep" />
            <span>Token-only aggregates · scoped &amp; audited</span>
          </div>
        </div>
      </div>

      {/* KPI cards — sparkline/Δ omitted (no trend in /dashboard; not faked) */}
      <div className="kpi-row" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <Kpi icon="users" label="Scored" value={h.total_scored} note="employees in scope" />
        <Kpi icon="alert" label="High risk" value={h.high_risk_count} note="flight-risk ≥ 70" color="var(--risk-high)" />
        <Kpi icon="award" label="Key people" value={h.key_person_count} note="high business value" />
        <Kpi icon="target" label="Act now" value={h.act_now_count} note="high risk × high value" color="var(--risk-high)" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.55fr) minmax(0,1fr)", gap: "var(--gap)", alignItems: "start" }}>
        <div className="card">
          <div className="card-head">
            <div className="card-title"><Icon name="target" size={16} style={{ color: "var(--text-3)" }} /> Risk × Value Action Matrix</div>
            <Legend dot />
          </div>
          <div className="card-body">
            <Quadrant points={d.quadrant} />
            <div className="subtle" style={{ marginTop: 4, textAlign: "center" }}>
              Dot size reflects modeled cost-to-lose (an estimate) · click a point to open the profile
            </div>
          </div>
        </div>

        <div className="stack-app">
          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="grid" size={16} style={{ color: "var(--text-3)" }} /> Risk by Division</div>
              <span className="card-title"><span className="ann">high-risk share</span></span>
            </div>
            <div className="card-body">
              <div className="bars">
                {d.by_division.map((r) => {
                  const pct = r.total ? (r.high_risk / r.total) * 100 : 0;
                  return (
                    <a key={r.division} href="#/watchlist" className="bar-row" style={{ cursor: "pointer" }}>
                      <div className="bl" title={r.division}>{r.division}</div>
                      <div className="bt" title={`${r.high_risk} high of ${r.total}`}>
                        <div className="seg-h" style={{ width: `${pct}%` }} />
                        <div className="seg-l" style={{ width: `${100 - pct}%` }} />
                      </div>
                      <div className="bv tnum">{r.high_risk}</div>
                    </a>
                  );
                })}
              </div>
              <div className="nodata" style={{ marginTop: 14 }}>
                <Icon name="alert" /> <span>Medium/low band split <b>not yet exposed</b> by the dashboard API — only total &amp; high-risk counts.</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="alert" size={16} style={{ color: "var(--risk-high)" }} /> Retention Priorities</div>
              <a className="btn ghost" href="#/watchlist" style={{ height: 28, padding: "0 8px", fontSize: 12.5 }}>
                View all <Icon name="chevright" size={13} />
              </a>
            </div>
            <div>
              {priorities.length === 0 && <div className="card-body subtle">No high-stakes employees in scope.</div>}
              {priorities.map((p, i) => (
                <a key={p.token} href={`#/profile/${p.token}`} className="pq-item">
                  <span className="pq-rank">{i + 1}</span>
                  <div className="pq-body">
                    <div className="pq-name"><NameTag token={p.token} /></div>
                    <div className="pq-meta">{p.division}{p.cost_to_lose ? ` · ~${usd(p.cost_to_lose)} to lose` : ""}</div>
                  </div>
                  <RiskBadge score={p.flight_risk} />
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Additive section: operational "Team Pulse". Its own full-width block below the
          existing content — nothing above is moved, resized, or restyled. */}
      {pulse && <TeamPulsePanel pulse={pulse} />}
    </div>
  );
}

// Thin monochrome line icons for the pulse cards (static markup, not user data).
const TP_ICONS: Record<string, string> = {
  cake: '<path d="M4 21h16M5 21v-7a2 2 0 012-2h10a2 2 0 012 2v7"/><path d="M4 16c1.5 0 1.5-1.2 3-1.2S8.5 16 10 16s1.5-1.2 3-1.2S15.5 16 17 16s1.5-1.2 3-1.2"/><path d="M12 8.5V11M12 4.5l.01 0"/>',
  award: '<circle cx="12" cy="9" r="5.2"/><path d="M8.5 13.6L7.4 21l4.6-2.6L16.6 21l-1.1-7.4"/>',
  plane: '<path d="M21.5 3.5L11.2 13.8M21.5 3.5l-6.5 18-3.8-8.3-8.2-3.8 18.5-5.9z"/>',
  calendar: '<rect x="3.5" y="4.5" width="17" height="16" rx="2"/><path d="M3.5 9.5h17M8 3v3M16 3v3"/>',
  userplus: '<path d="M15.5 20.5v-1.8a3.6 3.6 0 00-3.6-3.6H6.6A3.6 3.6 0 003 18.7v1.8"/><circle cx="9.3" cy="8" r="3.6"/><path d="M19 8.5v5M21.5 11h-5"/>',
  shuffle: '<path d="M15.5 3.5h5v5M20.5 3.5L4 20M20.5 15.5v5h-5M14.5 14.5l6 6M3.5 4.5l5 5"/>',
  shield: '<path d="M12 21.5s7.2-3.6 7.2-9V5l-7.2-2.7L4.8 5v7.5c0 5.4 7.2 9 7.2 9z"/><path d="M12 8.5v3.5M12 15h.01"/>',
};

function TpIcon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeLinecap="round"
         strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: TP_ICONS[name] ?? "" }} />
  );
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("");
  return (parts || name.slice(0, 2)).toUpperCase();
}

type PulseRow = { token: string; tag?: string; when?: string; soon?: boolean;
                  chip?: "expired" | "progress"; chipText?: string };
type PulseCard = { id: string; title: string; icon: string; ch: number;
                   count: number; more: number; rows: PulseRow[] };

const SHOWN = 5;

function buildCards(pulse: TeamPulse): PulseCard[] {
  const card = (id: string, title: string, icon: string, ch: number,
                items: { token: string }[], map: (x: never) => PulseRow): PulseCard => ({
    id, title, icon, ch, count: items.length, more: Math.max(0, items.length - SHOWN),
    rows: items.slice(0, SHOWN).map((x) => map(x as never)),
  });
  return [
    card("birthdays", "Birthdays", "cake", 24, pulse.birthdays,
      (b: TeamPulse["birthdays"][number]) => ({ token: b.token, when: inDaysLabel(b.in_days), soon: b.in_days <= 1 })),
    card("anniversaries", "Work anniversaries", "award", 64, pulse.anniversaries,
      (a: TeamPulse["anniversaries"][number]) => ({ token: a.token, tag: `${a.years} yr`, when: inDaysLabel(a.in_days) })),
    card("pto", "Away now", "plane", 232, pulse.on_pto,
      (p: TeamPulse["on_pto"][number]) => ({ token: p.token, when: p.return_date ? `back ${fmtDate(p.return_date)}` : "away" })),
    card("leave", "Extended leave", "calendar", 292, pulse.on_leave,
      (p: TeamPulse["on_leave"][number]) => ({ token: p.token, when: p.return_date ? `back ${fmtDate(p.return_date)}` : "away" })),
    card("onboarding", "Onboarding", "userplus", 156, pulse.onboarding,
      (o: TeamPulse["onboarding"][number]) => ({ token: o.token, chip: "progress", chipText: "in progress" })),
    card("changes", "Upcoming changes", "shuffle", 200, pulse.role_changes,
      (r: TeamPulse["role_changes"][number]) => ({ token: r.token, tag: r.note, when: inDaysLabel(r.in_days), soon: r.in_days <= 1 })),
    card("credentials", "Expiring credentials", "shield", 28, pulse.credentials,
      (c: TeamPulse["credentials"][number]) => c.in_days < 0
        ? { token: c.token, tag: c.name, chip: "expired", chipText: "expired" }
        : { token: c.token, tag: c.name, when: inDaysLabel(c.in_days), soon: c.in_days <= 1 }),
  ].filter((c) => c.count > 0);
}

function TeamPulsePanel({ pulse }: { pulse: TeamPulse }) {
  const cards = buildCards(pulse);
  const av = pulse.availability;
  const total = av.in_office + av.remote + av.out;
  const segs = [
    { key: "office", label: "In office", value: av.in_office },
    { key: "remote", label: "Remote", value: av.remote },
    { key: "out", label: "Out", value: av.out },
  ];

  // Resolve the visible tokens to names in one audited batch (for avatars + labels).
  const [names, setNames] = useState<Record<string, string>>({});
  useEffect(() => {
    const tokens = [...new Set(cards.flatMap((c) => c.rows.map((r) => r.token)))];
    if (tokens.length === 0) return;
    let alive = true;
    api.resolveNames(tokens).then((m) => { if (alive) setNames(m); }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pulse.as_of, pulse.team_size]);

  const pct = (v: number) => (total ? (v / total) * 100 : 0);

  return (
    <section className="team-pulse" aria-label="Team Pulse">
      <div className="tp-head">
        <span className="tp-rule" />
        <div className="tp-titles">
          <div className="tp-eyebrow">Team Pulse</div>
          <h2>What's <em>happening</em></h2>
          <div className="tp-sub">Moments and movements across your people this month</div>
        </div>
        <span className="tp-spacer" />
        <div className="tp-meta">
          next {pulse.horizon_days} days · <b>{pulse.team_size}</b> in scope<br />as of {fmtDate(pulse.as_of)}
        </div>
      </div>

      <div className="tp-avail">
        <div className="tp-avail-top">
          <span className="lbl">Where people are</span>
          <span className="note">{total} in scope</span>
        </div>
        <div className="tp-bar">
          {segs.map((s) => (
            <span key={s.key} className={`seg-${s.key}`} style={{ flex: `0 0 ${pct(s.value).toFixed(2)}%` }}
                  title={`${s.label}: ${s.value}`} />
          ))}
        </div>
        <div className="tp-legend">
          {segs.map((s) => (
            <div key={s.key} className="it">
              <span className={`dot seg-${s.key}`} />
              <span className="vl">{s.value}</span>
              <span className="nm">{s.label}</span>
              <span className="pc">{pct(s.value).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      </div>

      <div className="tp-div" />

      <div className="tp-grid">
        {cards.map((c) => (
          <article key={c.id} className="tp-card" style={{ ["--ch" as string]: c.ch }}>
            <div className="tp-card-head">
              <span className="tp-ico"><TpIcon name={c.icon} /></span>
              <span className="tp-h-txt"><h3>{c.title}</h3></span>
              <span className="tp-count">{c.count}</span>
            </div>
            <ul className="tp-list">
              {c.rows.map((r, i) => {
                const nm = names[r.token];
                return (
                  <li key={r.token + i}>
                    <a className="tp-row" href={`#/profile/${r.token}`}>
                      <span className="tp-avatar">{nm ? initials(nm) : "··"}</span>
                      <span className="tp-name">{nm ?? <NameTag token={r.token} />}</span>
                      <span className="tp-meta-pair">
                        {r.tag && <span className="tp-tag">{r.tag}</span>}
                        {r.chip
                          ? <span className={`tp-chip ${r.chip}`}><span className="pdot" />{r.chipText}</span>
                          : r.when && <span className={`tp-when${r.soon ? " soon" : ""}`}>{r.when}</span>}
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
            {c.more > 0 && (
              <a className="tp-more" href="#/watchlist">+{c.more} more
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
              </a>
            )}
          </article>
        ))}
      </div>

      <div className="tp-foot">
        <Icon name="lock" size={14} />
        <span>Leave shows <b>availability and return date only</b> — never a reason. Token-only, scoped to your team, audited.</span>
      </div>
    </section>
  );
}

function inDaysLabel(n: number): string {
  if (n <= 0) return "today";
  if (n === 1) return "tomorrow";
  return `in ${n}d`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function Kpi({ icon, label, value, note, color }: { icon: string; label: string; value: number; note: string; color?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label"><Icon name={icon} size={13} /> {label}</div>
      <div className="kpi-val tnum" style={{ color }}><AnimNum value={value} /></div>
      <div className="kpi-foot"><span className="kpi-note">{note}</span></div>
    </div>
  );
}

function Legend({ dot }: { dot?: boolean }) {
  return (
    <div className="legend">
      <span className="legend-item"><span className={`sw${dot ? " dot" : ""}`} style={{ background: "var(--risk-high)" }} /> High</span>
      <span className="legend-item"><span className={`sw${dot ? " dot" : ""}`} style={{ background: "var(--risk-med)" }} /> Medium</span>
      <span className="legend-item"><span className={`sw${dot ? " dot" : ""}`} style={{ background: "var(--risk-low)" }} /> Low</span>
    </div>
  );
}

// Risk × Value matrix on real 0–100 scales; thresholds at 70 (matches riskBand).
function Quadrant({ points }: { points: Dash["quadrant"] }) {
  const W = 560, H = 420, m = { t: 16, r: 16, b: 42, l: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const x = (risk: number) => m.l + (risk / 100) * iw;
  const y = (val: number) => m.t + ih - (val / 100) * ih;
  const maxCost = Math.max(1, ...points.map((p) => p.cost_to_lose ?? 0));
  const rad = (c: number | null) => 4 + Math.sqrt((c ?? 0) / maxCost) * 11;
  const midX = x(70), midY = y(70);
  const zones = [
    { tx: m.l + iw * 0.2, ty: m.t + ih * 0.14, t: "DEVELOP & GROW", s: "high value · stable", hot: false },
    { tx: m.l + iw * 0.85, ty: m.t + ih * 0.14, t: "RETAIN — URGENT", s: "high value · high risk", hot: true },
    { tx: m.l + iw * 0.2, ty: m.t + ih * 0.95, t: "MONITOR", s: "lower value · stable", hot: false },
    { tx: m.l + iw * 0.85, ty: m.t + ih * 0.95, t: "RE-ENGAGE", s: "lower value · high risk", hot: false },
  ];
  const grid = [0, 25, 50, 75, 100];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="risk versus value matrix">
      <rect x={midX} y={m.t} width={W - m.r - midX} height={midY - m.t} fill="var(--risk-high)" opacity={0.06} />
      {grid.map((g) => <line key={`vx${g}`} x1={x(g)} y1={m.t} x2={x(g)} y2={m.t + ih} stroke="var(--grid)" />)}
      {grid.map((g) => <line key={`hy${g}`} x1={m.l} y1={y(g)} x2={m.l + iw} y2={y(g)} stroke="var(--grid)" />)}
      <line x1={midX} y1={m.t} x2={midX} y2={m.t + ih} stroke="var(--border-2)" strokeWidth={1.25} strokeDasharray="4 4" />
      <line x1={m.l} y1={midY} x2={m.l + iw} y2={midY} stroke="var(--border-2)" strokeWidth={1.25} strokeDasharray="4 4" />
      <rect x={m.l} y={m.t} width={iw} height={ih} fill="none" stroke="var(--border)" />
      {zones.map((q, i) => (
        <text key={i} x={q.tx} y={q.ty} textAnchor="middle" fontFamily="var(--mono)" fontSize="9.5" fontWeight="600"
              fill={q.hot ? "var(--risk-high)" : "var(--text-4)"} opacity={q.hot ? 0.9 : 0.7}>
          <tspan x={q.tx} dy="0">{q.t}</tspan>
          <tspan x={q.tx} dy="13" fontFamily="var(--sans)" fontSize="9" fontWeight="500">{q.s}</tspan>
        </text>
      ))}
      {grid.map((g) => <text key={`xl${g}`} x={x(g)} y={m.t + ih + 16} textAnchor="middle" fontSize="9.5" fill="var(--text-4)" fontFamily="var(--mono)">{g}</text>)}
      {grid.map((g) => <text key={`yl${g}`} x={m.l - 10} y={y(g) + 3} textAnchor="end" fontSize="9.5" fill="var(--text-4)" fontFamily="var(--mono)">{g}</text>)}
      <text x={m.l + iw / 2} y={H - 5} textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--text-3)">FLIGHT RISK →</text>
      <text x={14} y={m.t + ih / 2} textAnchor="middle" fontSize="10.5" fontWeight="600" fill="var(--text-3)" transform={`rotate(-90 14 ${m.t + ih / 2})`}>BUSINESS VALUE →</text>
      {points.map((p) => {
        const hot = p.flight_risk >= 70 && p.value_score >= 60;
        return (
          <a key={p.token} href={`#/profile/${p.token}`} style={{ cursor: "pointer" }}>
            <circle cx={x(p.flight_risk)} cy={y(p.value_score)} r={rad(p.cost_to_lose)}
                    fill={riskColor(p.flight_risk)} fillOpacity={hot ? 0.85 : 0.55} stroke="var(--surface)" strokeWidth={1.5}>
              <title>{`risk ${p.flight_risk} · value ${p.value_score}${p.cost_to_lose ? ` · ~${usd(p.cost_to_lose)} to lose` : ""}`}</title>
            </circle>
          </a>
        );
      })}
    </svg>
  );
}
