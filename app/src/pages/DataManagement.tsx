/**
 * Data Management — the operator-facing console for the people data layer.
 *
 * VIEWS show REAL synthetic data through the existing scoped + audited seams
 * (`api.employees`, `api.employee360`, the `/identity` boundary via NameTag).
 * ACTIONS (edit / add / upload / ingest / send) are mocked against local state
 * and clearly badged "demo — not persisted" — no backend endpoint is added here.
 *
 * Guardrails carried over unchanged: names render only through NameTag (tokens
 * never shown raw to the operator as identity), manager scope is enforced
 * server-side, and comp + bias-walled fields are shown locked, values never
 * fetched.
 */
import { useEffect, useMemo, useState } from "react";
import { api, Employee360, EmployeeRow } from "../api/client";
import { useAuth } from "../App";
import { NameTag } from "../components/NameTag";
import { Skeleton, SkeletonTable } from "../components/ui";
import { Icon } from "../components/icons";
import { DemoBadge, SampleBadge, LockTag, useToast, ToastHost } from "../components/demo";
import { DocumentImport } from "./DocumentImport";
import { Connectors } from "./Connectors";
import { riskColor, titleCase } from "../lib/format";

// ---- local skeleton helpers for the non-table DM panels ---------------------
function SkTiles({ n = 6 }: { n?: number }) {
  return (
    <div className="dm-conn-grid sk-stagger">
      {Array.from({ length: n }).map((_, i) => (
        <div className="dm-conn-tile" key={i} style={{ display: "grid", gap: 12 }}>
          <div className="row between">
            <Skeleton className="sk-circle" w={34} h={34} />
            <Skeleton className="sk-line sm" w={64} h={16} r={8} />
          </div>
          <Skeleton className="sk-line" w="64%" h={13} />
          <Skeleton className="sk-line sm" w="44%" h={9} />
        </div>
      ))}
    </div>
  );
}

function SkFormCard({ rows = 6, width }: { rows?: number; width?: number }) {
  return (
    <div className="card sk-stagger" style={{ padding: 18, maxWidth: width, display: "grid", gap: 16 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: "grid", gap: 7 }}>
          <Skeleton className="sk-line sm" w={`${30 + (i % 3) * 8}%`} h={10} />
          <Skeleton className="sk-line" w="100%" h={34} r={8} />
        </div>
      ))}
    </div>
  );
}

type Tab = "master" | "editor" | "add" | "docs" | "import" | "connectors" | "ingest" | "surveys";
const TABS: [Tab, string, string][] = [
  ["master", "Employees", "users"],
  ["editor", "Field Editor", "sliders"],
  ["add", "Add Data", "plus"],
  ["docs", "Documents", "file"],
  ["import", "Document Import", "path"],
  ["connectors", "Connectors", "globe"],
  ["ingest", "Re-ingestion", "download"],
  ["surveys", "Surveys", "survey"],
];

export function DataManagement() {
  const [tab, setTab] = useState<Tab>("master");
  const [editToken, setEditToken] = useState<string | null>(null);

  return (
    <ToastHost>
      <div className="page">
        <div className="page-head">
          <div>
            <div className="page-title">Data Management</div>
            <div className="page-sub">
              <span>The people-data console — views are live synthetic data; actions are mocked for the showcase</span>
              <span className="dot-sep" />
              <span className="mono">Access logged · governed by People Analytics</span>
            </div>
          </div>
        </div>

        <div className="dm-tabs">
          {TABS.map(([key, label, ic]) => (
            <button key={key} className={`dm-tab${tab === key ? " on" : ""}`} onClick={() => setTab(key)}>
              <Icon name={ic} size={15} /> {label}
            </button>
          ))}
        </div>

        {tab === "master" && <EmployeeMaster onEdit={(t) => { setEditToken(t); setTab("editor"); }} />}
        {tab === "editor" && <FieldEditor token={editToken} onToken={setEditToken} />}
        {tab === "add" && <AddData />}
        {tab === "docs" && <Documents />}
        {tab === "import" && <DocumentImport />}
        {tab === "connectors" && <Connectors />}
        {tab === "ingest" && <Reingestion />}
        {tab === "surveys" && <SurveyBuilder />}
      </div>
    </ToastHost>
  );
}

// ---- Feature 1: Employee master list (REAL data) ---------------------------
type SortKey = "role" | "level" | "division" | "tenure" | "comp" | "risk" | "value";

export function EmployeeMaster({ onEdit }: { onEdit: (token: string) => void }) {
  const [all, setAll] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [q, setQ] = useState("");
  const [div, setDiv] = useState("all");
  const [sort, setSort] = useState<SortKey>("risk");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    api.employees().then(setAll).catch(() => setErr(true));
  }, []);

  const divisions = useMemo(
    () => (all ? Array.from(new Set(all.map((r) => r.division))).sort() : []),
    [all]
  );

  const rows = useMemo(() => {
    if (!all) return [];
    const ql = q.trim().toLowerCase();
    const out = all.filter((r) => {
      if (div !== "all" && r.division !== div) return false;
      if (ql && !`${r.token} ${r.role} ${r.division} ${r.team} ${r.location}`.toLowerCase().includes(ql)) return false;
      return true;
    });
    const val = (r: EmployeeRow): number | string => {
      switch (sort) {
        case "role": return r.role;
        case "level": return r.level;
        case "division": return r.division;
        case "tenure": return r.features?.tenure_months ?? -1;
        case "comp": return r.features?.comp_gap ?? Number.NEGATIVE_INFINITY;
        case "risk": return r.latest_score?.flight_risk ?? -1;
        case "value": return r.latest_score?.value_score ?? -1;
      }
    };
    return out.sort((a, b) => {
      const av = val(a), bv = val(b);
      if (typeof av === "string" && typeof bv === "string") {
        const r = av.localeCompare(bv);
        return dir === "asc" ? r : -r;
      }
      return dir === "asc" ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
  }, [all, div, q, sort, dir]);

  const onSort = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSort(k); setDir("desc"); }
  };

  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
        <div className="search" style={{ width: 260 }}>
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search roles, teams, divisions, locations…" />
        </div>
        <select className="chip-filter" value={div} onChange={(e) => setDiv(e.target.value)}
                style={{ appearance: "none", paddingRight: 26 }}>
          <option value="all">All divisions</option>
          {divisions.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <div className="spacer" />
        <span className="subtle">
          <b style={{ color: "var(--text)" }}>{rows.length}</b> employees in scope
        </span>
      </div>

      {err ? (
        <p className="flash err">Could not load employees.</p>
      ) : !all ? (
        <SkeletonTable rows={9} cols={5} widths={["60%", "52%", "30%", "46%", "50%"]} />
      ) : (
        <>
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Employee</th>
                    <Th k="role" sort={sort} dir={dir} onSort={onSort}>Role</Th>
                    <Th k="level" sort={sort} dir={dir} onSort={onSort} num>Level</Th>
                    <Th k="division" sort={sort} dir={dir} onSort={onSort}>Division</Th>
                    <th>Manager</th>
                    <Th k="comp" sort={sort} dir={dir} onSort={onSort} num>Comp Gap</Th>
                    <Th k="tenure" sort={sort} dir={dir} onSort={onSort} num>Tenure</Th>
                    <Th k="risk" sort={sort} dir={dir} onSort={onSort} num>Flight Risk</Th>
                    <Th k="value" sort={sort} dir={dir} onSort={onSort} num>Value</Th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const risk = r.latest_score?.flight_risk ?? null;
                    const value = r.latest_score?.value_score ?? null;
                    const comp = r.features?.comp_gap ?? null;
                    return (
                      <tr key={r.token} onClick={() => { window.location.hash = `#/profile/${r.token}`; }}>
                        <td>
                          <div className="emp-cell">
                            <div>
                              <div className="emp-name"><NameTag token={r.token} /></div>
                              <div className="emp-meta">{r.team} <span className="emp-id">{r.token}</span></div>
                            </div>
                          </div>
                        </td>
                        <td><span className="subtle" style={{ color: "var(--text-2)" }}>{r.role}</span></td>
                        <td className="td-num mono">L{r.level}</td>
                        <td><span className="subtle" style={{ color: "var(--text-2)" }}>{r.division}</span></td>
                        <td>
                          {r.manager_token
                            ? <span className="subtle"><NameTag token={r.manager_token} /></span>
                            : <span className="subtle">—</span>}
                        </td>
                        <td className="td-num mono" style={{ color: comp != null && comp > 0 ? "var(--risk-high)" : "var(--text-2)" }}>
                          {comp == null ? "—" : `${Math.round(comp * 100)}%`}
                        </td>
                        <td className="td-num mono" style={{ color: "var(--text-2)" }}>
                          {r.features ? `${(r.features.tenure_months / 12).toFixed(1)}y` : "—"}
                        </td>
                        <td className="td-num">
                          {risk == null ? <span className="code">no score</span> : (
                            <div className="scorebar">
                              <span className="track"><span className="fill" style={{ width: `${risk}%`, background: riskColor(risk) }} /></span>
                              <span className="sv" style={{ color: riskColor(risk) }}>{risk}</span>
                            </div>
                          )}
                        </td>
                        <td className="td-num mono" style={{ color: "var(--text-2)" }}>
                          {value == null ? "—" : value}
                        </td>
                        <td className="td-num">
                          <button className="dm-rowbtn" title="Open in field editor"
                                  onClick={(e) => { e.stopPropagation(); onEdit(r.token); }}>
                            <Icon name="sliders" size={13} /> Edit
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {!rows.length && (
                    <tr><td colSpan={10} className="subtle" style={{ padding: 18 }}>No employees match this filter.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="subtle" style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="shield" size={13} /> Live synthetic records · names resolved through the audited identity boundary · row → full profile.
          </div>
        </>
      )}
    </>
  );
}

function Th({ k, sort, dir, onSort, num, children }: {
  k: SortKey; sort: SortKey; dir: "asc" | "desc"; onSort: (k: SortKey) => void; num?: boolean; children: React.ReactNode;
}) {
  const active = sort === k;
  return (
    <th className={`${num ? "num " : ""}sortable`} onClick={() => onSort(k)}>
      <span className="th-in">
        {children}
        {active
          ? <Icon name={dir === "desc" ? "arrowdown" : "arrowup"} size={12} style={{ color: "var(--accent)" }} />
          : <Icon name="sort" size={12} style={{ opacity: 0.35 }} />}
      </span>
    </th>
  );
}

// ---- Feature 2: field editor + mocked audit panel --------------------------
// VIEW is real (employee360 + the in-scope features block). EDITS are local-only
// and badged "demo — not persisted"; every saved change appends a mocked audit
// row (who/what/when). Comp + bias-walled fields are shown locked — their values
// are never fetched or rendered, only acknowledged as masked.
type Group = "core" | "comp" | "career" | "skills" | "engagement" | "lifecycle" | "protected";
const GROUPS: [Group, string, string][] = [
  ["core", "Core record", "users"],
  ["comp", "Compensation", "comp"],
  ["career", "Career & mobility", "path"],
  ["skills", "Skills & performance", "award"],
  ["engagement", "Engagement", "pulse"],
  ["lifecycle", "Lifecycle", "clock"],
  ["protected", "Protected attributes", "lock"],
];

interface FieldDef {
  key: string; label: string; group: Group;
  kind: "text" | "number" | "select" | "date";
  opts?: string[]; hint?: string;
}
const FIELDS: FieldDef[] = [
  { key: "role", label: "Role", group: "core", kind: "text" },
  { key: "level", label: "Level", group: "core", kind: "number" },
  { key: "team", label: "Team", group: "core", kind: "text" },
  { key: "location", label: "Location", group: "core", kind: "text" },
  { key: "employment_type", label: "Employment type", group: "core", kind: "select",
    opts: ["full_time", "part_time", "contractor", "intern"] },
  { key: "comp_gap", label: "Comp gap vs. market", group: "comp", kind: "number", hint: "model feature · % below midpoint" },
  { key: "months_since_promotion", label: "Months since promotion", group: "career", kind: "number" },
  { key: "internal_moves", label: "Internal moves", group: "career", kind: "number" },
  { key: "span_of_control", label: "Span of control", group: "career", kind: "number" },
  { key: "perf_rating", label: "Performance rating", group: "skills", kind: "number", hint: "0–5" },
  { key: "learning_hours_12mo", label: "Learning hours (12mo)", group: "skills", kind: "number" },
  { key: "engagement_pulse", label: "Engagement pulse", group: "engagement", kind: "number", hint: "0–100 · consent-gated" },
  { key: "hire_date", label: "Hire date", group: "lifecycle", kind: "date" },
];
// Sensitive groups/fields rendered locked — value never shown, never editable.
const LOCKED_COMP = ["Base salary", "Pay band", "Pay percentile", "Equity / deferred"];
const LOCKED_PROTECTED: [string, string][] = [
  ["Gender", "bias-audit walled — used only in the audited fairness path, never in scoring or this editor"],
  ["Birth-year band", "bias-audit walled — age proxy, excluded from scoring features"],
  ["Marital status", "bias-audit walled — excluded from scoring features"],
];

interface AuditEntry { id: number; who: string; field: string; from: string; to: string; when: string; }

function initDraft(emp: Employee360, feat: EmployeeRow["features"]): Record<string, string> {
  const a = emp.attributes;
  const g = (v: unknown) => (v == null ? "" : String(v));
  return {
    role: g(emp.role), level: g(emp.level), team: g(emp.team), location: g(emp.location),
    employment_type: g(emp.employment_type), hire_date: g(emp.hire_date),
    comp_gap: feat ? String(Math.round(feat.comp_gap * 100)) : "",
    months_since_promotion: g(feat?.months_since_promotion),
    internal_moves: g(a?.internal_moves), span_of_control: g(a?.span_of_control),
    perf_rating: g(a?.perf_rating), learning_hours_12mo: g(a?.learning_hours_12mo),
    engagement_pulse: g(a?.engagement_pulse),
  };
}

function FieldEditor({ token, onToken }: { token: string | null; onToken: (t: string | null) => void }) {
  const { me } = useAuth();
  const { push } = useToast();
  const [emp, setEmp] = useState<Employee360 | null>(null);
  const [base, setBase] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [log, setLog] = useState<AuditEntry[]>([]);
  const [err, setErr] = useState(false);

  useEffect(() => {
    if (!token) { setEmp(null); return; }
    setEmp(null); setErr(false); setLog([]);
    api.employee360(token).then((e) => {
      api.employees().then((list) => {
        const f = list.find((r) => r.token === token)?.features ?? null;
        const d = initDraft(e, f);
        setBase(d); setDraft(d); setEmp(e);
      }).catch(() => setErr(true));
    }).catch(() => setErr(true));
  }, [token]);

  const consentGated = emp?.attributes?.consented_signals === 0;
  const dirtyKeys = useMemo(
    () => Object.keys(draft).filter((k) => draft[k] !== base[k]),
    [draft, base]
  );

  const save = () => {
    if (!dirtyKeys.length) return;
    const now = new Date().toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const entries: AuditEntry[] = dirtyKeys.map((k, i) => ({
      id: Date.now() + i, who: me.name,
      field: FIELDS.find((f) => f.key === k)?.label ?? k,
      from: base[k] || "—", to: draft[k] || "—", when: now,
    }));
    setLog((l) => [...entries.reverse(), ...l]);
    setBase({ ...draft });
    push(`${entries.length} field change${entries.length > 1 ? "s" : ""} saved (local only)`, "demo");
  };

  if (!token) return <EditorPicker onPick={onToken} />;
  if (err) return <p className="flash err">Could not load this employee, or they are out of your scope.</p>;
  if (!emp) return <SkFormCard rows={6} />;

  return (
    <div className="dm-editor">
      <div className="dm-editor-main">
        <div className="card dm-emp-head">
          <div className="emp-cell">
            <div className="avatar" style={{ width: 38, height: 38 }}><Icon name="profile" size={20} /></div>
            <div>
              <div className="emp-name" style={{ fontSize: 15 }}><NameTag token={emp.token} /></div>
              <div className="emp-meta">{emp.role} · {emp.division} · {emp.team} <span className="emp-id">{emp.token}</span></div>
            </div>
          </div>
          <div className="spacer" />
          <button className="btn ghost" onClick={() => onToken(null)}><Icon name="arrowleft" size={14} /> Change</button>
          <a className="btn ghost" href={`#/profile/${emp.token}`}><Icon name="eye" size={14} /> Profile</a>
        </div>

        {GROUPS.map(([g, label, ic]) => {
          const fields = FIELDS.filter((f) => f.group === g);
          if (g !== "comp" && g !== "protected" && !fields.length) return null;
          return (
            <div className="card dm-group" key={g}>
              <div className="card-head">
                <div className="card-title"><Icon name={ic} size={15} style={{ color: "var(--text-3)" }} /> {label}</div>
                {(g === "comp" || g === "protected") && <LockTag reason="sensitive — values masked in this console" />}
              </div>
              <div className="dm-fields">
                {fields.map((f) => {
                  const gated = f.key === "engagement_pulse" && consentGated;
                  return (
                    <label className="dm-field" key={f.key}>
                      <span className="dm-field-l">{f.label}{f.hint && <span className="dm-field-hint">{f.hint}</span>}</span>
                      {gated ? (
                        <span className="dm-field-gated">not consented — no covert monitoring</span>
                      ) : f.kind === "select" ? (
                        <select value={draft[f.key] ?? ""} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}>
                          {f.opts!.map((o) => <option key={o} value={o}>{titleCase(o)}</option>)}
                        </select>
                      ) : (
                        <input type={f.kind === "number" ? "number" : f.kind === "date" ? "date" : "text"}
                               value={draft[f.key] ?? ""}
                               onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
                      )}
                      {draft[f.key] !== base[f.key] && !gated && <span className="dm-field-dirty" title="unsaved change" />}
                    </label>
                  );
                })}
                {g === "comp" && LOCKED_COMP.map((l) => (
                  <div className="dm-field locked" key={l}>
                    <span className="dm-field-l">{l}</span>
                    <span className="dm-field-mask">•••••• masked</span>
                  </div>
                ))}
                {g === "protected" && LOCKED_PROTECTED.map(([l, why]) => (
                  <div className="dm-field locked" key={l} title={why}>
                    <span className="dm-field-l">{l}</span>
                    <span className="dm-field-mask">•••••• walled</span>
                  </div>
                ))}
                {g === "skills" && (
                  <div className="dm-skills">
                    {emp.skills.length ? emp.skills.map((sk) => (
                      <span className="code" key={sk.name}>{sk.name} <span className="mono" style={{ color: "var(--text-4)" }}>{sk.proficiency}/5</span></span>
                    )) : <span className="subtle">No recorded skills.</span>}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* audit / change-log side panel */}
      <div className="dm-editor-side">
        <div className="card dm-audit">
          <div className="card-head">
            <div className="card-title"><Icon name="shield" size={15} style={{ color: "var(--text-3)" }} /> Change log</div>
            <DemoBadge small />
          </div>
          <div className="dm-audit-body">
            {log.length === 0
              ? <p className="subtle" style={{ margin: 0 }}>No changes yet. Edits you save appear here with who / what / when — mocked for the showcase.</p>
              : log.map((e) => (
                <div className="dm-audit-row" key={e.id}>
                  <div className="dm-audit-top"><b>{e.field}</b><span className="dm-audit-when">{e.when}</span></div>
                  <div className="dm-audit-diff"><span className="dm-from">{e.from}</span><Icon name="chevright" size={11} /><span className="dm-to">{e.to}</span></div>
                  <div className="dm-audit-who">by {e.who}</div>
                </div>
              ))}
          </div>
        </div>
      </div>

      {/* sticky save bar */}
      <div className={`dm-savebar${dirtyKeys.length ? " on" : ""}`}>
        <span className="subtle">{dirtyKeys.length} unsaved change{dirtyKeys.length === 1 ? "" : "s"}</span>
        <DemoBadge small />
        <div className="spacer" />
        <button className="btn ghost" disabled={!dirtyKeys.length} onClick={() => setDraft({ ...base })}>Discard</button>
        <button className="btn" disabled={!dirtyKeys.length} onClick={save}><Icon name="check" size={14} /> Save changes</button>
      </div>
    </div>
  );
}

// Compact searchable employee chooser for the editor tab (real list + NameTag).
function EditorPicker({ onPick }: { onPick: (token: string) => void }) {
  const [all, setAll] = useState<EmployeeRow[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { api.employees().then(setAll).catch(() => setAll([])); }, []);
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (all ?? []).filter((r) => !ql || `${r.token} ${r.role} ${r.division} ${r.team}`.toLowerCase().includes(ql)).slice(0, 40);
  }, [all, q]);
  if (!all) return <SkFormCard rows={5} width={560} />;
  return (
    <div className="card" style={{ padding: 18, maxWidth: 560 }}>
      <div className="card-title" style={{ marginBottom: 12 }}><Icon name="sliders" size={15} /> Pick an employee to edit</div>
      <div className="search" style={{ width: "100%", marginBottom: 12 }}>
        <Icon name="search" size={15} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by role, team, division…" autoFocus />
      </div>
      <div className="dm-pick-list">
        {rows.map((r) => (
          <button className="dm-pick-row" key={r.token} onClick={() => onPick(r.token)}>
            <span className="emp-name"><NameTag token={r.token} /></span>
            <span className="emp-meta">{r.role} · {r.division}</span>
          </button>
        ))}
        {!rows.length && <p className="subtle" style={{ padding: 10 }}>No matches.</p>}
      </div>
    </div>
  );
}

// ---- Feature 3: add data (vacation / comp change / promotion) --------------
// Mocked write flows: full forms with validation + confirmation, saved to local
// state and badged "demo — not persisted". No backend write. The target employee
// is chosen through the audited list (token-only; name via NameTag).
type RecordKind = "vacation" | "comp" | "promotion";
const KINDS: [RecordKind, string, string][] = [
  ["vacation", "Vacation / PTO", "clock"],
  ["comp", "Compensation change", "comp"],
  ["promotion", "Promotion", "arrowup"],
];

interface FormField { key: string; label: string; kind: "text" | "number" | "date" | "select"; opts?: string[]; required?: boolean; }
const FORMS: Record<RecordKind, FormField[]> = {
  vacation: [
    { key: "type", label: "Leave type", kind: "select", opts: ["PTO", "Sick", "Unpaid", "Parental"], required: true },
    { key: "start", label: "Start date", kind: "date", required: true },
    { key: "end", label: "End date", kind: "date", required: true },
    { key: "note", label: "Note (optional)", kind: "text" },
  ],
  comp: [
    { key: "reason", label: "Change reason", kind: "select", opts: ["Merit", "Market adjustment", "Promotion", "Retention"], required: true },
    { key: "effective", label: "Effective date", kind: "date", required: true },
    { key: "percent", label: "Increase (%)", kind: "number", required: true },
    { key: "note", label: "Note (optional)", kind: "text" },
  ],
  promotion: [
    { key: "new_title", label: "New title", kind: "text", required: true },
    { key: "new_level", label: "New level", kind: "number", required: true },
    { key: "effective", label: "Effective date", kind: "date", required: true },
    { key: "note", label: "Note (optional)", kind: "text" },
  ],
};

interface AddedRecord { id: number; kind: RecordKind; token: string; summary: string; when: string; }

function AddData() {
  const { push } = useToast();
  const [kind, setKind] = useState<RecordKind>("vacation");
  const [token, setToken] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [added, setAdded] = useState<AddedRecord[]>([]);

  const reset = () => { setForm({}); setErrors({}); };
  const pick = (t: string) => { setToken(t); reset(); };

  const validate = (): boolean => {
    const e: Record<string, string> = {};
    for (const f of FORMS[kind]) {
      if (f.required && !(form[f.key] ?? "").trim()) e[f.key] = "Required";
    }
    if (kind === "vacation" && form.start && form.end && form.end < form.start) e.end = "End is before start";
    if (kind === "comp" && form.percent && Number(form.percent) <= 0) e.percent = "Must be greater than 0";
    if (kind === "promotion" && form.new_level && Number(form.new_level) < 1) e.new_level = "Invalid level";
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const summarize = (): string => {
    if (kind === "vacation") return `${form.type} leave ${form.start} → ${form.end}`;
    if (kind === "comp") return `${form.reason} +${form.percent}% effective ${form.effective}`;
    return `Promoted to ${form.new_title} (L${form.new_level}) effective ${form.effective}`;
  };

  const submit = () => {
    if (!token || !validate()) return;
    const now = new Date().toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    setAdded((l) => [{ id: Date.now(), kind, token, summary: summarize(), when: now }, ...l]);
    push(`${KINDS.find((k) => k[0] === kind)![1]} record added (local only)`, "demo");
    reset();
  };

  return (
    <div className="dm-add">
      <div className="dm-add-form">
        <div className="seg" style={{ marginBottom: 14 }}>
          {KINDS.map(([k, label, ic]) => (
            <button key={k} className={kind === k ? "on" : ""} onClick={() => { setKind(k); reset(); }}>
              <Icon name={ic} size={14} /> {label}
            </button>
          ))}
        </div>

        {!token ? (
          <EditorPicker onPick={pick} />
        ) : (
          <div className="card" style={{ padding: 18 }}>
            <div className="dm-add-head">
              <div className="emp-cell">
                <div className="avatar" style={{ width: 32, height: 32, fontSize: 12 }}><Icon name="profile" size={16} /></div>
                <div>
                  <div className="emp-name"><NameTag token={token} /></div>
                  <div className="emp-meta"><span className="emp-id">{token}</span></div>
                </div>
              </div>
              <div className="spacer" />
              <button className="btn ghost" onClick={() => setToken(null)}><Icon name="arrowleft" size={14} /> Change person</button>
            </div>

            <div className="dm-fields" style={{ padding: "14px 0 4px" }}>
              {FORMS[kind].map((f) => (
                <label className="dm-field" key={f.key}>
                  <span className="dm-field-l">{f.label}{f.required && <span style={{ color: "var(--risk-high)" }}>*</span>}</span>
                  {f.kind === "select" ? (
                    <select value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                      <option value="">Select…</option>
                      {f.opts!.map((o) => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input type={f.kind === "number" ? "number" : f.kind === "date" ? "date" : "text"}
                           value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
                  )}
                  {errors[f.key] && <span className="dm-field-err">{errors[f.key]}</span>}
                </label>
              ))}
            </div>
            <div className="row" style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
              <DemoBadge />
              <div className="spacer" style={{ flex: 1 }} />
              <button className="btn ghost" onClick={reset}>Clear</button>
              <button className="btn" onClick={submit}><Icon name="plus" size={14} /> Add record</button>
            </div>
          </div>
        )}
      </div>

      <div className="dm-add-side">
        <div className="card dm-audit">
          <div className="card-head">
            <div className="card-title"><Icon name="file" size={15} style={{ color: "var(--text-3)" }} /> Recently added</div>
            <DemoBadge small />
          </div>
          <div className="dm-audit-body">
            {added.length === 0
              ? <p className="subtle" style={{ margin: 0 }}>Records you add appear here — mocked, never written to the backend.</p>
              : added.map((r) => (
                <div className="dm-audit-row" key={r.id}>
                  <div className="dm-audit-top"><b>{titleCase(r.kind)}</b><span className="dm-audit-when">{r.when}</span></div>
                  <div className="dm-add-sum">{r.summary}</div>
                  <div className="dm-audit-who">for <NameTag token={r.token} /></div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Feature 4: Document grid + mocked upload ------------------------------
// No document table exists in the backend, so the displayed grid is illustrative
// (SampleBadge) — but it hangs off REAL employee tokens so owners resolve through
// NameTag. Uploads are mocked into local state and badged "demo — not persisted".
type DocCategory = "contract" | "review" | "comp" | "identity" | "certification" | "policy";
const DOC_CATS: Record<DocCategory, { label: string; icon: string; color: string; sensitive?: boolean }> = {
  contract:      { label: "Contract",       icon: "file",   color: "var(--accent)" },
  review:        { label: "Performance",    icon: "report", color: "var(--risk-low)" },
  comp:          { label: "Compensation",   icon: "comp",   color: "var(--risk-med)", sensitive: true },
  identity:      { label: "ID / Work auth", icon: "shield", color: "var(--risk-high)", sensitive: true },
  certification: { label: "Certification",  icon: "award",  color: "var(--accent)" },
  policy:        { label: "Signed policy",  icon: "check",  color: "var(--text-3)" },
};
const DOC_ORDER: DocCategory[] = ["contract", "review", "comp", "identity", "certification", "policy"];

interface DocItem { id: string; token: string; cat: DocCategory; title: string; date: string; size: string; uploaded?: boolean; }

// Deterministic illustrative filenames per category (no fabricated employee data —
// these are document *labels*, not analytics values).
const DOC_TITLES: Record<DocCategory, string[]> = {
  contract: ["Employment agreement", "Offer letter", "NDA addendum"],
  review: ["FY25 performance review", "Mid-year check-in", "360 feedback summary"],
  comp: ["Compensation letter", "Equity grant notice", "Bonus statement"],
  identity: ["Work authorization", "Government ID scan", "Right-to-work form"],
  certification: ["CFA Level II", "AWS Solutions Architect", "Series 7 license"],
  policy: ["Code of conduct", "Data handling policy", "Trading restrictions ack."],
};

function seededDocs(rows: EmployeeRow[]): DocItem[] {
  const docs: DocItem[] = [];
  rows.slice(0, 14).forEach((r, i) => {
    // 1–2 sample documents per employee, category cycled deterministically.
    const cats: DocCategory[] = [DOC_ORDER[i % DOC_ORDER.length], DOC_ORDER[(i + 3) % DOC_ORDER.length]];
    cats.slice(0, (i % 2) + 1).forEach((cat, j) => {
      const titles = DOC_TITLES[cat];
      const months = (i * 2 + j * 5) % 18;
      const d = new Date(2026, 5 - (months % 6), 1 + ((i + j) % 27));
      docs.push({
        id: `${r.token}-${cat}-${j}`,
        token: r.token,
        cat,
        title: titles[(i + j) % titles.length],
        date: d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }),
        size: `${((i % 5) + 1) * 0.4 + j * 0.3 + 0.2}`.slice(0, 3) + " MB",
      });
    });
  });
  return docs;
}

function Documents() {
  const { push } = useToast();
  const [rows, setRows] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [uploads, setUploads] = useState<DocItem[]>([]);
  const [filter, setFilter] = useState<DocCategory | "all">("all");
  const [dragging, setDragging] = useState(false);
  const [upCat, setUpCat] = useState<DocCategory>("contract");
  const [upToken, setUpToken] = useState<string | null>(null);

  useEffect(() => {
    api.employees().then(setRows).catch(() => setErr(true));
  }, []);

  const sample = useMemo(() => (rows ? seededDocs(rows) : []), [rows]);
  const all = useMemo(() => [...uploads, ...sample], [uploads, sample]);
  const shown = filter === "all" ? all : all.filter((d) => d.cat === filter);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const d of all) c[d.cat] = (c[d.cat] ?? 0) + 1;
    return c;
  }, [all]);

  const addUpload = (name: string) => {
    if (!upToken) { push("Pick an owner before uploading", "err"); return; }
    const clean = name.replace(/\.[a-z0-9]+$/i, "") || "Untitled document";
    setUploads((u) => [{
      id: `up-${Date.now()}`,
      token: upToken,
      cat: upCat,
      title: clean,
      date: new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }),
      size: `${(Math.random() * 2 + 0.3).toFixed(1)} MB`,
      uploaded: true,
    }, ...u]);
    push(`“${clean}” uploaded to ${DOC_CATS[upCat].label} (local only)`, "demo");
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    addUpload(f ? f.name : "Dropped file");
  };

  if (err) return <div className="card" style={{ padding: 22 }}>Couldn’t load employees for the document grid.</div>;
  if (!rows) return <SkTiles n={6} />;

  return (
    <div className="dm-docs">
      <div className="dm-docs-main">
        <div className="dm-doc-filters">
          <button className={`dm-chip${filter === "all" ? " on" : ""}`} onClick={() => setFilter("all")}>
            All <span className="dm-chip-n">{all.length}</span>
          </button>
          {DOC_ORDER.map((c) => (
            <button key={c} className={`dm-chip${filter === c ? " on" : ""}`} onClick={() => setFilter(c)}>
              <Icon name={DOC_CATS[c].icon} size={13} style={{ color: DOC_CATS[c].color }} /> {DOC_CATS[c].label}
              <span className="dm-chip-n">{counts[c] ?? 0}</span>
            </button>
          ))}
          <div className="spacer" style={{ flex: 1 }} />
          <SampleBadge />
        </div>

        <div className="dm-doc-grid">
          {shown.map((d) => {
            const cat = DOC_CATS[d.cat];
            return (
              <div className="dm-doc-card" key={d.id}>
                <div className="dm-doc-ico" style={{ background: `color-mix(in srgb, ${cat.color} 14%, transparent)`, color: cat.color }}>
                  <Icon name={cat.icon} size={18} />
                </div>
                <div className="dm-doc-body">
                  <div className="dm-doc-title" title={d.title}>{d.title}</div>
                  <div className="dm-doc-meta">
                    <NameTag token={d.token} /> <span className="dot-sep" /> {d.date} <span className="dot-sep" /> {d.size}
                  </div>
                  <div className="dm-doc-tags">
                    <span className="dm-doc-tag" style={{ color: cat.color }}>{cat.label}</span>
                    {cat.sensitive && <LockTag reason="Sensitive document — preview gated, value never fetched" />}
                    {d.uploaded ? <DemoBadge small /> : null}
                  </div>
                </div>
                <button className="dm-rowbtn" title={cat.sensitive ? "Locked" : "Preview (demo)"} disabled={cat.sensitive}
                        onClick={() => push(`Opening “${d.title}” preview (local only)`, "demo")}>
                  <Icon name="eye" size={15} />
                </button>
              </div>
            );
          })}
          {shown.length === 0 && <p className="subtle" style={{ padding: 18 }}>No documents in this category.</p>}
        </div>
      </div>

      <div className="dm-docs-side">
        <div className="card" style={{ padding: 16 }}>
          <div className="card-head" style={{ padding: 0, marginBottom: 12 }}>
            <div className="card-title"><Icon name="download" size={15} style={{ color: "var(--text-3)" }} /> Upload document</div>
            <DemoBadge small />
          </div>

          <label className="dm-field" style={{ marginBottom: 10 }}>
            <span className="dm-field-l">Owner</span>
            <select value={upToken ?? ""} onChange={(e) => setUpToken(e.target.value || null)}>
              <option value="">Select employee…</option>
              {rows.map((r) => <option key={r.token} value={r.token}>{r.role} · {titleCase(r.division)}</option>)}
            </select>
            {upToken && <span className="dm-field-hint"><NameTag token={upToken} /></span>}
          </label>

          <label className="dm-field" style={{ marginBottom: 12 }}>
            <span className="dm-field-l">Category</span>
            <select value={upCat} onChange={(e) => setUpCat(e.target.value as DocCategory)}>
              {DOC_ORDER.map((c) => <option key={c} value={c}>{DOC_CATS[c].label}</option>)}
            </select>
          </label>

          <div className={`dm-dropzone${dragging ? " over" : ""}`}
               onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
               onDragLeave={() => setDragging(false)}
               onDrop={onDrop}>
            <Icon name="download" size={22} style={{ color: "var(--text-4)" }} />
            <div className="dm-drop-text">Drag a file here, or
              <button className="dm-drop-browse" onClick={() => addUpload("Browsed document.pdf")}>browse</button>
            </div>
            <div className="dm-drop-sub">PDF, DOCX, PNG · upload is mocked, nothing leaves the browser</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---- Feature 5: Re-ingestion + change-preview diff -------------------------
// Connector tiles and file upload are mocked. Running a source builds a
// change-preview diff against REAL employee tokens (names via NameTag) — the
// proposed field changes are illustrative, and Apply only mutates local state.
// Bias-walled fields (gender / age band / comp values) never appear in a diff.
interface Connector { id: string; name: string; kind: string; icon: string; status: "connected" | "available"; last: string; }
const CONNECTORS: Connector[] = [
  { id: "workday",    name: "Workday",      kind: "Core HRIS",          icon: "globe",    status: "connected", last: "2 days ago" },
  { id: "bamboo",     name: "BambooHR",     kind: "HRIS",               icon: "division", status: "available", last: "—" },
  { id: "greenhouse", name: "Greenhouse",   kind: "ATS / recruiting",   icon: "users",    status: "available", last: "—" },
  { id: "peakon",     name: "Peakon",       kind: "Engagement survey",  icon: "survey",   status: "connected", last: "6 days ago" },
  { id: "csv",        name: "CSV / Excel",  kind: "Manual file",        icon: "file",     status: "available", last: "—" },
];

// Non-sensitive fields only — comp values and bias-walled attributes are excluded by design.
const DIFF_FIELDS: { key: string; label: string; next: (r: EmployeeRow) => string; from: (r: EmployeeRow) => string }[] = [
  { key: "title", label: "Title", from: (r) => r.role, next: (r) => `Senior ${r.role}` },
  { key: "level", label: "Level", from: (r) => `L${r.level}`, next: (r) => `L${r.level + 1}` },
  { key: "location", label: "Location", from: (r) => r.location, next: (r) => (r.location === "Remote" ? "London" : "Remote") },
  { key: "team", label: "Team", from: (r) => titleCase(r.team), next: (r) => `${titleCase(r.team)} (West)` },
  { key: "employment_type", label: "Employment", from: (r) => titleCase(r.employment_type), next: () => "Full-time" },
];

interface DiffRow { token: string; field: string; from: string; to: string; }
interface NewRow { id: string; label: string; role: string; division: string; }
interface ConflictRow { token: string; field: string; a: string; b: string; sourceA: string; sourceB: string; }
interface Preview { source: string; updated: DiffRow[]; created: NewRow[]; conflicts: ConflictRow[]; }

function buildPreview(rows: EmployeeRow[], source: string): Preview {
  const updated: DiffRow[] = rows.slice(0, 12).map((r, i) => {
    const f = DIFF_FIELDS[i % DIFF_FIELDS.length];
    return { token: r.token, field: f.label, from: f.from(r), to: f.next(r) };
  });
  const created: NewRow[] = rows.slice(20, 23).map((r, i) => ({
    id: `new-${i}`,
    label: "New hire — pending identity",
    role: r.role,
    division: titleCase(r.division),
  }));
  const conflicts: ConflictRow[] = rows.slice(5, 6).map((r) => ({
    token: r.token,
    field: "Manager",
    a: "Mateo Patel",
    b: "Dana Romano",
    sourceA: source,
    sourceB: "Current record",
  }));
  return { source, updated, created, conflicts };
}

function Reingestion() {
  const { push } = useToast();
  const [rows, setRows] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => { api.employees().then(setRows).catch(() => setErr(true)); }, []);

  const run = (source: string) => {
    if (!rows) return;
    setPreview(buildPreview(rows, source));
    push(`${source} dry-run complete — review the change preview`, "demo");
  };
  const apply = () => {
    if (!preview) return;
    const n = preview.updated.length + preview.created.length;
    push(`${n} records merged from ${preview.source} (local only)`, "demo");
    setPreview(null);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragging(false);
    const f = e.dataTransfer.files?.[0];
    run(f ? f.name : "Dropped file");
  };

  if (err) return <div className="card" style={{ padding: 22 }}>Couldn’t load employees for the change preview.</div>;
  if (!rows) return <SkTiles n={6} />;

  return (
    <div className="dm-ingest">
      <div className="dm-conn-grid">
        {CONNECTORS.map((c) => (
          <div className="dm-conn-tile" key={c.id}>
            <div className="dm-conn-top">
              <div className="dm-conn-ico"><Icon name={c.icon} size={18} /></div>
              <span className={`dm-conn-status ${c.status}`}>{c.status === "connected" ? "Connected" : "Available"}</span>
            </div>
            <div className="dm-conn-name">{c.name}</div>
            <div className="dm-conn-kind">{c.kind}</div>
            <div className="dm-conn-foot">
              <span className="dm-conn-last">Last sync · {c.last}</span>
              <button className="btn ghost sm" onClick={() => run(c.name)}>
                <Icon name="download" size={13} /> Dry-run
              </button>
            </div>
          </div>
        ))}
        <div className={`dm-conn-tile dm-conn-drop${dragging ? " over" : ""}`}
             onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
             onDragLeave={() => setDragging(false)} onDrop={onDrop}
             onClick={() => run("people_export.xlsx")}>
          <Icon name="download" size={20} style={{ color: "var(--text-4)" }} />
          <div className="dm-conn-name" style={{ marginTop: 6 }}>Upload export</div>
          <div className="dm-conn-kind">Drop a CSV / XLSX, or click to simulate</div>
        </div>
      </div>

      {preview && (
        <div className="card dm-preview">
          <div className="dm-preview-head">
            <div>
              <div className="card-title"><Icon name="path" size={15} style={{ color: "var(--text-3)" }} /> Change preview — {preview.source}</div>
              <div className="dm-preview-summary">
                <span className="dm-stat up">{preview.updated.length} updated</span>
                <span className="dm-stat new">{preview.created.length} new</span>
                <span className="dm-stat conf">{preview.conflicts.length} conflict{preview.conflicts.length === 1 ? "" : "s"}</span>
              </div>
            </div>
            <DemoBadge />
          </div>

          {preview.conflicts.length > 0 && (
            <div className="dm-preview-sec">
              <div className="dm-preview-sec-h conf"><Icon name="alert" size={13} /> Conflicts — resolve before applying</div>
              {preview.conflicts.map((c) => (
                <div className="dm-diff-row conflict" key={c.token + c.field}>
                  <div className="dm-diff-who"><NameTag token={c.token} /> <span className="dm-diff-field">{c.field}</span></div>
                  <div className="dm-conflict-opts">
                    <label className="dm-conflict-opt"><input type="radio" name={`cf-${c.token}`} defaultChecked /> {c.a} <span className="dm-diff-src">{c.sourceA}</span></label>
                    <label className="dm-conflict-opt"><input type="radio" name={`cf-${c.token}`} /> {c.b} <span className="dm-diff-src">{c.sourceB}</span></label>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="dm-preview-sec">
            <div className="dm-preview-sec-h up"><Icon name="sliders" size={13} /> Updated records</div>
            {preview.updated.map((d) => (
              <div className="dm-diff-row" key={d.token + d.field}>
                <div className="dm-diff-who"><NameTag token={d.token} /> <span className="dm-diff-field">{d.field}</span></div>
                <div className="dm-diff-change">
                  <span className="dm-diff-from">{d.from}</span>
                  <Icon name="arrowup" size={12} className="dm-diff-arrow" />
                  <span className="dm-diff-to">{d.to}</span>
                </div>
              </div>
            ))}
          </div>

          {preview.created.length > 0 && (
            <div className="dm-preview-sec">
              <div className="dm-preview-sec-h new"><Icon name="plus" size={13} /> New records</div>
              {preview.created.map((n) => (
                <div className="dm-diff-row" key={n.id}>
                  <div className="dm-diff-who">{n.label} <span className="dm-diff-field">{n.role} · {n.division}</span></div>
                  <span className="dm-diff-to">incoming</span>
                </div>
              ))}
            </div>
          )}

          <div className="dm-preview-foot">
            <DemoBadge />
            <div className="spacer" style={{ flex: 1 }} />
            <button className="btn ghost" onClick={() => setPreview(null)}>Cancel</button>
            <button className="btn" onClick={apply}><Icon name="check" size={14} /> Apply changes</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Feature 6: Survey builder + dispatch mock -----------------------------
// The audience counts are REAL (derived from api.employees by division). Building
// and dispatching a survey is mocked: no message is actually sent, dispatch only
// appends to a local "sent" list and is badged "demo — not persisted".
type QType = "scale" | "yesno" | "text";
const QTYPES: [QType, string][] = [["scale", "1–5 scale"], ["yesno", "Yes / No"], ["text", "Open text"]];
const CADENCES = ["One-time", "Weekly", "Monthly", "Quarterly"];
const STARTER_QS: { text: string; type: QType }[] = [
  { text: "How supported do you feel by your manager?", type: "scale" },
  { text: "Would you recommend Talent88 as a place to work?", type: "yesno" },
  { text: "What would most improve your day-to-day?", type: "text" },
];

interface Question { id: number; text: string; type: QType; }
interface SentSurvey { id: number; title: string; recipients: number; questions: number; cadence: string; when: string; }

function SurveyBuilder() {
  const { push } = useToast();
  const [rows, setRows] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [title, setTitle] = useState("Q3 Engagement Pulse");
  const [cadence, setCadence] = useState(CADENCES[2]);
  const [divs, setDivs] = useState<Set<string>>(new Set());
  const [questions, setQuestions] = useState<Question[]>(STARTER_QS.map((q, i) => ({ id: i, text: q.text, type: q.type })));
  const [newText, setNewText] = useState("");
  const [newType, setNewType] = useState<QType>("scale");
  const [sent, setSent] = useState<SentSurvey[]>([]);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => { api.employees().then(setRows).catch(() => setErr(true)); }, []);

  const divisions = useMemo(() => {
    if (!rows) return [] as string[];
    return Array.from(new Set(rows.map((r) => r.division))).sort();
  }, [rows]);
  const recipients = useMemo(() => {
    if (!rows) return 0;
    return divs.size === 0 ? rows.length : rows.filter((r) => divs.has(r.division)).length;
  }, [rows, divs]);

  const toggleDiv = (d: string) => setDivs((s) => { const n = new Set(s); n.has(d) ? n.delete(d) : n.add(d); return n; });
  const addQuestion = () => {
    if (!newText.trim()) return;
    setQuestions((q) => [...q, { id: Date.now(), text: newText.trim(), type: newType }]);
    setNewText("");
  };
  const removeQuestion = (id: number) => setQuestions((q) => q.filter((x) => x.id !== id));

  const canDispatch = title.trim() && questions.length > 0 && recipients > 0;
  const dispatch = () => {
    if (!canDispatch) return;
    const when = new Date().toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    setSent((l) => [{ id: Date.now(), title: title.trim(), recipients, questions: questions.length, cadence, when }, ...l]);
    push(`Survey dispatched to ${recipients} recipients (local only)`, "demo");
    setConfirming(false);
  };

  if (err) return <div className="card" style={{ padding: 22 }}>Couldn’t load employees for the audience selector.</div>;
  if (!rows) return <SkFormCard rows={5} />;

  return (
    <div className="dm-survey">
      <div className="dm-survey-main">
        <div className="card" style={{ padding: 18 }}>
          <label className="dm-field" style={{ marginBottom: 14 }}>
            <span className="dm-field-l">Survey title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Q3 Engagement Pulse" />
          </label>

          <div className="dm-field-l" style={{ marginBottom: 8 }}>Audience</div>
          <div className="dm-aud-chips">
            <button className={`dm-chip${divs.size === 0 ? " on" : ""}`} onClick={() => setDivs(new Set())}>
              Everyone <span className="dm-chip-n">{rows.length}</span>
            </button>
            {divisions.map((d) => (
              <button key={d} className={`dm-chip${divs.has(d) ? " on" : ""}`} onClick={() => toggleDiv(d)}>
                {titleCase(d)} <span className="dm-chip-n">{rows.filter((r) => r.division === d).length}</span>
              </button>
            ))}
          </div>

          <label className="dm-field" style={{ margin: "14px 0" }}>
            <span className="dm-field-l">Cadence</span>
            <select value={cadence} onChange={(e) => setCadence(e.target.value)}>
              {CADENCES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>

          <div className="dm-field-l" style={{ marginBottom: 8 }}>Questions <span className="dm-chip-n">{questions.length}</span></div>
          <div className="dm-q-list">
            {questions.map((q, i) => (
              <div className="dm-q-row" key={q.id}>
                <span className="dm-q-num">{i + 1}</span>
                <span className="dm-q-text">{q.text}</span>
                <span className="dm-q-type">{QTYPES.find((t) => t[0] === q.type)![1]}</span>
                <button className="dm-rowbtn" title="Remove" onClick={() => removeQuestion(q.id)}><Icon name="close" size={14} /></button>
              </div>
            ))}
          </div>
          <div className="dm-q-add">
            <input value={newText} onChange={(e) => setNewText(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && addQuestion()} placeholder="Add a question…" />
            <select value={newType} onChange={(e) => setNewType(e.target.value as QType)}>
              {QTYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <button className="btn ghost" onClick={addQuestion}><Icon name="plus" size={14} /> Add</button>
          </div>
        </div>
      </div>

      <div className="dm-survey-side">
        <div className="card dm-dispatch">
          <div className="card-head" style={{ padding: 0, marginBottom: 12 }}>
            <div className="card-title"><Icon name="send" size={15} style={{ color: "var(--text-3)" }} /> Dispatch</div>
            <DemoBadge small />
          </div>
          <div className="dm-dispatch-stat"><b>{recipients.toLocaleString()}</b> recipients<span>{divs.size === 0 ? "everyone" : `${divs.size} division${divs.size === 1 ? "" : "s"}`}</span></div>
          <div className="dm-dispatch-stat"><b>{questions.length}</b> questions<span>{cadence.toLowerCase()}</span></div>

          {!confirming ? (
            <button className="btn primary" style={{ width: "100%", marginTop: 12 }} disabled={!canDispatch}
                    onClick={() => setConfirming(true)}>
              <Icon name="send" size={14} /> Dispatch survey
            </button>
          ) : (
            <div className="dm-confirm">
              <p className="dm-confirm-q">Send “{title}” to <b>{recipients}</b> recipients?</p>
              <div className="dm-confirm-row">
                <button className="btn ghost" onClick={() => setConfirming(false)}>Cancel</button>
                <button className="btn primary" onClick={dispatch}><Icon name="check" size={14} /> Confirm send</button>
              </div>
              <DemoBadge small />
            </div>
          )}
          {!canDispatch && <p className="dm-field-err" style={{ marginTop: 8 }}>Add a title, at least one question, and a non-empty audience.</p>}
        </div>

        <div className="card dm-audit" style={{ marginTop: 14 }}>
          <div className="card-head">
            <div className="card-title"><Icon name="survey" size={15} style={{ color: "var(--text-3)" }} /> Dispatched</div>
            <DemoBadge small />
          </div>
          <div className="dm-audit-body">
            {sent.length === 0
              ? <p className="subtle" style={{ margin: 0 }}>Dispatched surveys appear here — mocked, nothing is actually sent.</p>
              : sent.map((s) => (
                <div className="dm-audit-row" key={s.id}>
                  <div className="dm-audit-top"><b>{s.title}</b><span className="dm-audit-when">{s.when}</span></div>
                  <div className="dm-add-sum">{s.recipients} recipients · {s.questions} questions · {s.cadence}</div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}
