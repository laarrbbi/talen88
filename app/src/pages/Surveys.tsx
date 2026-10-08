/**
 * Surveys — employee-listening module (live).
 *
 * Replaces the former UI stub with a real, wired surface over the survey seam
 * (:8000, token-only · scoped · audited): browse campaigns, build one from the
 * science-backed question library/templates, distribute it, and read confidential
 * aggregate results. Results respect the minimum-reporting threshold (a cohort below
 * it is suppressed server-side). Closing a campaign writes results back into the
 * engagement table, which feeds the Analytics "Engagement" tab and the flight-risk model.
 */
import { useEffect, useState } from "react";
import {
  api, SurveyCampaign, SurveyCampaignDetail, SurveyResults, SurveyTemplate,
  SurveyTemplateDetail, CampaignCreate,
} from "../api/client";
import { Icon } from "../components/icons";
import { Skeleton, SkeletonChartCard, SkeletonHead } from "../components/ui";

// ---- skeleton helpers (shapes mirror the real survey surfaces) --------------
function SurveyCardSkeleton() {
  return (
    <div className="svy-card" style={{ cursor: "default", padding: 16 }}>
      <div className="svy-card-top" style={{ marginBottom: 14 }}>
        <Skeleton className="sk-line sm" w={96} h={10} />
        <Skeleton className="sk-line sm" w={48} h={16} r={8} />
      </div>
      <Skeleton className="sk-line lg" w="72%" style={{ marginBottom: 9 }} />
      <Skeleton className="sk-line sm" w="56%" h={10} style={{ marginBottom: 18 }} />
      <div className="svy-mid">
        <Skeleton className="sk-circle" w={82} h={82} />
        <div className="svy-stats" style={{ display: "grid", gap: 12, flex: 1 }}>
          <Skeleton className="sk-line" w="60%" />
          <Skeleton className="sk-line" w="48%" />
          <Skeleton className="sk-line" w="54%" />
        </div>
      </div>
    </div>
  );
}

function MetricStripSkeleton({ n = 4 }: { n?: number }) {
  return (
    <div className="metric-strip">
      {Array.from({ length: n }).flatMap((_, i) => [
        ...(i > 0 ? [<div className="ms-div" key={`d${i}`} />] : []),
        <div className="ms-item" key={i}>
          <div style={{ display: "grid", gap: 8 }}>
            <Skeleton className="sk-line lg" w={62} h={21} />
            <Skeleton className="sk-line sm" w={96} h={9} />
          </div>
        </div>,
      ])}
    </div>
  );
}

const TYPE_LABEL: Record<string, string> = {
  engagement: "Engagement", pulse: "Pulse", lifecycle: "Lifecycle", enps: "eNPS",
  manager_effectiveness: "Manager Effectiveness", dei: "DEI / Belonging",
  wellbeing: "Wellbeing", change: "Change", custom: "Custom",
};
const STATUS_LABEL: Record<string, string> = {
  draft: "Draft", scheduled: "Scheduled", active: "Active", closed: "Closed", archived: "Archived",
};

function rateOf(c: { invited: number; responded: number }): number {
  return c.invited ? Math.round((c.responded / c.invited) * 100) : 0;
}
function ringColor(status: string, rate: number): string {
  if (status === "draft" || status === "scheduled") return "var(--text-4)";
  return rate >= 75 ? "var(--risk-low)" : rate >= 50 ? "var(--accent)" : "var(--risk-med)";
}
function scoreColor(v: number): string {
  return v >= 60 ? "var(--risk-low)" : v >= 45 ? "var(--risk-med)" : "var(--risk-high)";
}

// Sub-view is driven by the hash (#/surveys, #/surveys/new, #/surveys/:id) so the nav
// resets correctly, campaigns deep-link, and browser back/forward work.
export function Surveys({ sub }: { sub?: string }) {
  const go = (hash: string) => { window.location.hash = hash; };
  if (sub === "new") return <Builder onDone={(id) => go(id ? `#/surveys/${id}` : "#/surveys")} />;
  if (sub && /^\d+$/.test(sub)) return <Detail id={Number(sub)} onBack={() => go("#/surveys")} />;
  return <List onOpen={(id) => go(`#/surveys/${id}`)} onNew={() => go("#/surveys/new")} />;
}

// ---- list -------------------------------------------------------------------
function List({ onOpen, onNew }: { onOpen: (id: number) => void; onNew: () => void }) {
  const [campaigns, setCampaigns] = useState<SurveyCampaign[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.surveyCampaigns().then(setCampaigns).catch(() => setErr("Could not load campaigns."));
  }, []);

  if (err) return <div className="page"><p className="flash err">{err}</p></div>;
  if (!campaigns) return (
    <div className="page">
      <SkeletonHead actions />
      <MetricStripSkeleton />
      <div className="svy-grid">
        {Array.from({ length: 6 }).map((_, i) => <SurveyCardSkeleton key={i} />)}
      </div>
    </div>
  );

  const active = campaigns.filter((c) => c.status === "active").length;
  const totalResp = campaigns.reduce((s, c) => s + c.responded, 0);
  const launched = campaigns.filter((c) => c.invited > 0);
  const avgRate = launched.length
    ? Math.round(launched.reduce((s, c) => s + rateOf(c), 0) / launched.length) : 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Surveys</div>
          <div className="page-sub">
            <span><b style={{ color: "var(--text)" }}>{active}</b> active</span>
            <span className="dot-sep" />
            <span className="mono">{totalResp.toLocaleString()} responses collected</span>
            <span className="dot-sep" />
            <span>Build, distribute, and analyze employee listening</span>
          </div>
        </div>
        <div className="page-actions">
          <button className="btn primary" onClick={onNew}><Icon name="plus" size={15} /> New survey</button>
        </div>
      </div>

      <div className="metric-strip">
        <div className="ms-item"><div className="ms-val">{avgRate}<span>%</span></div><div className="ms-lbl">Avg response rate</div></div>
        <div className="ms-div" />
        <div className="ms-item"><div className="ms-val">{totalResp.toLocaleString()}</div><div className="ms-lbl">Responses collected</div></div>
        <div className="ms-div" />
        <div className="ms-item"><div className="ms-val">{active}</div><div className="ms-lbl">Active campaigns</div></div>
        <div className="ms-div" />
        <div className="ms-item"><div className="ms-val">{campaigns.length}</div><div className="ms-lbl">Total campaigns</div></div>
      </div>

      {campaigns.length === 0 ? (
        <div className="svy-empty" style={{ padding: "60px 0" }}>
          <Icon name="survey" size={28} style={{ opacity: 0.4 }} />
          <div>No campaigns yet</div>
          <span>Create your first survey from the question library</span>
          <button className="btn primary" style={{ marginTop: 14 }} onClick={onNew}><Icon name="plus" size={14} /> New survey</button>
        </div>
      ) : (
        <div className="svy-grid">
          {campaigns.map((c) => {
            const rate = rateOf(c);
            return (
              <button key={c.id} className={`svy-card status-${c.status}`} onClick={() => onOpen(c.id)}>
                <div className="svy-card-top">
                  <span className="svy-freq">{TYPE_LABEL[c.type] ?? c.type}{c.cadence ? ` · ${c.cadence}` : ""}</span>
                  <span className={`svy-status ${c.status}`}>{STATUS_LABEL[c.status] ?? c.status}</span>
                </div>
                <div className="svy-title">{c.title}</div>
                <div className="svy-aud">{c.division ? `${c.division} · ` : "All divisions · "}{c.anonymous ? "anonymous" : "confidential"}</div>
                <div className="svy-mid">
                  <Donut pct={rate} size={82} col={ringColor(c.status, rate)}
                         center={c.status === "draft" ? <span className="dr">Draft</span> : <>{rate}<span>%</span></>} />
                  <div className="svy-stats">
                    <div className="svy-stat"><b>{c.invited.toLocaleString()}</b><span>recipients</span></div>
                    <div className="svy-stat"><b>{c.responded.toLocaleString()}</b><span>responses</span></div>
                    <div className="svy-stat"><b>{c.question_count}</b><span>questions</span></div>
                  </div>
                </div>
                <div className="svy-foot">
                  <span className="svy-sent">
                    {c.trigger_event ? <><Icon name="spark2" size={12} /> on {c.trigger_event}</>
                      : c.opens_at ? `Opened ${c.opens_at.slice(0, 10)}` : "Not yet sent"}
                  </span>
                  <span className="svy-csv"><Icon name="chevright" size={13} /> Open</span>
                </div>
              </button>
            );
          })}
          <button className="svy-card svy-new" onClick={onNew}>
            <div className="svy-new-ico"><Icon name="plus" size={22} /></div>
            <div className="svy-new-t">New survey</div>
            <div className="svy-new-s">Build from the question library</div>
          </button>
        </div>
      )}
    </div>
  );
}

// ---- detail + results -------------------------------------------------------
function Detail({ id, onBack }: { id: number; onBack: () => void }) {
  const [camp, setCamp] = useState<SurveyCampaignDetail | null>(null);
  const [results, setResults] = useState<SurveyResults | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setErr(null);
    api.surveyCampaign(id).then(setCamp).catch(() => setErr("Campaign not found or out of scope."));
    api.surveyResults(id).then(setResults).catch(() => setResults(null));
  };
  useEffect(load, [id]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); load(); } catch { setErr("Action failed."); } finally { setBusy(false); }
  };

  if (err) return <div className="page"><button className="btn ghost" onClick={onBack} style={{ marginBottom: 12 }}><Icon name="arrowleft" size={15} /> All surveys</button><p className="flash err">{err}</p></div>;
  if (!camp) return (
    <div className="page">
      <SkeletonHead />
      <div className="svy-hero">
        <Skeleton className="sk-circle" w={92} h={92} />
        <div className="svy-hero-stats">
          {Array.from({ length: 4 }).map((_, i) => (
            <div className="ms-item" key={i}>
              <Skeleton className="sk-line lg" w={54} h={20} style={{ marginBottom: 8 }} />
              <Skeleton className="sk-line sm" w={82} h={9} />
            </div>
          ))}
        </div>
      </div>
      <div className="grid-2" style={{ alignItems: "start" }}>
        <SkeletonChartCard h={260} />
        <SkeletonChartCard h={260} />
      </div>
    </div>
  );

  const rate = rateOf(camp);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <button className="btn ghost" style={{ marginBottom: 10, paddingLeft: 6 }} onClick={onBack}><Icon name="arrowleft" size={15} /> All surveys</button>
          <div className="page-title">{camp.title} <span className={`svy-status ${camp.status}`}>{STATUS_LABEL[camp.status] ?? camp.status}</span></div>
          <div className="page-sub">
            <span className="mono">SVY-{camp.id}</span><span className="dot-sep" />
            <span>{TYPE_LABEL[camp.type] ?? camp.type}</span><span className="dot-sep" />
            <span>{camp.division ?? "All divisions"}</span>
            {camp.trigger_event && <><span className="dot-sep" /><span><Icon name="spark2" size={12} /> on {camp.trigger_event}</span></>}
          </div>
        </div>
        <div className="page-actions">
          {camp.status === "draft" && (
            <button className="btn primary" disabled={busy} onClick={() => act(() => api.launchCampaign(id))}>
              <Icon name="send" size={15} /> {camp.trigger_event ? "Activate" : "Launch"}
            </button>
          )}
          {camp.status === "active" && (
            <button className="btn" disabled={busy} onClick={() => act(() => api.closeCampaign(id))}>
              <Icon name="check" size={15} /> Close &amp; write back
            </button>
          )}
          {camp.status === "closed" && (
            <button className="btn ghost" disabled={busy} onClick={() => act(() => api.setCampaignStatus(id, "archived"))}>
              <Icon name="file" size={15} /> Archive
            </button>
          )}
        </div>
      </div>

      <div className="svy-hero">
        <Donut pct={rate} size={92} col={ringColor(camp.status, rate)}
               center={camp.status === "draft" ? <span className="dr">Draft</span> : <>{rate}<span>%</span></>} />
        <div className="svy-hero-stats">
          <div className="ms-item"><div className="ms-val">{camp.invited.toLocaleString()}</div><div className="ms-lbl">Recipients</div></div>
          <div className="ms-item"><div className="ms-val">{camp.responded.toLocaleString()}</div><div className="ms-lbl">Responses</div></div>
          <div className="ms-item"><div className="ms-val">{camp.question_count}</div><div className="ms-lbl">Questions</div></div>
          <div className="ms-item"><div className="ms-val" style={{ fontSize: 16 }}>{camp.min_threshold}</div><div className="ms-lbl">Min-report threshold</div></div>
        </div>
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="card">
          <div className="card-head"><div className="card-title2">Questions<span className="ann">{camp.questions.length}</span></div></div>
          <div className="card-body" style={{ paddingTop: 8 }}>
            {camp.questions.map((q, i) => (
              <div className="qblock" key={q.id}>
                <div className="qb-top"><span className="qb-num">Q{i + 1}</span><span className="qb-type code">{q.qtype.replace("_", " ")}</span>{q.driver_code && <span className="qb-type code">{q.driver_code}</span>}</div>
                <div className="qb-q">{q.text}</div>
                <ScaleViz scale={q.scale} qtype={q.qtype} />
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card-head"><div className="card-title2">Results<span className="ann">{results?.suppressed ? "confidential" : "favorable %"}</span></div></div>
          <div className="card-body">
            <ResultsPanel results={results} responded={camp.responded} />
          </div>
        </div>
      </div>
    </div>
  );
}

function ResultsPanel({ results, responded }: { results: SurveyResults | null; responded: number }) {
  if (responded === 0) {
    return (
      <div className="svy-empty">
        <Icon name="survey" size={26} style={{ opacity: 0.4 }} />
        <div>No responses yet</div>
        <span>Results appear once people respond</span>
      </div>
    );
  }
  if (!results) return (
    <div className="sk-stagger" style={{ display: "grid", gap: 16, padding: "4px 0" }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} style={{ display: "grid", gap: 7 }}>
          <Skeleton className="sk-line sm" w={`${48 + (i % 3) * 12}%`} h={10} />
          <Skeleton className="sk-line" w="100%" h={10} r={5} />
        </div>
      ))}
    </div>
  );
  if (results.suppressed) {
    return (
      <div className="nodata">
        <Icon name="lock" />
        <span>Results are <b>hidden to protect confidentiality</b> — only {results.respondents} of a minimum
          {" "}{results.min_threshold} required respondents. Individual responses are never shown.</span>
      </div>
    );
  }
  return (
    <>
      <div className="svy-score-row">
        {results.engagement_score != null && (
          <div className="svy-score"><div className="svy-score-v" style={{ color: scoreColor(results.engagement_score) }}>{results.engagement_score}</div><div className="svy-score-l">Engagement</div></div>
        )}
        {results.enps_score != null && (
          <div className="svy-score"><div className="svy-score-v">{results.enps_score > 0 ? "+" : ""}{results.enps_score}</div><div className="svy-score-l">eNPS</div></div>
        )}
        <div className="svy-score"><div className="svy-score-v">{results.respondents}</div><div className="svy-score-l">Respondents</div></div>
      </div>

      {results.drivers.length > 0 && (
        <div className="res-list" style={{ marginTop: 14 }}>
          {results.drivers.map((d) => (
            <div className="res-row" key={d.code}>
              <div className="res-q">{d.name}</div>
              <div className="res-bar-wrap">
                <div className="res-bar"><span style={{ width: `${d.score}%`, background: scoreColor(d.score) }} /></div>
                <span className="res-val" style={{ color: scoreColor(d.score) }}>{d.score}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {results.themes.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="prof-subhead">Open-text themes</div>
          <div className="codes">{results.themes.map((t) => <span className="code" key={t}>{t}</span>)}</div>
        </div>
      )}
      <div className="subtle" style={{ marginTop: 16, paddingTop: 13, borderTop: "1px solid var(--border)" }}>
        Aggregated &amp; confidential · {results.respondents.toLocaleString()} respondents · threshold {results.min_threshold}.
      </div>
    </>
  );
}

// ---- builder ----------------------------------------------------------------
function Builder({ onDone }: { onDone: (id: number | null) => void }) {
  const [templates, setTemplates] = useState<SurveyTemplate[] | null>(null);
  const [picked, setPicked] = useState<SurveyTemplateDetail | null>(null);
  const [divisions, setDivisions] = useState<{ id: number; name: string }[]>([]);
  const [title, setTitle] = useState("");
  const [division, setDivision] = useState("");
  const [threshold, setThreshold] = useState(5);
  const [anonymous, setAnonymous] = useState(false);
  const [launch, setLaunch] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.surveyTemplates().then(setTemplates).catch(() => setErr("Could not load templates."));
    api.divisions().then(setDivisions).catch(() => {});
  }, []);

  const choose = (t: SurveyTemplate) => {
    setErr(null);
    api.surveyTemplate(t.id).then((d) => { setPicked(d); setTitle(d.title); }).catch(() => setErr("Could not load template."));
  };

  const create = async () => {
    if (!picked || !title.trim()) return;
    setBusy(true); setErr(null);
    try {
      const isLifecycle = picked.type === "lifecycle";
      const trigger = picked.key.startsWith("onboarding") ? "hire" : picked.key === "exit_survey" ? "termination" : null;
      const body: CampaignCreate = {
        title: title.trim(), type: picked.type, template_id: picked.id,
        audience: division ? { division } : null, cadence: picked.cadence,
        trigger_event: isLifecycle ? trigger : null, min_threshold: threshold, anonymous,
      };
      const created = await api.createCampaign(body);
      if (launch) await api.launchCampaign(created.id);
      onDone(created.id);
    } catch {
      setErr("Could not create the campaign.");
      setBusy(false);
    }
  };

  if (err && !templates) return <div className="page"><p className="flash err">{err}</p></div>;
  if (!templates) return (
    <div className="page">
      <SkeletonHead />
      <div className="svy-build">
        <div className="card">
          <div className="card-head"><Skeleton className="sk-line" w={118} h={14} /></div>
          <div className="card-body sk-stagger" style={{ display: "grid", gap: 0, paddingTop: 4 }}>
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} style={{ display: "grid", gap: 8, padding: "13px 0", borderBottom: i < 5 ? "1px solid var(--border)" : "none" }}>
                <Skeleton className="sk-line" w="44%" h={13} />
                <Skeleton className="sk-line sm" w="76%" h={10} />
                <Skeleton className="sk-line sm" w={118} h={9} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <button className="btn ghost" style={{ marginBottom: 10, paddingLeft: 6 }} onClick={() => onDone(null)}><Icon name="arrowleft" size={15} /> All surveys</button>
          <div className="page-title">New survey</div>
          <div className="page-sub"><span>Start from a science-backed template, set the audience, and launch</span></div>
        </div>
      </div>

      {err && <p className="flash err">{err}</p>}

      <div className="svy-build">
        <div className="card">
          <div className="card-head"><div className="card-title2">Templates<span className="ann">{templates.length}</span></div></div>
          <div className="card-body" style={{ paddingTop: 8 }}>
            <div className="svy-tpl-list">
              {templates.map((t) => (
                <button key={t.id} className={`svy-tpl${picked?.id === t.id ? " on" : ""}`} onClick={() => choose(t)}>
                  <div className="svy-tpl-h">
                    <span className="svy-tpl-t">{t.title}</span>
                    <span className="svy-status-chip">{TYPE_LABEL[t.type] ?? t.type}</span>
                  </div>
                  <div className="svy-tpl-s">{t.description}</div>
                  <div className="svy-tpl-m">{t.item_count} questions{t.cadence ? ` · ${t.cadence}` : ""}</div>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="card">
          {!picked ? (
            <div className="card-body"><div className="svy-empty"><Icon name="survey" size={26} style={{ opacity: 0.4 }} /><div>Pick a template</div><span>Its questions and settings load here</span></div></div>
          ) : (
            <>
              <div className="card-head"><div className="card-title2">Configure &amp; review</div></div>
              <div className="card-body">
                <label className="svy-field">
                  <span>Title</span>
                  <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Q3 Engagement Pulse" />
                </label>
                <div className="svy-field-row">
                  <label className="svy-field">
                    <span>Audience</span>
                    <select value={division} onChange={(e) => setDivision(e.target.value)}>
                      <option value="">All divisions</option>
                      {divisions.map((d) => <option key={d.id} value={d.name}>{d.name}</option>)}
                    </select>
                  </label>
                  <label className="svy-field">
                    <span>Min-report threshold</span>
                    <input type="number" min={1} max={100} value={threshold}
                           onChange={(e) => setThreshold(Math.max(1, Number(e.target.value) || 1))} />
                  </label>
                </div>
                <div className="svy-toggles">
                  <label className="svy-check"><input type="checkbox" checked={anonymous} onChange={(e) => setAnonymous(e.target.checked)} /> Anonymous (no token link)</label>
                  <label className="svy-check"><input type="checkbox" checked={launch} onChange={(e) => setLaunch(e.target.checked)} /> Launch immediately</label>
                </div>

                <div className="prof-subhead">Questions ({picked.questions.length})</div>
                <div className="svy-q-preview">
                  {picked.questions.map((q, i) => (
                    <div className="svy-q-row" key={q.position}>
                      <span className="qb-num">Q{i + 1}</span>
                      <span className="svy-q-text">{q.text}</span>
                      <span className="qb-type code">{q.qtype.replace("_", " ")}</span>
                    </div>
                  ))}
                </div>

                <div className="svy-build-foot">
                  <span className="subtle">{picked.type === "lifecycle" ? "Lifecycle survey — enrols people on the matching HRIS event." : "Token-only · scoped · audited"}</span>
                  <button className="btn primary" disabled={busy || !title.trim()} onClick={create}>
                    <Icon name="check" size={15} /> {busy ? "Creating…" : launch ? "Create & launch" : "Create draft"}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- shared bits ------------------------------------------------------------
function ScaleViz({ scale, qtype }: { scale: string | null; qtype: string }) {
  if (qtype === "open_text") return <div className="qb-free"><Icon name="talk" size={13} /> Free-text response</div>;
  if (qtype === "enps") return <div className="qb-free"><Icon name="pulse" size={13} /> 0–10 likelihood to recommend</div>;
  const opts = scale === "FREQ5"
    ? ["Never", "Rarely", "Sometimes", "Often", "Always"]
    : ["Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly agree"];
  return (
    <div className="scale-viz">
      {opts.map((o, i) => (
        <div className={`sv-seg${i >= opts.length - 2 ? " fav" : ""}`} key={i}>
          <span className="sv-bar" /><span className="sv-lbl">{o}</span>
        </div>
      ))}
    </div>
  );
}

function Donut({ pct, size = 78, col = "var(--accent)", center }: { pct: number; size?: number; col?: string; center?: React.ReactNode }) {
  const sw = Math.round(size * 0.115), r = (size - sw) / 2, cx = size / 2, c = 2 * Math.PI * r;
  const target = c * (1 - pct / 100);
  const [off, setOff] = useState(c);
  useEffect(() => {
    const id = requestAnimationFrame(() => setOff(target));
    return () => cancelAnimationFrame(id);
  }, [target]);
  return (
    <div className="donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--inset)" strokeWidth={sw} />
        {pct === 0 ? (
          <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--border-2)" strokeWidth={sw} strokeDasharray="2 5" strokeLinecap="round" />
        ) : (
          <circle cx={cx} cy={cx} r={r} fill="none" stroke={col} strokeWidth={sw} strokeLinecap="round"
                  strokeDasharray={c.toFixed(1)} strokeDashoffset={off.toFixed(1)} transform={`rotate(-90 ${cx} ${cx})`}
                  style={{ transition: "stroke-dashoffset 0.75s cubic-bezier(0.22, 0.61, 0.36, 1)" }} />
        )}
      </svg>
      <div className="donut-center">{center ?? <>{pct}<span>%</span></>}</div>
    </div>
  );
}
