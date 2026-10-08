import { useEffect, useState } from "react";
import { api, Employee360, EmployeeRow, Score } from "../api/client";
import { NameTag } from "../components/NameTag";
import { ScoreBar, RiskBadge, Skeleton, SkeletonChartCard } from "../components/ui";
import { Icon } from "../components/icons";
import { formatCapital, titleCase, shortDate, monthYear, riskColor } from "../lib/format";

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
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    setEmp(null);
    setFeat(null);
    api.employee360(token).then(setEmp).catch(() => setErr("Not found or out of your scope."));
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
            </div>
            <div className="facts">
              <Fact label="Percentile" value="—" />
              <Fact label="QoQ change" value={s ? `${s.risk_trend > 0 ? "+" : ""}${Math.round(s.risk_trend)}` : "—"}
                    color={s ? (s.risk_trend > 0 ? "var(--risk-high)" : "var(--risk-low)") : undefined} />
              <Fact label="Model confidence" value="—" />
              <Fact label="Value tier" value={tier ? `${tier} / 5` : "—"} />
            </div>
            <div className="nodata" style={{ margin: "12px 14px 14px" }}>
              <Icon name="alert" /> <span>Percentile &amp; model confidence are <b>not yet exposed</b> by the scoring API.</span>
            </div>
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
                  {emp.capital.map((c) => (
                    <tr key={c.metric} style={{ cursor: "default" }}>
                      <td>{titleCase(c.metric)}</td>
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
              <Trend history={emp.history} />
              <div className="nodata" style={{ marginTop: 12 }}>
                <Icon name="alert" /> <span>Event annotations (comp / role / risk events on the timeline) are <b>not yet available</b> from the API.</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title"><Icon name="target" size={16} style={{ color: "var(--text-3)" }} /> Intelligence Panel</div>
            </div>
            <div className="card-body">
              {emp.panel.length ? emp.panel.map((m) => <ScoreBar key={m.metric} score={m.score} label={m.metric} />)
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
                <div className="card-title"><Icon name="clock" size={16} style={{ color: "var(--text-3)" }} /> Role &amp; Tenure History</div>
              </div>
              <div className="card-body">
                <div className="timeline">
                  {p?.promotions && p.promotions.length ? (
                    [...p.promotions]
                      .sort((x, y) => (y.date ?? "").localeCompare(x.date ?? ""))
                      .map((pr, i) => (
                        <div className="tl-item" key={i}>
                          <span className="tl-dot" />
                          <div className="tl-date">{pr.date ? monthYear(pr.date) : "—"}</div>
                          <div className="tl-title">Promotion{pr.to ? ` → ${pr.to}` : ""}</div>
                          <div className="tl-desc">{pr.from ? `From ${pr.from}` : "Level change"}</div>
                        </div>
                      ))
                  ) : feat && (
                    <div className="tl-item">
                      <span className="tl-dot" />
                      <div className="tl-date">{feat.months_since_promotion} mo ago</div>
                      <div className="tl-title">Last promotion</div>
                      <div className="tl-desc">
                        {feat.months_since_promotion === 0
                          ? "Promoted in the current period"
                          : `${(feat.months_since_promotion / 12).toFixed(1)} yrs since last promotion`}
                      </div>
                    </div>
                  )}
                  <div className="tl-item">
                    <span className="tl-dot accent" />
                    <div className="tl-date">{monthYear(emp.hire_date)}</div>
                    <div className="tl-title">Hired · {titleCase(emp.employment_type)}</div>
                    <div className="tl-desc">{p?.title || emp.role} in {emp.division}</div>
                  </div>
                </div>
                {!(p?.promotions && p.promotions.length) && (
                  <div className="nodata" style={{ marginTop: 12 }}>
                    <Icon name="alert" /> <span>Detailed per-event role history is limited — hire date and last-promotion timing are the available signals.</span>
                  </div>
                )}
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

          <div className="card">
            <div className="card-head">
              <div className="card-title2">Documents<span className="ann">0 files</span></div>
            </div>
            <div className="card-body">
              <div className="nodata">
                <Icon name="file" /> <span>Source-document management (CV, contract, comp letters, reviews) is <b>not yet available</b>.</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <div className="card-title2">Surveys &amp; Responses<span className="ann">0 completed</span></div>
            </div>
            <div className="card-body">
              <div className="nodata">
                <Icon name="survey" /> <span>Per-employee survey responses are <b>not yet available</b> (survey backend deferred).</span>
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
function Trend({ history }: { history: Score[] }) {
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
      <circle cx={x(n - 1)} cy={y(cur)} r={4} fill={col} stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}
