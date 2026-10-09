/**
 * Document Import — REAL employee creation from ANY employee document.
 *
 * Upload a CV, an employment contract, an offer letter, a payslip, or a performance review
 * (PDF / Word / text). Each document is parsed on the server and its fields pre-fill the form
 * — you can add several documents for one person and they merge (a CV fills name/role/skills/
 * education/languages, a contract adds pay & start date, a review adds the rating). Then you
 * confirm, fill what no document carries (division, level, photo), and Save persists a
 * complete, real employee across every table we model.
 *
 * Everything beyond the required core (identity, role, base pay) is OPTIONAL — if you don't have
 * the document or the information for a field, leave it blank and continue. Records are real and
 * persisted: they appear immediately in the Employees list, the Profile, and the analytics.
 * Admin-only · audited. (Cloud-drive / email connectors are a separate, later surface.)
 */
import { useEffect, useRef, useState } from "react";
import {
  api, ApiError, CreatedEmployee, CvSuggestions, ImportDivision, NewEmployeeInput,
} from "../api/client";
import { Skeleton } from "../components/ui";
import { Icon } from "../components/icons";
import { useAuth } from "../App";

const EMPLOYMENT_TYPES = ["full_time", "part_time", "contractor"];
const STATUSES = ["active", "on_leave", "terminated"];
const TRAVEL = ["", "none", "occasional", "frequent"];
const CURRENCIES = ["USD", "GBP", "EUR", "SGD", "HKD", "JPY"];
const GENDERS = ["", "female", "male", "non_binary", "undisclosed"];
const MARITAL = ["", "single", "married", "divorced", "undisclosed"];
const EDU_LEVELS = ["", "high_school", "associate", "bachelor", "master", "doctorate"];
const EDU_FIELDS = ["", "life_sciences", "medical", "marketing", "technical_degree", "human_resources", "business", "other"];
const ONBOARDING = ["", "not_started", "in_progress", "complete"];

const DOC_TYPES: [string, string][] = [
  ["auto", "Auto-detect"], ["cv", "CV / résumé"], ["contract", "Employment contract"],
  ["offer", "Offer letter"], ["payslip", "Payslip"], ["review", "Performance review"],
];
const DOC_LABEL: Record<string, string> = Object.fromEntries(DOC_TYPES);
// What each document type can fill — shown so it's clear which paper gives which info.
const DOC_FILLS: Record<string, string> = {
  auto: "detects the type and pulls whatever it can",
  cv: "name · role · skills · education · university · languages · certifications · experience",
  contract: "title · salary · currency · start date · employment type",
  offer: "salary · currency · title · start date",
  payslip: "salary · currency",
  review: "performance rating · goal attainment",
};

const nice = (s: string) => s.replace(/_/g, " ");

type FormState = {
  full_name: string; email: string; role: string; title: string;
  level: string; division: string; team: string; location: string;
  employment_type: string; status: string; business_travel_frequency: string; hire_date: string;
  base_salary: string; currency: string; bonus: string; equity: string; pay_band: string; last_raise_date: string;
  years_of_experience: string; total_working_years: string; prior_employer_count: string; time_since_last_promotion: string;
  performance_rating: string; goal_attainment: string;
  birth_date: string; gender: string; marital_status: string;
  education_level: string; education_field: string; education_institution: string;
  skills: string; certifications: string; licenses: string; languages: string; hobbies: string;
  onboarding_status: string; credential_name: string; credential_expiry: string; distance_from_home: string;
  photo: string;
};

const EMPTY_FORM: FormState = {
  full_name: "", email: "", role: "", title: "", level: "", division: "", team: "", location: "",
  employment_type: "full_time", status: "active", business_travel_frequency: "", hire_date: "",
  base_salary: "", currency: "USD", bonus: "", equity: "", pay_band: "", last_raise_date: "",
  years_of_experience: "", total_working_years: "", prior_employer_count: "", time_since_last_promotion: "",
  performance_rating: "", goal_attainment: "",
  birth_date: "", gender: "", marital_status: "",
  education_level: "", education_field: "", education_institution: "",
  skills: "", certifications: "", licenses: "", languages: "", hobbies: "",
  onboarding_status: "", credential_name: "", credential_expiry: "", distance_from_home: "", photo: "",
};

// Scalar suggestion key -> form field. Lists (skills/languages/certifications) and goal_attainment
// are handled separately in the merge.
const SUGGEST_TO_FORM: Record<string, keyof FormState> = {
  full_name: "full_name", email: "email", role: "role", title: "title", location: "location",
  base_salary: "base_salary", currency: "currency", hire_date: "hire_date",
  employment_type: "employment_type", years_of_experience: "years_of_experience",
  total_working_years: "total_working_years", prior_employer_count: "prior_employer_count",
  performance_rating: "performance_rating", education_field: "education_field",
  education_institution: "education_institution",
};

export function ConfidencePill({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const level = value >= 0.85 ? "hi" : value >= 0.7 ? "mid" : "lo";
  return <span className={`di-conf ${level}`}>{pct}%</span>;
}

// Downscale a chosen image to a small square-ish JPEG data URL (keeps the stored PII tiny).
function downscaleImage(file: File, max = 256): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const w = Math.max(1, Math.round(img.width * scale));
      const h = Math.max(1, Math.round(img.height * scale));
      const c = document.createElement("canvas");
      c.width = w; c.height = h;
      const ctx = c.getContext("2d");
      URL.revokeObjectURL(url);
      if (!ctx) { reject(new Error("canvas unavailable")); return; }
      ctx.drawImage(img, 0, 0, w, h);
      resolve(c.toDataURL("image/jpeg", 0.82));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("could not read the image")); };
    img.src = url;
  });
}

export function DocumentImport() {
  const { me } = useAuth();
  const [divisions, setDivisions] = useState<ImportDivision[] | null>(null);
  const [suggestions, setSuggestions] = useState<CvSuggestions | null>(null);
  const [added, setAdded] = useState<CreatedEmployee[]>([]);

  const loadDivisions = () => { api.importDivisions().then(setDivisions).catch(() => setDivisions([])); };
  useEffect(loadDivisions, []);

  if (me.role !== "admin") {
    return (
      <div className="di2-wrap">
        <div className="card" style={{ padding: 28, textAlign: "center" }}>
          <Icon name="lock" size={20} style={{ color: "var(--text-4)" }} />
          <p className="subtle" style={{ marginTop: 8 }}>
            Adding employees is restricted to administrators. Ask an admin to import records.
          </p>
        </div>
      </div>
    );
  }

  if (!divisions) {
    return (
      <div className="di2-wrap">
        <Skeleton className="sk-chart" h={92} style={{ borderRadius: "var(--r-lg)" }} />
        <div className="di2-grid">
          <Skeleton className="sk-chart" h={300} style={{ borderRadius: "var(--r-lg)" }} />
          <Skeleton className="sk-chart" h={620} style={{ borderRadius: "var(--r-lg)" }} />
        </div>
      </div>
    );
  }

  return (
    <div className="di2-wrap">
      <div className="di2-intro card">
        <div className="di2-intro-ico"><Icon name="file" size={18} /></div>
        <div>
          <div className="di2-intro-title">Add an employee from their documents</div>
          <p className="di2-intro-sub">
            Upload any documents you have — a CV, contract, offer letter, payslip or review — and
            they pre-fill what they contain. Complete division, level and anything else you know;
            <b> every field beyond the basics is optional</b>, so save with whatever you have.
            Records are <b>real and persisted</b>. Admin-only · audited.
          </p>
        </div>
      </div>

      <div className="di2-grid">
        <div className="di2-col">
          <DocumentsCard onParsed={setSuggestions} />
          <SessionList added={added} />
        </div>
        <EmployeeForm
          divisions={divisions}
          suggestions={suggestions}
          onSaved={(emp) => { setAdded((a) => [emp, ...a]); loadDivisions(); }}
        />
      </div>
    </div>
  );
}

// ---- document upload + parse (multi) ---------------------------------------
interface UploadedDoc { name: string; type: string; filled: number; }

function DocumentsCard({ onParsed }: { onParsed: (s: CvSuggestions) => void }) {
  const [docType, setDocType] = useState("auto");
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [docs, setDocs] = useState<UploadedDoc[]>([]);
  const [last, setLast] = useState<CvSuggestions | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handle = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const res = await api.parseDocument(file, docType);
      const filled = Object.keys(res.fields).length + res.skills.length
        + res.languages.length + res.certifications.length;
      setDocs((d) => [{ name: res.filename || file.name, type: res.doc_type, filled }, ...d]);
      setLast(res);
      onParsed(res);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not read that file.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onDrop = (e: React.DragEvent) => { e.preventDefault(); setDragging(false); handle(e.dataTransfer.files?.[0]); };

  return (
    <div className="card di2-cv">
      <div className="card-head">
        <div className="card-title"><Icon name="file" size={15} /> Documents</div>
        <select className="di2-doctype" value={docType} onChange={(e) => setDocType(e.target.value)}
                title="What kind of document is this?">
          {DOC_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
      </div>
      <div className="card-body">
        <div className="di2-fills"><Icon name="eye" size={12} /> {DOC_LABEL[docType]} fills: <b>{DOC_FILLS[docType]}</b></div>
        <div
          className={`di-drop${dragging ? " over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          style={{ cursor: "pointer" }}
        >
          <Icon name={busy ? "clock" : "download"} size={24} style={{ color: "var(--text-4)" }} />
          <div className="di-drop-title">{busy ? "Reading…" : "Drop a document or click to choose"}</div>
          <div className="di-drop-sub">PDF, Word (.docx) or text — read on your server, never sent elsewhere. Add as many as you have.</div>
          <input ref={inputRef} type="file" accept=".pdf,.docx,.txt,.md,.text" hidden
                 onChange={(e) => handle(e.target.files?.[0] ?? undefined)} />
        </div>
        {err && <div className="di2-err"><Icon name="alert" size={13} /> {err}</div>}

        {docs.length > 0 && (
          <div className="di2-doclist">
            {docs.map((d, i) => (
              <div className="di2-docrow" key={i}>
                <Icon name="file" size={13} style={{ color: "var(--accent)" }} />
                <span className="di2-docname">{d.name}</span>
                <span className="di2-doctag">{DOC_LABEL[d.type] ?? d.type}</span>
                <span className="di2-docfilled">{d.filled} field{d.filled === 1 ? "" : "s"}</span>
              </div>
            ))}
          </div>
        )}
        {last && <CvSummary parsed={last} />}
      </div>
    </div>
  );
}

function CvSummary({ parsed }: { parsed: CvSuggestions }) {
  const f = parsed.fields;
  const show: [string, string][] = [
    ["Name", "full_name"], ["Email", "email"], ["Role", "role"], ["University", "education_institution"],
    ["Pay", "base_salary"], ["Hire date", "hire_date"], ["Rating", "performance_rating"],
  ];
  const rows = show.filter(([, k]) => f[k]);
  const chips = [...parsed.skills, ...parsed.languages, ...parsed.certifications];
  if (rows.length === 0 && chips.length === 0) return null;
  return (
    <div className="di2-cv-sum">
      <div className="di2-cv-rows">
        {rows.map(([label, k]) => (
          <div className="di2-cv-row" key={k}>
            <span className="di2-cv-l">{label}</span>
            <span className="di2-cv-v">{String(f[k].value)}</span>
            <ConfidencePill value={f[k].confidence} />
          </div>
        ))}
      </div>
      {chips.length > 0 && (
        <div className="di2-cv-skills">{chips.map((s) => <span key={s} className="di2-skill">{s}</span>)}</div>
      )}
      <p className="di2-cv-note"><Icon name="arrowdown" size={12} /> Suggestions filled the form — review them below.</p>
    </div>
  );
}

// ---- session list ----------------------------------------------------------
function SessionList({ added }: { added: CreatedEmployee[] }) {
  if (added.length === 0) return null;
  return (
    <div className="card di2-session">
      <div className="card-head"><div className="card-title"><Icon name="check" size={15} style={{ color: "var(--risk-low)" }} /> Added this session <span className="ann">{added.length}</span></div></div>
      <div>
        {added.map((e) => (
          <a key={e.token} href={`#/profile/${e.token}`} className="di2-session-row">
            <div className="di2-session-main">
              <div className="di2-session-name">{e.full_name}</div>
              <div className="di2-session-meta">{e.title || e.role} · {e.division} · {e.currency} {e.base_salary.toLocaleString()}</div>
            </div>
            <span className="di2-session-open">Open <Icon name="chevright" size={12} /></span>
          </a>
        ))}
      </div>
    </div>
  );
}

// ---- the form --------------------------------------------------------------
function EmployeeForm({ divisions, suggestions, onSaved }: {
  divisions: ImportDivision[];
  suggestions: CvSuggestions | null;
  onSaved: (e: CreatedEmployee) => void;
}) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [customDiv, setCustomDiv] = useState(false);
  const [cvKeys, setCvKeys] = useState<Set<keyof FormState>>(new Set());
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  // Merge each parsed document into the form NON-DESTRUCTIVELY: only fill fields still empty,
  // so a second document (or anything you typed) is never clobbered.
  useEffect(() => {
    if (!suggestions) return;
    const fields = suggestions.fields;
    const filled = new Set<keyof FormState>();
    setForm((prev) => {
      const next = { ...prev };
      for (const [sk, fk] of Object.entries(SUGGEST_TO_FORM)) {
        const s = fields[sk];
        if (s && !next[fk]) { next[fk] = String(s.value); filled.add(fk); }
      }
      const g = fields["goal_attainment"];                  // stored as 0–1; shown as a %
      if (g && !next.goal_attainment) { next.goal_attainment = String(Math.round(Number(g.value) * 100)); filled.add("goal_attainment"); }
      if (suggestions.education_level && !next.education_level) { next.education_level = suggestions.education_level; filled.add("education_level"); }
      const mergeList = (key: keyof FormState, items: string[]) => {
        if (items.length && !next[key]) { next[key] = items.join(", "); filled.add(key); }
      };
      mergeList("skills", suggestions.skills);
      mergeList("languages", suggestions.languages);
      mergeList("certifications", suggestions.certifications);
      return next;
    });
    setCvKeys((prev) => new Set([...prev, ...filled]));
    setErr(null); setOk(null);
  }, [suggestions]);

  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }));

  const onDivisionSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
    if (e.target.value === "__new__") { setCustomDiv(true); setForm((p) => ({ ...p, division: "" })); }
    else { setCustomDiv(false); setForm((p) => ({ ...p, division: e.target.value })); }
  };

  const onPhoto = async (file: File | undefined) => {
    if (!file) return;
    try {
      const url = await downscaleImage(file);
      setForm((p) => ({ ...p, photo: url }));
    } catch {
      setErr("Could not read that image.");
    }
  };

  const submit = async () => {
    setErr(null); setOk(null);
    const required: [keyof FormState, string][] = [
      ["full_name", "Full name"], ["email", "Email"], ["role", "Role"],
      ["division", "Division"], ["level", "Level"], ["location", "Location"],
      ["hire_date", "Hire date"], ["base_salary", "Base salary"],
    ];
    for (const [k, label] of required) if (!form[k].trim()) { setErr(`${label} is required.`); return; }
    const salary = Number(form.base_salary);
    if (!Number.isFinite(salary) || salary <= 0) { setErr("Base salary must be a positive number."); return; }

    const num = (v: string) => (v.trim() === "" ? undefined : Number(v));
    const list = (v: string) => { const a = v.split(",").map((s) => s.trim()).filter(Boolean); return a.length ? a : undefined; };
    const str = (v: string) => (v.trim() === "" ? undefined : v.trim());

    const body: NewEmployeeInput = {
      full_name: form.full_name.trim(), email: form.email.trim(), role: form.role.trim(),
      title: str(form.title), level: form.level.trim(), division: form.division.trim(),
      team: str(form.team), location: form.location.trim(), employment_type: form.employment_type,
      status: form.status, hire_date: form.hire_date,
      business_travel_frequency: form.business_travel_frequency || undefined,
      base_salary: salary, currency: form.currency,
      photo: form.photo || undefined,
      // compensation extras
      bonus: num(form.bonus), equity: str(form.equity), pay_band: str(form.pay_band),
      last_raise_date: str(form.last_raise_date),
      // work history
      years_of_experience: num(form.years_of_experience), total_working_years: num(form.total_working_years),
      prior_employer_count: num(form.prior_employer_count), time_since_last_promotion: num(form.time_since_last_promotion),
      // performance
      performance_rating: num(form.performance_rating),
      goal_attainment: form.goal_attainment.trim() === "" ? undefined : Number(form.goal_attainment) / 100,
      // demographics
      gender: str(form.gender), marital_status: str(form.marital_status), birth_date: str(form.birth_date),
      // education & skills
      education_level: str(form.education_level), education_field: str(form.education_field),
      education_institution: str(form.education_institution),
      skills: list(form.skills), certifications: list(form.certifications), licenses: list(form.licenses),
      languages: list(form.languages), hobbies: list(form.hobbies),
      // lifecycle / compliance
      onboarding_status: str(form.onboarding_status), credential_name: str(form.credential_name),
      credential_expiry: str(form.credential_expiry), distance_from_home: num(form.distance_from_home),
    };
    setSaving(true);
    try {
      const created = await api.createEmployee(body);
      setOk(`${created.full_name} added to ${created.division}.`);
      setForm(EMPTY_FORM); setCustomDiv(false); setCvKeys(new Set());
      onSaved(created);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save the employee.");
    } finally {
      setSaving(false);
    }
  };

  const hint = (k: keyof FormState) => cvKeys.has(k)
    ? <span className="di2-fromcv" title="suggested from a document">from doc</span> : null;
  const initials = form.full_name.trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="card di2-form">
      <div className="card-head"><div className="card-title"><Icon name="profile" size={15} /> Employee details</div></div>
      <div className="card-body">
        <Section title="Identity & role" note="required">
          <div className="di2-photo-row">
            <PhotoPicker photo={form.photo} initials={initials} onPick={onPhoto}
                         onClear={() => setForm((p) => ({ ...p, photo: "" }))} />
            <div className="di2-photo-fields">
              <Field label="Full name" req hint={hint("full_name")}>
                <input value={form.full_name} onChange={set("full_name")} placeholder="e.g. Marie Curie" />
              </Field>
              <Field label="Work email" req hint={hint("email")}>
                <input value={form.email} onChange={set("email")} placeholder="name@company.com" />
              </Field>
            </div>
          </div>
          <div className="di2-fields">
            <Field label="Role" req hint={hint("role")}>
              <input value={form.role} onChange={set("role")} placeholder="e.g. Research Scientist" />
            </Field>
            <Field label="Title" hint={hint("title")}>
              <input value={form.title} onChange={set("title")} placeholder="defaults to role" />
            </Field>
            <Field label="Division" req>
              <select value={customDiv ? "__new__" : form.division} onChange={onDivisionSelect}>
                <option value="" disabled>Select a division…</option>
                {divisions.map((d) => <option key={d.name} value={d.name}>{d.name} · {d.headcount}</option>)}
                <option value="__new__">+ Add a new division…</option>
              </select>
              {customDiv && (
                <input autoFocus value={form.division} onChange={set("division")}
                       placeholder="New division name (e.g. Engineering)" style={{ marginTop: 6 }} />
              )}
            </Field>
            <Field label="Level" req hint={<span className="dm-field-hint">int or grade</span>}>
              <input value={form.level} onChange={set("level")} placeholder="e.g. 4 or VP" />
            </Field>
            <Field label="Team"><input value={form.team} onChange={set("team")} placeholder="defaults to division" /></Field>
            <Field label="Location" req hint={hint("location")}>
              <input value={form.location} onChange={set("location")} placeholder="e.g. New York" />
            </Field>
            <Field label="Employment type" req hint={hint("employment_type")}>
              <select value={form.employment_type} onChange={set("employment_type")}>
                {EMPLOYMENT_TYPES.map((t) => <option key={t} value={t}>{nice(t)}</option>)}
              </select>
            </Field>
            <Field label="Status">
              <select value={form.status} onChange={set("status")}>
                {STATUSES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}
              </select>
            </Field>
            <Field label="Hire date" req hint={hint("hire_date")}>
              <input type="date" value={form.hire_date} onChange={set("hire_date")} />
            </Field>
            <Field label="Business travel">
              <select value={form.business_travel_frequency} onChange={set("business_travel_frequency")}>
                {TRAVEL.map((t) => <option key={t} value={t}>{t || "—"}</option>)}
              </select>
            </Field>
          </div>
        </Section>

        <Section title="Personal" note="optional · birthday & demographics — fairness audit only, never scored">
          <Field label="Birthday"><input type="date" value={form.birth_date} onChange={set("birth_date")} /></Field>
          <Field label="Gender">
            <select value={form.gender} onChange={set("gender")}>
              {GENDERS.map((g) => <option key={g} value={g}>{g ? nice(g) : "—"}</option>)}
            </select>
          </Field>
          <Field label="Marital status">
            <select value={form.marital_status} onChange={set("marital_status")}>
              {MARITAL.map((m) => <option key={m} value={m}>{m ? nice(m) : "—"}</option>)}
            </select>
          </Field>
          <ListField label="Languages" value={form.languages} onChange={set("languages")} hint={hint("languages")} placeholder="English, French" />
          <ListField label="Hobbies & interests" value={form.hobbies} onChange={set("hobbies")} placeholder="Chess, Cycling" />
        </Section>

        <Section title="Education & skills" note="optional — pulled from a CV">
          <Field label="Education level" hint={hint("education_level")}>
            <select value={form.education_level} onChange={set("education_level")}>
              {EDU_LEVELS.map((e) => <option key={e} value={e}>{e ? nice(e) : "—"}</option>)}
            </select>
          </Field>
          <Field label="Field of study" hint={hint("education_field")}>
            <select value={form.education_field} onChange={set("education_field")}>
              {EDU_FIELDS.map((e) => <option key={e} value={e}>{e ? nice(e) : "—"}</option>)}
            </select>
          </Field>
          <Field label="University / school" hint={hint("education_institution")}>
            <input value={form.education_institution} onChange={set("education_institution")} placeholder="e.g. Stanford University" />
          </Field>
          <ListField label="Skills" value={form.skills} onChange={set("skills")} hint={hint("skills")} placeholder="Python, SQL, Risk" />
          <ListField label="Certifications" value={form.certifications} onChange={set("certifications")} hint={hint("certifications")} placeholder="CFA, PMP" />
          <ListField label="Licenses" value={form.licenses} onChange={set("licenses")} placeholder="Series 7, Series 63" />
        </Section>

        <Section title="Compensation" note="base pay required · rest optional">
          <Field label="Base salary" req hint={hint("base_salary")}>
            <input type="number" min="0" value={form.base_salary} onChange={set("base_salary")} placeholder="e.g. 120000" />
          </Field>
          <Field label="Currency" req hint={hint("currency")}>
            <select value={form.currency} onChange={set("currency")}>
              {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Annual bonus"><input type="number" min="0" value={form.bonus} onChange={set("bonus")} placeholder="optional" /></Field>
          <Field label="Equity / deferred"><input value={form.equity} onChange={set("equity")} placeholder="e.g. 2,000 RSUs" /></Field>
          <Field label="Pay band"><input value={form.pay_band} onChange={set("pay_band")} placeholder="e.g. P4" /></Field>
          <Field label="Last raise date"><input type="date" value={form.last_raise_date} onChange={set("last_raise_date")} /></Field>
        </Section>

        <Section title="Work history & experience" note="optional — skip if you don't have it">
          <Field label="Years of experience" hint={hint("years_of_experience")}>
            <input type="number" min="0" value={form.years_of_experience} onChange={set("years_of_experience")} placeholder="optional" />
          </Field>
          <Field label="Total working years" hint={hint("total_working_years")}>
            <input type="number" min="0" value={form.total_working_years} onChange={set("total_working_years")} placeholder="optional" />
          </Field>
          <Field label="Prior employers" hint={hint("prior_employer_count")}>
            <input type="number" min="0" value={form.prior_employer_count} onChange={set("prior_employer_count")} placeholder="optional" />
          </Field>
          <Field label="Months since last promotion">
            <input type="number" min="0" value={form.time_since_last_promotion} onChange={set("time_since_last_promotion")} placeholder="optional" />
          </Field>
        </Section>

        <Section title="Performance" note="optional — skip if you don't have it">
          <Field label="Latest rating (0–5)" hint={hint("performance_rating")}>
            <input type="number" min="0" max="5" step="0.1" value={form.performance_rating} onChange={set("performance_rating")} placeholder="optional" />
          </Field>
          <Field label="Goal attainment (%)" hint={hint("goal_attainment")}>
            <input type="number" min="0" max="200" value={form.goal_attainment} onChange={set("goal_attainment")} placeholder="optional" />
          </Field>
        </Section>

        <Section title="Compliance & lifecycle" note="optional">
          <Field label="Onboarding">
            <select value={form.onboarding_status} onChange={set("onboarding_status")}>
              {ONBOARDING.map((o) => <option key={o} value={o}>{o ? nice(o) : "—"}</option>)}
            </select>
          </Field>
          <Field label="Work permit / credential"><input value={form.credential_name} onChange={set("credential_name")} placeholder="e.g. Work permit" /></Field>
          <Field label="Credential expiry"><input type="date" value={form.credential_expiry} onChange={set("credential_expiry")} /></Field>
          <Field label="Commute (km)"><input type="number" min="0" value={form.distance_from_home} onChange={set("distance_from_home")} placeholder="optional" /></Field>
        </Section>

        {err && <div className="di2-err"><Icon name="alert" size={13} /> {err}</div>}
        {ok && <div className="di2-ok"><Icon name="check" size={13} /> {ok}</div>}

        <div className="di2-form-foot">
          <span className="subtle">Saved to the canonical record — Employees list, Profile &amp; analytics.</span>
          <button className="btn primary" onClick={submit} disabled={saving}>
            <Icon name="check" size={15} /> {saving ? "Saving…" : "Add employee"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PhotoPicker({ photo, initials, onPick, onClear }: {
  photo: string; initials: string; onPick: (f: File | undefined) => void; onClear: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <div className="di2-photo">
      <button type="button" className="di2-photo-disc" onClick={() => ref.current?.click()} title="Add a profile photo">
        {photo
          ? <img src={photo} alt="" />
          : <span className="di2-photo-ph">{initials || <Icon name="profile" size={22} />}</span>}
        <span className="di2-photo-cam"><Icon name="download" size={12} /></span>
      </button>
      {photo
        ? <button type="button" className="di2-photo-clear" onClick={onClear}>Remove</button>
        : <span className="di2-photo-lbl">Photo</span>}
      <input ref={ref} type="file" accept="image/*" hidden onChange={(e) => onPick(e.target.files?.[0] ?? undefined)} />
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="di2-section">
      <div className="di2-section-head">
        <span className="di2-section-t">{title}</span>
        {note && <span className="di2-section-note">{note}</span>}
      </div>
      <div className="di2-fields">{children}</div>
    </div>
  );
}

function Field({ label, req, hint, children }: {
  label: string; req?: boolean; hint?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <label className="dm-field">
      <span className="dm-field-l">{label}{req && <span className="di2-req">*</span>}{hint}</span>
      {children}
    </label>
  );
}

function ListField({ label, value, onChange, hint, placeholder }: {
  label: string; value: string; hint?: React.ReactNode; placeholder?: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <Field label={label} hint={hint ?? <span className="dm-field-hint">comma-separated</span>}>
      <input value={value} onChange={onChange} placeholder={placeholder} />
    </Field>
  );
}
