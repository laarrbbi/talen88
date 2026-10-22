import { useEffect, useRef, useState } from "react";
import { api, ApiError, Employee360, EmployeeDocument, EmployeeRow, Score, TimelineEvent } from "../api/client";
import { useAuth } from "../App";
import { NameTag } from "../components/NameTag";
import { ScoreBar, RiskBadge, Skeleton, SkeletonChartCard } from "../components/ui";
import { Icon } from "../components/icons";
import { byCapitalOrder, capitalLabel, formatCapital, ordinal, titleCase, shortDate, monthYear, riskColor } from "../lib/format";

function valueTier(v: number | null | undefined): number {
  if (v == null) return 0;
  return Math.max(1, Math.min(5, Math.round(v / 20)));
}

export function Profile({ token }: { token?: string }) {
  const [emp, setEmp] = useState<Employee360 | null>(null);
  // The /360 read omits the engineered `features` block (comp gap, months since
  // promotion, tenure, manager changes); those are computed only in the scoped
  // list. We fetch the in-scope list once and merge this token's features in, so
  // the profile shows every datapoint the data layer exposes for the employee.
  const [feat, setFeat] = useState<EmployeeRow["features"]>(null);
  const [events, setEvents] = useState<TimelineEvent[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { me } = useAuth();

  useEffect(() => {
    if (!token) return;
    setEmp(null);
    setFeat(null);
    setEvents(null);
    api.employee360(token).then(setEmp).catch(() => setErr("Not found or out of your scope."));
    api.timeline(token).then((t) => setEvents(t.events)).catch(() => setEvents([]));
    api.employees().then((list) => setFeat(list.find((r) => r.token === token)?.features ?? null)).catch(() => {});
  }, [token]);

  if (!token) return <div className="page"><p className="muted">No employee selected.</p></div>;
  if (err) return <div className="page"><p className="flash err">{err}</p></div>;
  if (!emp) return (
    <div className="page">
      <div className="page-head">
        <div>
          <a className="btn ghost" href="#/watchlist" style={{ marginBottom: 10, paddingLeft: 6 }}>
            <Icon name="arrowleft" size={15} /> Watchlist
          </a>
          <div className="prof-head">
            <Skeleton w={60} h={60} r={13} />
            <div style={{ display: "grid", gap: 9 }}>
              <Skeleton className="sk-line lg" w={208} h={22} />
              <Skeleton className="sk-line" w={276} h={12} />
              <div style={{ display: "flex", gap: 7, marginTop: 2 }}>
                {[70, 96, 58, 78].map((w, i) => <Skeleton key={i} w={w} h={20} r={6} />)}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="profile-grid">
        <div className="stack">
          <SkeletonChartCard h={250} head={false} />
          <SkeletonChartCard h={190} />
        </div>
        <div className="stack">
          <SkeletonChartCard h={150} />
          <SkeletonChartCard h={210} />
        </div>
      </div>
    </div>
  );

  const a = emp.attributes;
  const s = emp.latest_score;
  const p = emp.profile;
  const risk = s?.flight_risk ?? null;
  const tier = valueTier(s?.value_score);
  const comp = feat?.comp_gap ?? null;
  const factors = [...emp.reason_codes].sort((x, y) => y.weight - x.weight);
  // Tenure: prefer the canonical years; fall back to the engineered feature months.
  const tenureYrs = p?.tenure_years ?? (feat ? feat.tenure_months / 12 : null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <a className="btn ghost" href="#/watchlist" style={{ marginBottom: 10, paddingLeft: 6 }}>
            <Icon name="arrowleft" size={15} /> Watchlist
          </a>
          <div className="prof-head">
            <div className="avatar prof-av"><Icon name="profile" size={26} /></div>
            <div>
              <div className="prof-name"><NameTag token={emp.token} /></div>
              <div className="prof-role">{p?.title || emp.role} · {emp.division} · {emp.team}</div>
              <div className="prof-tags">
                <RiskBadge score={risk} label />
                {p?.status && p.status !== "active" && (
                  <span className="code" style={{ color: "var(--risk-med)", borderColor: "var(--risk-med)" }}>
                    {titleCase(p.status)}
                  </span>
                )}
                <span className="code"><span className="mono">{emp.token}</span></span>
                <span className="code">{emp.location}</span>
                <span className="code">level {emp.level}</span>
                <span className="code">{titleCase(emp.employment_type)}</span>
                {tenureYrs != null && <span className="code">{tenureYrs.toFixed(1)} yrs tenure</span>}
                {p?.years_of_experience != null && <span className="code">{p.years_of_experience} yrs experience</span>}
                {emp.manager_token && <span className="code">Reports to <NameTag token={emp.manager_token} /></span>}
                {p?.skiplevel_token && <span className="code">Skip-level <NameTag token={p.skiplevel_token} /></span>}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="profile-grid">
        {/* ---- left column ---- */}
        <div className="stack">
          <div className="card">
            <div className="score-panel">
              <div className="gauge-wrap">
                <Gauge score={risk ?? 0} size={200} />
                <div className="gauge-center">
                  <div className="gauge-score" style={{ color: riskColor(risk) }}>{risk ?? "—"}</div>
                  <div className="gauge-of">flight-risk score</div>
                </div>
              </div>
              <div className="gauge-band"><RiskBadge score={risk} label /></div>
              {s?.leave_probability != null && (
                <div className="gauge-prob">
                  <b>{Math.round(s.leave_probability * 100)}%</b> chance of leaving in the next 12 months
                </div>
              )}
            </div>
            <div className="facts">
              <Fact label="Percentile" value={s?.risk_percentile != null ? ordinal(s.risk_percentile) : "—"} />
              <Fact label="QoQ change" value={s ? `${s.risk_trend > 0 ? "+" : ""}${Math.round(s.risk_trend)}` : "—"}
                    color={s ? (s.risk_trend > 0 ? "var(--risk-high)" : "var(--risk-low)") : undefined} />
              <Fact label="Data confidence" value={s?.confidence != null ? `${s.confidence} / 100` : "—"}
                    color={s?.confidence != null ? (s.confidence >= 70 ? "var(--risk-low)" : s.confidence >= 40 ? "var(--risk-med)" : "var(--risk-high)") : undefined} />
              <Fact label="Value tier" value={tier ? `${tier} / 5` : "—"} />
            </div>
            {s?.risk_percentile == null && s?.confidence == null ? (
              <div className="nodata" style={{ margin: "12px 14px 14px" }}>
                <Icon name="alert" /> <span>Percentile &amp; data confidence need the current scorer — re-run the score refresh.</span>
              </div>
            ) : (
              <p className="caveat" style={{ margin: "10px 14px 14px" }}>
                Percentile ranks this person among current employees (higher = riskier). Data confidence
                shows how complete their inputs were{s?.scorer_version ? ` · ${s.scorer_version}` : ""}.
              </p>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="pulse" size={16} style={{ color: "var(--text-3)" }} /> Contributing Factors</div>
              <span className="card-title"><span className="ann">reason-code weighted</span></span>
            </div>
            <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
              {factors.length === 0 && <p className="muted">No reason codes recorded.</p>}
              {factors.map((f, i) => (
                <div className="factor" key={i}>
                  <div className="factor-top">
                    <span className="factor-name">
                      <span className={f.direction === "increases" ? "dir-inc" : "dir-dec"}>{f.direction === "increases" ? "▲" : "▼"}</span> {titleCase(f.label)}
                    </span>
                    <span className="factor-w">{Math.round(f.weight * 100)}%</span>
                  </div>
                  {f.detail && <div className="factor-detail">{f.detail}</div>}
                  <div className="factor-bar">
                    <div className="factor-fill" style={{ width: `${f.weight * 100}%`, background: f.weight >= 0.7 ? "var(--risk-high)" : f.weight >= 0.5 ? "var(--risk-med)" : "var(--accent)" }} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="comp" size={16} style={{ color: "var(--text-3)" }} /> Capital Metrics</div>
            </div>
            <div className="card-body">
              <p className="caveat" style={{ marginTop: 0 }}>All dollar / ratio figures are modeled estimates — not precise individual values.</p>
              <table className="tbl">
                <tbody>
                  {[...emp.capital].sort(byCapitalOrder).map((c) => (
                    <tr key={c.metric} style={{ cursor: "default" }}>
                      <td>{capitalLabel(c.metric)}</td>
                      <td className="td-num" style={{ fontWeight: 600 }}>
                        {formatCapital(c)}{c.is_estimate ? <span className="caveat"> *est</span> : null}
                      </td>
                    </tr>
                  ))}
                  {!emp.capital.length && <tr><td className="muted">No capital metrics yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* ---- right column ---- */}
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="pulse" size={16} style={{ color: "var(--text-3)" }} /> Flight-Risk Trend</div>
            </div>
            <div className="card-body">
              <Trend history={emp.history} events={events ?? []} />
              <RecentEvents events={events} />
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="target" size={16} style={{ color: "var(--text-3)" }} /> Intelligence Panel</div>
            </div>
            <div className="card-body">
              {emp.panel.length ? emp.panel.map((m) => <ScoreBar key={m.metric} score={m.score} label={m.metric}
                                                                 risk={m.metric === "retention_risk"} />)
                : <p className="muted">No score panel yet — run the score refresh.</p>}
              {a && a.engagement_pulse == null && (
                <p className="caveat">Engagement is not shown: this employee has not consented to engagement signals (no covert monitoring).</p>
              )}
            </div>
          </div>

          <div className="grid-3">
            <StatMini label="Comp gap" value={comp == null ? "—" : `${Math.round(comp * 100)}%`}
                      color={comp != null && comp > 0 ? "var(--risk-high)" : "var(--up)"}
                      sub={comp == null ? "no feature data" : comp > 0 ? "below market midpoint" : "at or above midpoint"} />
            <StatMini label="Engagement" value={a?.engagement_pulse == null ? "—" : String(a.engagement_pulse)}
                      sub={a?.engagement_pulse == null ? "not consented (no covert monitoring)" : "consented pulse signal"} />
            <StatMini label="Mgr changes (12mo)" value={feat ? String(feat.manager_changes_12mo) : "—"}
                      color={feat && feat.manager_changes_12mo > 1 ? "var(--risk-high)" : undefined}
                      sub="manager stability" />
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-head">
                <div className="card-title"><Icon name="check" size={16} style={{ color: "var(--accent)" }} /> Recommended Actions</div>
              </div>
              <div className="card-body">
                <div className="nodata">
                  <Icon name="spark2" />
                  <span>Retention recommendations are generated in the <a href="#/agents">Agents workspace</a> — not yet inlined on the profile.</span>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <div className="card-title"><Icon name="clock" size={16} style={{ color: "var(--text-3)" }} /> Career History</div>
              </div>
              <div className="card-body">
                <CareerHistory events={events} />
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="award" size={16} style={{ color: "var(--text-3)" }} /> Skills</div>
            </div>
            <div className="card-body">
              {emp.skills.length ? (
                <div className="codes">
                  {emp.skills.map((sk) => (
                    <span className="code" key={sk.name}>{sk.name} <span className="mono" style={{ color: "var(--text-4)" }}>{sk.family} · {sk.proficiency}/5</span></span>
                  ))}
                </div>
              ) : <p className="muted">No recorded skills.</p>}
              {a && (
                <div className="facts" style={{ marginTop: 14 }}>
                  <Fact label="Perf rating" value={`${a.perf_rating.toFixed(1)}/5`} />
                  <Fact label="Learning hrs (12mo)" value={String(a.learning_hours_12mo)} />
                  <Fact label="Internal moves" value={String(a.internal_moves)} />
                  <Fact label="Span of control" value={String(a.span_of_control)} />
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="award" size={16} style={{ color: "var(--text-3)" }} /> Education &amp; Credentials</div>
              {p?.pay_band && <span className="card-title"><span className="ann">band {p.pay_band}</span></span>}
            </div>
            <div className="card-body">
              <div className="facts">
                <Fact label="Education" value={p?.education_level ? titleCase(p.education_level) : "—"} />
                <Fact label="Field" value={p?.education_field ? titleCase(p.education_field) : "—"} />
                <Fact label="Experience" value={p?.years_of_experience != null ? `${p.years_of_experience} yrs` : "—"} />
                <Fact label="Pay percentile" value={p?.pay_percentile_in_role != null ? `${Math.round(p.pay_percentile_in_role * 100)}th` : "—"} />
              </div>

              <div className="prof-subhead">Certifications</div>
              {p?.certifications.length ? (
                <div className="codes">
                  {p.certifications.map((c) => (
                    <span className="code" key={c}><Icon name="award" size={11} /> {c}</span>
                  ))}
                </div>
              ) : <p className="muted">None recorded.</p>}

              <div className="prof-subhead">Licenses</div>
              {p?.licenses.length ? (
                <table className="tbl">
                  <tbody>
                    {p.licenses.map((l, i) => {
                      const exp = expiryState(l.expiry);
                      return (
                        <tr key={i} style={{ cursor: "default" }}>
                          <td>{l.name}</td>
                          <td className="td-num" style={{ color: exp.color, fontWeight: 600 }}>
                            {l.expiry ? `${exp.label} ${monthYear(l.expiry)}` : "no expiry"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : <p className="muted">None recorded.</p>}
            </div>
          </div>

          <DocumentsCard token={emp.token} isAdmin={me.role === "admin"} />

          <div className="card">
            <div className="card-head">
              <div className="card-title2">Surveys &amp; Responses<span className="ann">confidential</span></div>
            </div>
            <div className="card-body">
              <div className="nodata">
                <Icon name="survey" /> <span>Survey responses are <b>confidential</b> — reported only in aggregate, above each campaign's minimum group size, on the <a href="#/surveys">Surveys</a> screen. They are never shown per person.</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Credential expiry framing: expired (red) / expiring within 90 days (amber) / valid.
function expiryState(iso?: string | null): { label: string; color: string } {
  if (!iso) return { label: "", color: "var(--text-3)" };
  const days = (new Date(iso).getTime() - Date.now()) / 86400000;
  if (isNaN(days)) return { label: "expires", color: "var(--text-3)" };
  if (days < 0) return { label: "expired", color: "var(--risk-high)" };
  if (days < 90) return { label: "expires", color: "var(--risk-med)" };
  return { label: "valid to", color: "var(--text-3)" };
}

function Fact({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="fact">
      <div className="fl">{label}</div>
      <div className="fv tnum" style={{ color }}>{value}</div>
    </div>
  );
}

function StatMini({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <div className="card stat-mini">
      <div className="sl">{label}</div>
      <div className="sv" style={{ color }}>{value}</div>
      <div className="sx">{sub}</div>
    </div>
  );
}

// Radial flight-risk gauge (135°→405° sweep), ported from the design handoff.
function Gauge({ score, size = 200 }: { score: number; size?: number }) {
  const r = size / 2 - 16, cx = size / 2, cy = size / 2, start = 135, end = 405;
  const ang = start + (end - start) * (score / 100);
  const arc = (a0: number, a1: number, R: number) => {
    const p0 = [cx + R * Math.cos((a0 * Math.PI) / 180), cy + R * Math.sin((a0 * Math.PI) / 180)];
    const p1 = [cx + R * Math.cos((a1 * Math.PI) / 180), cy + R * Math.sin((a1 * Math.PI) / 180)];
    const large = a1 - a0 > 180 ? 1 : 0;
    return `M${p0[0].toFixed(1)} ${p0[1].toFixed(1)} A${R} ${R} 0 ${large} 1 ${p1[0].toFixed(1)} ${p1[1].toFixed(1)}`;
  };
  const col = riskColor(score);
  const hx = cx + r * Math.cos((ang * Math.PI) / 180), hy = cy + r * Math.sin((ang * Math.PI) / 180);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <path d={arc(start, end, r)} fill="none" stroke="var(--inset)" strokeWidth={11} strokeLinecap="round" />
      <path d={arc(start + (end - start) * 0.45, start + (end - start) * 0.70, r)} fill="none" stroke="var(--risk-med)" strokeWidth={3} opacity={0.4} />
      <path d={arc(start + (end - start) * 0.70, end, r)} fill="none" stroke="var(--risk-high)" strokeWidth={3} opacity={0.4} />
      <path d={arc(start, ang, r)} fill="none" stroke={col} strokeWidth={11} strokeLinecap="round" />
      <circle cx={hx.toFixed(1)} cy={hy.toFixed(1)} r={6.5} fill="var(--surface)" stroke={col} strokeWidth={3} />
    </svg>
  );
}

// Retention-risk trend area chart on a real 0–100 scale, dates from /360 history.
// Event kinds -> short marker label + colour on the trend chart and in the history list.
const EVENT_STYLE: Record<string, { mark: string; color: string; label: string }> = {
  promotion: { mark: "P", color: "var(--risk-low)", label: "Promotion" },
  raise: { mark: "$", color: "var(--risk-low)", label: "Pay raise" },
  bonus: { mark: "B", color: "var(--risk-low)", label: "Bonus" },
  vest: { mark: "V", color: "var(--accent)", label: "Stock vest" },
  manager_change: { mark: "M", color: "var(--risk-med)", label: "New manager" },
  internal_move: { mark: "T", color: "var(--text-3)", label: "Team move" },
  internal_application: { mark: "A", color: "var(--risk-med)", label: "Internal application" },
  role_change: { mark: "R", color: "var(--text-3)", label: "Role change" },
  intervention: { mark: "★", color: "var(--accent)", label: "Retention action" },
  hire: { mark: "H", color: "var(--text-3)", label: "Joined" },
  exit: { mark: "×", color: "var(--risk-high)", label: "Left" },
};
const styleOf = (kind: string) => EVENT_STYLE[kind] ?? { mark: "•", color: "var(--text-3)", label: kind };

function Trend({ history, events }: { history: Score[]; events: TimelineEvent[] }) {
  if (history.length < 2) return <p className="muted">Not enough history to chart.</p>;
  const W = 720, H = 250, m = { t: 18, r: 18, b: 30, l: 36 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b, n = history.length;
  const x = (i: number) => m.l + (i / (n - 1)) * iw;
  const y = (v: number) => m.t + ih - (v / 100) * ih;
  const vals = history.map((h) => h.flight_risk);
  const line = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(n - 1)} ${m.t + ih} L${x(0)} ${m.t + ih} Z`;
  const cur = vals[n - 1], col = riskColor(cur);
  const grid = [0, 25, 50, 75, 100];
  // Place each event between the snapshots it falls between (dates are ISO, so they sort).
  const t0 = Date.parse(history[0].as_of_date), t1 = Date.parse(history[n - 1].as_of_date);
  const xAt = (iso: string) => m.l + ((Date.parse(iso) - t0) / (t1 - t0)) * iw;
  const inWindow = events.filter((e) => !e.upcoming && e.date >= history[0].as_of_date
                                        && e.date <= history[n - 1].as_of_date);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: "block" }} role="img" aria-label="risk trend">
      <defs>
        <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity={0.18} />
          <stop offset="100%" stopColor={col} stopOpacity={0} />
        </linearGradient>
      </defs>
      <rect x={m.l} y={y(100)} width={iw} height={y(70) - y(100)} fill="var(--risk-high)" opacity={0.05} />
      <rect x={m.l} y={y(70)} width={iw} height={y(45) - y(70)} fill="var(--risk-med)" opacity={0.05} />
      {grid.map((g) => (
        <g key={g}>
          <line x1={m.l} y1={y(g)} x2={m.l + iw} y2={y(g)} stroke="var(--grid)" />
          <text x={m.l - 8} y={y(g) + 3} textAnchor="end" fontSize="9.5" fill="var(--text-4)" fontFamily="var(--mono)">{g}</text>
        </g>
      ))}
      <path d={area} fill="url(#trendFill)" />
      <path d={line} fill="none" stroke={col} strokeWidth={2.25} strokeLinecap="round" strokeLinejoin="round" />
      {history.map((h, i) => (
        <text key={h.as_of_date} x={x(i)} y={H - 8} textAnchor="middle" fontSize="9.5" fill="var(--text-4)" fontFamily="var(--mono)">{shortDate(h.as_of_date)}</text>
      ))}
      {inWindow.map((e, i) => {
        const st = styleOf(e.kind), ex = xAt(e.date);
        return (
          <g key={`${e.kind}-${e.date}-${i}`}>
            <line x1={ex} y1={m.t + 12} x2={ex} y2={m.t + ih} stroke={st.color} strokeDasharray="3 3" opacity={0.7} />
            <circle cx={ex} cy={m.t + 6} r={8} fill="var(--surface)" stroke={st.color} strokeWidth={1.5} />
            <text x={ex} y={m.t + 9.5} textAnchor="middle" fontSize="9.5" fontWeight={700} fill={st.color}>{st.mark}</text>
            <title>{`${shortDate(e.date)} · ${e.title}${e.detail ? ` — ${e.detail}` : ""}`}</title>
          </g>
        );
      })}
      <circle cx={x(n - 1)} cy={y(cur)} r={4} fill={col} stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}

// The latest few past events and the next upcoming one, under the trend chart.
function RecentEvents({ events }: { events: TimelineEvent[] | null }) {
  if (events == null) return <Skeleton className="sk-line" w="60%" h={12} style={{ marginTop: 12 }} />;
  const past = events.filter((e) => !e.upcoming).slice(0, 4);
  const next = [...events].reverse().find((e) => e.upcoming);
  if (!past.length && !next) return <p className="muted" style={{ marginTop: 10 }}>No dated events on record.</p>;
  return (
    <div className="ev-strip">
      {next && <EventChip e={next} />}
      {past.map((e, i) => <EventChip key={i} e={e} />)}
    </div>
  );
}

function EventChip({ e }: { e: TimelineEvent }) {
  const st = styleOf(e.kind);
  return (
    <span className={`ev-chip${e.upcoming ? " upcoming" : ""}`} title={e.detail || undefined}>
      <b style={{ color: st.color }}>{st.mark}</b> {e.title}
      <span className="ev-date">{e.upcoming ? "due " : ""}{e.approximate ? "~" : ""}{monthYear(e.date)}</span>
    </span>
  );
}

function CareerHistory({ events }: { events: TimelineEvent[] | null }) {
  if (events == null) return <Skeleton className="sk-line" w="70%" h={14} />;
  if (!events.length) return <p className="muted">No career events on record.</p>;
  return (
    <div className="timeline">
      {events.map((e, i) => {
        const st = styleOf(e.kind);
        return (
          <div className={`tl-item${e.upcoming ? " tl-upcoming" : ""}`} key={i}>
            <span className="tl-dot" style={{ borderColor: st.color, background: e.kind === "hire" ? st.color : undefined }} />
            <div className="tl-date">{e.upcoming ? "upcoming · " : ""}{e.approximate ? "about " : ""}{monthYear(e.date)}</div>
            <div className="tl-title">{e.title}</div>
            {e.detail && <div className="tl-desc">{e.detail}</div>}
          </div>
        );
      })}
    </div>
  );
}

const DOC_TYPE_OPTIONS: [string, string][] = [
  ["auto", "Detect type"], ["cv", "CV / résumé"], ["contract", "Contract"], ["offer", "Offer letter"],
  ["payslip", "Payslip"], ["review", "Performance review"], ["other", "Other"],
];

function fileSize(n: number): string {
  return n >= 1_048_576 ? `${(n / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

// HR-admin only: the server refuses everyone else, and the card says so instead of
// showing an error.
function DocumentsCard({ token, isAdmin }: { token: string; isAdmin: boolean }) {
  const [docs, setDocs] = useState<EmployeeDocument[] | null>(null);
  const [docType, setDocType] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isAdmin) return;
    api.documents(token).then(setDocs).catch(() => setDocs([]));
  }, [token, isAdmin]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const doc = await api.uploadDocument(token, file, docType);
      setDocs((d) => [doc, ...(d ?? [])]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  const remove = async (doc: EmployeeDocument) => {
    if (!window.confirm(`Delete ${doc.filename}? This cannot be undone.`)) return;
    try {
      await api.deleteDocument(token, doc.id);
      setDocs((d) => (d ?? []).filter((x) => x.id !== doc.id));
    } catch {
      setErr("Could not delete that document.");
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title2">Documents<span className="ann">{isAdmin ? `${docs?.length ?? 0} files · HR only` : "HR only"}</span></div>
        {isAdmin && (
          <div className="row" style={{ gap: 8 }}>
            <select className="chip-filter" value={docType} onChange={(e) => setDocType(e.target.value)} aria-label="Document type">
              {DOC_TYPE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <button className="btn" disabled={busy} onClick={() => input.current?.click()}>
              <Icon name="plus" size={14} /> {busy ? "Uploading…" : "Upload"}
            </button>
            <input ref={input} type="file" hidden accept=".pdf,.docx,.txt,.png,.jpg,.jpeg"
                   onChange={(e) => upload(e.target.files?.[0])} />
          </div>
        )}
      </div>
      <div className="card-body">
        {!isAdmin ? (
          <div className="nodata">
            <Icon name="lock" /> <span>CVs, contracts, payslips and reviews are visible to <b>HR admins only</b>.</span>
          </div>
        ) : (
          <>
            {err && <p className="flash err" style={{ marginTop: 0 }}>{err}</p>}
            {docs == null ? <Skeleton className="sk-line" w="60%" h={14} /> : docs.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>No documents yet. Uploads are encrypted and every access is logged.</p>
            ) : (
              <table className="tbl">
                <tbody>
                  {docs.map((d) => (
                    <tr key={d.id} style={{ cursor: "default" }}>
                      <td><span className="code">{titleCase(d.doc_type)}</span></td>
                      <td style={{ wordBreak: "break-all" }}>{d.filename}</td>
                      <td className="td-num subtle">{fileSize(d.size_bytes)} · {monthYear(d.uploaded_at)}</td>
                      <td className="td-num" style={{ whiteSpace: "nowrap" }}>
                        <button className="icon-btn" title="Download" onClick={() => api.downloadDocument(token, d).catch(() => setErr("Download failed."))}>
                          <Icon name="download" size={15} />
                        </button>
                        <button className="icon-btn" title="Delete" onClick={() => remove(d)}>
                          <Icon name="close" size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </div>
    </div>
  );
}
