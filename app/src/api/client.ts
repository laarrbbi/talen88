/**
 * The ONLY module that talks to the backends.
 *
 * Two hosts: the data API (:8000, scoped + audited — every employee/score read,
 * name resolution, and notification goes through it) and the agent layer (:8002,
 * read + L3-approve only). No component fetches directly; they all go through here,
 * so auth, scoping, and the name-resolution boundary stay in one place.
 *
 * Names are NEVER part of an analytics payload — they are resolved on demand via
 * `resolveNames` (the data layer's single identity boundary), for authorized
 * records only. Everything else is token-only.
 */

const DATA_URL = import.meta.env.VITE_DATA_URL ?? "http://localhost:8000";
const AGENTS_URL = import.meta.env.VITE_AGENTS_URL ?? "http://localhost:8002";

const TOKEN_KEY = "talent88.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null): void {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(base: string, path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.body) headers["Content-Type"] = "application/json";
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${base}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const data = <T>(p: string, init?: RequestInit) => request<T>(DATA_URL, p, init);
const agents = <T>(p: string, init?: RequestInit) => request<T>(AGENTS_URL, p, init);

// Multipart upload (CV files). Must NOT set Content-Type — the browser adds the
// multipart boundary itself — so this bypasses the JSON `request` helper.
async function uploadData<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${DATA_URL}${path}`, { method: "POST", body: form, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

// ---- types (mirror the data/agent contracts) -------------------------------
export interface Me { email: string; name: string; role: string; division: string | null; }
export interface LoginResult extends Me { token: string; }

export interface Score { as_of_date: string; flight_risk: number; value_score: number; risk_trend: number; }
export interface ReasonCode { label: string; direction: "increases" | "decreases"; weight: number; }
export interface EmployeeRow {
  token: string; role: string; level: number; division: string; team: string;
  manager_token: string | null; location: string; hire_date: string; employment_type: string;
  features: { comp_gap: number; months_since_promotion: number; tenure_months: number; manager_changes_12mo: number } | null;
  latest_score: Score | null; reason_codes: ReasonCode[];
}
export interface PanelMetric { metric: string; score: number; reason_codes: ReasonCode[]; }
export interface CapitalMetric { metric: string; amount: number; unit: string; is_estimate: number; }
export interface License { name: string; expiry?: string | null; }
export interface Promotion { date?: string | null; from?: string | null; to?: string | null; }
// Privacy-safe canonical enrichment (same fields as the people-search surface). No
// bias-audit (gender/birth_year_band/marital_status) or identity-proxy columns ever.
export interface ProfileBlock {
  title: string | null; status: string | null;
  skiplevel_token: string | null; business_travel_frequency: string | null;
  education_level: string | null; education_field: string | null;
  certifications: string[]; licenses: License[]; trainings_last_year: number | null;
  years_of_experience: number | null;
  pay_percentile_in_role: number | null; pay_band: string | null;
  tenure_years: number | null; years_in_current_role: number | null;
  total_working_years: number | null; months_since_promotion: number | null;
  promotions: Promotion[];
}
export interface Employee360 extends EmployeeRow {
  attributes: {
    as_of_date: string; perf_rating: number; engagement_pulse: number | null;
    learning_hours_12mo: number; internal_moves: number; span_of_control: number;
    consented_signals: number;
  } | null;
  skills: { name: string; family: string; proficiency: number }[];
  history: Score[]; panel: PanelMetric[]; capital: CapitalMetric[];
  profile?: ProfileBlock;
}
export interface QuadrantPoint { token: string; division: string; flight_risk: number; value_score: number; cost_to_lose: number | null; }
export interface Dashboard {
  headline: { total_scored: number; high_risk_count: number; key_person_count: number; act_now_count: number };
  quadrant: QuadrantPoint[];
  by_division: { division: string; total: number; high_risk: number }[];
}
// Operational "Team Pulse" digest — neutral, scoped, token-only. Leave carries a return
// date ONLY, never a reason (enforced by the schema + query seam, mirrored in these types).
export interface PulsePerson { token: string; role: string; division: string; team: string; }
export interface PulseBirthday extends PulsePerson { in_days: number; month_day: string; }
export interface PulseAnniversary extends PulsePerson { in_days: number; years: number; }
export interface PulseAway extends PulsePerson { return_date: string | null; }
export interface PulseOnboarding extends PulsePerson { status: string; hire_date: string; }
export interface PulseRoleChange extends PulsePerson { note: string; effective_date: string; in_days: number; }
export interface PulseCredential extends PulsePerson { name: string; expiry_date: string; in_days: number; }
export interface TeamPulse {
  as_of: string; horizon_days: number;
  scope: { division: string | null; manager_token: string | null };
  birthdays: PulseBirthday[]; anniversaries: PulseAnniversary[];
  on_pto: PulseAway[]; on_leave: PulseAway[];
  onboarding: PulseOnboarding[]; role_changes: PulseRoleChange[];
  credentials: PulseCredential[];
  availability: { in_office: number; remote: number; out: number };
  team_size: number;
}
export interface IdentityHit { token: string; name: string; role: string; division: string; location: string; }
export interface Notification { id: number; kind: string; payload: Record<string, unknown>; why: string; created_ts: string; read: boolean; }
export interface NotificationPref { kind: string; description: string; channel: string; opted_in: boolean; }
export interface AgentInfo { name: string; max_level: number; status: string; scope?: string; description?: string; }
export interface Recommendation {
  employee_token: string; title: string; rationale: string; drivers: ReasonCode[];
  estimates: Record<string, number>; estimates_caveat?: string;
  proposed_action: { kind: string; employee_token: string; level: number; params: Record<string, unknown> } | null;
}
export interface AgentRun { agent: string; insight: Record<string, unknown>; recommendations: Recommendation[]; narrative?: string | null; }
export interface SearchHit {
  token: string; role: string; title: string; level: number; division: string; team: string;
  location: string; employment_type: string; status: string;
  skills: string[]; certifications: string[]; licenses: unknown[];
  education_level: string | null; education_field: string | null;
  pay_percentile_in_role: number | null; years_of_experience: number | null;
  latest_score: { flight_risk: number; value_score: number } | null;
}
export interface PeopleSearchResult {
  agent: string; question: string; criteria: Record<string, unknown>;
  unsupported_fields: string[]; matched: SearchHit[]; note: string | null; answer: string;
}
// One dispatched chat turn. `kind` tells the UI how to render it:
//   answer | search -> a grounded reply + matched people; recommendation -> reco cards;
//   clarify -> the agent is asking for a missing input (e.g. which person).
export interface ChatResult {
  kind: "answer" | "search" | "recommendation" | "clarify";
  agent: string;
  answer: string;
  matched: { token: string; role?: string; location?: string; latest_score?: { flight_risk?: number } | null }[];
  recommendations: Recommendation[];
  note: string | null;
}

// ---- analytics (the GOLD-layer Analytics section) --------------------------
// Token-only aggregates; every endpoint is scoped + audited server-side and carries a
// `data_status` so the UI renders the honest data-maturity ladder (live vs locked card)
// instead of fabricating data. Shared filter params mirror the backend query params.
export interface AnalyticsFilters {
  division?: string; manager?: string; location?: string;
  tenure_band?: string; level?: number; date_range?: string;
}
export interface AnalyticsScope {
  division: string | null; manager?: string | null; location?: string | null;
  level?: number | null; tenure_band?: string | null; metric?: string;
}
export interface HistBin { lo: number; hi: number; count: number; }
export interface TurnoverSegment { segment: string; headcount: number; leavers: number; rate: number; }
export interface SurvivalPoint { t: number; survival: number; }
export interface Turnover {
  headline: { headcount: number; leavers: number; attrition_rate: number; voluntary: number; involuntary: number; regretted: number };
  by_segment: TurnoverSegment[]; suppressed_segments: number;
  survival: SurvivalPoint[];
  trend: { data_status: string; points: { month: string; leavers: number }[] };
  data_status: string; scope: AnalyticsScope;
}
export interface DriverRow { label: string; direction: "increases" | "decreases"; mean_weight: number; count: number; }
export interface Drivers {
  top_drivers: DriverRow[]; distribution: HistBin[];
  risk_vs_attrition: { leaver_mean_flight_risk: number | null; stayer_mean_flight_risk: number | null };
  data_status: string; scope: AnalyticsScope;
}
export interface ManagerRow {
  manager_token: string; team_turnover_rate: number | null; span_of_control: number | null;
  peers_departed_recently: number | null; manager_performance_rating: number | null;
}
export interface Managers {
  by_manager: ManagerRow[]; suppressed_managers: number; span_distribution: HistBin[];
  contagion: { manager_token: string; peers_departed_recently: number | null }[];
  data_status: string; scope: AnalyticsScope;
}
export interface FairnessCohort { cohort: string; count: number; mean: number | null; }
export interface FairnessTrait { trait: string; cohorts: FairnessCohort[]; adverse_impact_ratio: number | null; four_fifths_pass: boolean | null; }
export interface Fairness { traits: FairnessTrait[]; metric: string; restricted: boolean; data_status: string; scope: AnalyticsScope; }
export interface CostScenario { token: string; cost_to_lose: number; flight_risk: number; cost_at_risk: number; }
export interface CostSegment { segment: string; headcount: number; cost_at_risk: number; }
export interface Cost {
  total_at_risk: number; by_division: CostSegment[]; distribution: HistBin[]; scenario: CostScenario[];
  intervention_roi: { data_status: string }; data_status: string; scope: AnalyticsScope;
}
export interface CompBox { segment: string; headcount: number; box: { min: number; q1: number; median: number; q3: number; max: number; n: number } | null; }
export interface Compensation {
  by_level: CompBox[]; percentile_distribution: HistBin[];
  market: { data_status: string; mean_gap_vs_market?: number | null; n?: number };
  compa_ratio: { data_status: string; fallback?: string };
  data_status: string; scope: AnalyticsScope;
}
export interface Engagement {
  data_status: string;
  headline: { engagement_score?: number | null; enps_score?: number | null; response_rate?: number | null };
  trend: { data_status: string; points: { month: string; engagement_score: number | null; enps_score: number | null }[] };
  scope: AnalyticsScope;
}
export interface ForecastSegment { segment: string; headcount: number; expected_leavers: number; }
export interface Forecast {
  expected_leavers: { value: number; is_estimate: boolean }; by_division: ForecastSegment[];
  seasonal: { data_status: string }; data_status: string; scope: AnalyticsScope;
}

function anQs(f: AnalyticsFilters = {}): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

// ---- survey module (:8000, token-only · scoped · audited) ------------------
export interface SurveyScale { key: string; label: string; options: string[] | null; }
export interface SurveyItem { id: number; text: string; scale: string; qtype: string; lifecycle_tag: string | null; }
export interface SurveyDriver { code: string; name: string; description: string | null; is_outcome: number; items: SurveyItem[]; }
export interface SurveyLibrary { scales: SurveyScale[]; drivers: SurveyDriver[]; driver_count: number; item_count: number; }
export interface SurveyTemplate {
  id: number; key: string; title: string; type: string; cadence: string | null;
  description: string | null; builtin: number; item_count: number;
}
export interface SurveyTemplateQuestion {
  position: number; item_id: number; driver_code: string; driver_name: string | null;
  text: string; scale: string | null; qtype: string;
}
export interface SurveyTemplateDetail {
  id: number; key: string; title: string; type: string; cadence: string | null;
  description: string | null; builtin: number; questions: SurveyTemplateQuestion[];
}
export interface SurveyCampaign {
  id: number; title: string; type: string; status: string; cadence: string | null;
  division: string | null; trigger_event: string | null; anonymous: number;
  min_threshold: number; opens_at: string | null; closes_at: string | null; created_ts: string;
  question_count: number; invited: number; responded: number;
}
export interface SurveyQuestion {
  id: number; position: number; driver_code: string | null; text: string;
  qtype: string; scale: string | null; required: number;
}
export interface SurveyCampaignDetail extends SurveyCampaign {
  audience: Record<string, unknown>; trigger_offset_days: number | null; questions: SurveyQuestion[];
}
export interface SurveyDriverResult { code: string; name: string; score: number; n: number; }
export interface SurveyResults {
  campaign_id: number; title: string; type: string; status: string;
  invited: number; respondents: number; response_rate: number | null;
  min_threshold: number; division: string | null; suppressed: boolean;
  engagement_score: number | null; enps_score: number | null;
  drivers: SurveyDriverResult[]; themes: string[];
}
export interface CampaignCreate {
  title: string; type: string; template_id?: number | null; item_ids?: number[];
  audience?: Record<string, unknown> | null; cadence?: string | null;
  trigger_event?: string | null; trigger_offset_days?: number | null;
  anonymous?: boolean; min_threshold?: number;
}

// ---- document import (real employee creation) ------------------------------
export interface ImportDivision { name: string; headcount: number; }
export interface CvSuggestion { value: string | number; confidence: number; }
export interface CvSuggestions {
  // fields can include: full_name, email, phone, role, title, location, base_salary,
  // currency, hire_date, employment_type, performance_rating, goal_attainment,
  // years_of_experience, prior_employer_count, total_working_years, education_field,
  // education_institution
  fields: Record<string, CvSuggestion>;
  skills: string[];
  languages: string[];
  certifications: string[];
  years_experience: number | null;
  education_level: string | null;
  doc_type: string;                         // resolved document type (cv|contract|offer|payslip|review)
  excerpt: string;
  char_count: number;
  filename: string;
}
export interface NewEmployeeInput {
  full_name: string; email: string; role: string; title?: string;
  level: string; division: string; team?: string; location: string;
  employment_type: string; status?: string; business_travel_frequency?: string;
  hire_date: string; base_salary: number; currency: string; manager_token?: string;
  photo?: string;                           // small data-URL thumbnail
  // compensation extras
  bonus?: number; equity?: string; pay_band?: string; last_raise_date?: string;
  // work history / experience
  performance_rating?: number; goal_attainment?: number;
  years_of_experience?: number; prior_employer_count?: number; total_working_years?: number;
  time_since_last_promotion?: number;
  // demographics (bias-audit only)
  gender?: string; marital_status?: string; birth_date?: string; birth_year_band?: string;
  // education & skills
  education_level?: string; education_field?: string; education_institution?: string;
  skills?: string[]; certifications?: string[]; licenses?: string[];
  languages?: string[]; hobbies?: string[];
  // lifecycle / compliance
  onboarding_status?: string; credential_name?: string; credential_expiry?: string;
  distance_from_home?: number;
}
export interface CreatedEmployee {
  token: string; full_name: string; email: string; role: string; title: string;
  level: number; division: string; team: string; location: string;
  employment_type: string; status: string; hire_date: string;
  base_salary: number; currency: string; manager_token: string | null;
}

// ---- data API --------------------------------------------------------------
export const api = {
  login: (email: string) =>
    data<LoginResult>("/auth/login", { method: "POST", body: JSON.stringify({ email }) }),
  me: () => data<Me>("/me"),
  divisions: () => data<{ id: number; name: string }[]>("/divisions"),
  employees: (params: Record<string, string | number | undefined> = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    const qs = q.toString();
    return data<EmployeeRow[]>(`/employees${qs ? `?${qs}` : ""}`);
  },
  employee360: (token: string) => data<Employee360>(`/employees/${token}/360`),

  // ---- document import: real, persisted employee creation (admin-only) -----
  importDivisions: () => data<ImportDivision[]>("/import/divisions"),
  parseDocument: (file: File, docType = "auto") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", docType);
    return uploadData<CvSuggestions>("/import/parse_document", fd);
  },
  createEmployee: (body: NewEmployeeInput) =>
    data<CreatedEmployee>("/import/employee", { method: "POST", body: JSON.stringify(body) }),

  dashboard: () => data<Dashboard>("/dashboard"),
  // Operational "what's happening" digest for the Dashboard panel. Scoped + audited
  // server-side (managers see only their division); returns neutral signals only.
  teamPulse: (horizonDays = 30) => data<TeamPulse>(`/team_pulse?horizon_days=${horizonDays}`),
  // The single identity boundary: tokens -> names, authorized records only.
  resolveNames: (tokens: string[]) =>
    data<Record<string, string>>("/identity/resolve", { method: "POST", body: JSON.stringify({ tokens }) }),
  // Same boundary for profile pictures: tokens -> data-URL photos (those that have one).
  resolvePhotos: (tokens: string[]) =>
    data<Record<string, string>>("/identity/photos", { method: "POST", body: JSON.stringify({ tokens }) }),
  // Reverse of resolveNames: name typeahead for the person-picker, scoped + audited.
  // Names are shown only in the picker; the chosen person flows onward as a token.
  searchIdentities: (q: string, limit = 8) =>
    data<IdentityHit[]>("/identity/search", { method: "POST", body: JSON.stringify({ q, limit }) }),

  notifications: (unreadOnly = false) =>
    data<Notification[]>(`/notifications${unreadOnly ? "?unread_only=true" : ""}`),
  markRead: (id: number) => data<{ read: boolean }>(`/notifications/${id}/read`, { method: "POST", body: "{}" }),
  prefs: () => data<NotificationPref[]>("/notification_prefs"),
  setPref: (kind: string, opted_in: boolean) =>
    data<NotificationPref>("/notification_prefs", { method: "PUT", body: JSON.stringify({ kind, opted_in }) }),

  // ---- agent layer (:8002) -------------------------------------------------
  agentsList: () => agents<{ max_autonomy_level: number; agents: AgentInfo[] }>("/agents"),
  ask: (question: string, history: { role: "user" | "assistant"; content: string }[] = []) =>
    agents<Record<string, unknown>>("/agents/conversational/ask", { method: "POST", body: JSON.stringify({ question, history }) }),
  // The unified chat surface: the dispatcher routes to conversational / search / one of the
  // four recommending agents. person_token (from the picker) and division (from the dropdown)
  // are optional scope; names never leave the browser, only tokens flow to the agents.
  chat: (body: {
    question: string;
    history?: { role: "user" | "assistant"; content: string }[];
    person_token?: string;
    division?: string;
  }) => agents<ChatResult>("/agents/chat", { method: "POST", body: JSON.stringify({ history: [], ...body }) }),
  searchPeople: (question: string, history: { role: "user" | "assistant"; content: string }[] = []) =>
    agents<PeopleSearchResult>("/agents/search/people", { method: "POST", body: JSON.stringify({ question, history }) }),
  runAgent: (name: string, body: { division?: string; token?: string }) =>
    agents<AgentRun>(`/agents/${name}/run`, { method: "POST", body: JSON.stringify(body) }),
  approve: (name: string, action: { kind: string; employee_token: string; params?: Record<string, unknown> }) =>
    agents<Record<string, unknown>>(`/agents/${name}/approve`, { method: "POST", body: JSON.stringify(action) }),

  // ---- analytics (:8000, GOLD-layer aggregates, token-only + scoped + audited) ----
  analyticsTurnover: (f?: AnalyticsFilters) => data<Turnover>(`/analytics/turnover${anQs(f)}`),
  analyticsDrivers: (f?: AnalyticsFilters) => data<Drivers>(`/analytics/drivers${anQs(f)}`),
  analyticsManagers: (f?: AnalyticsFilters) => data<Managers>(`/analytics/managers${anQs(f)}`),
  analyticsFairness: (metric = "flight_risk") => data<Fairness>(`/analytics/fairness?metric=${encodeURIComponent(metric)}`),
  analyticsCost: (f?: AnalyticsFilters) => data<Cost>(`/analytics/cost${anQs(f)}`),
  analyticsCompensation: (f?: AnalyticsFilters) => data<Compensation>(`/analytics/compensation${anQs(f)}`),
  analyticsEngagement: (f?: AnalyticsFilters) => data<Engagement>(`/analytics/engagement${anQs(f)}`),
  analyticsForecast: (f?: AnalyticsFilters) => data<Forecast>(`/analytics/forecast${anQs(f)}`),

  // ---- survey module (:8000) ----------------------------------------------
  surveyLibrary: () => data<SurveyLibrary>("/surveys/library"),
  surveyTemplates: () => data<SurveyTemplate[]>("/surveys/templates"),
  surveyTemplate: (id: number) => data<SurveyTemplateDetail>(`/surveys/templates/${id}`),
  surveyCampaigns: (status?: string) =>
    data<SurveyCampaign[]>(`/surveys/campaigns${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  surveyCampaign: (id: number) => data<SurveyCampaignDetail>(`/surveys/campaigns/${id}`),
  surveyResults: (id: number) => data<SurveyResults>(`/surveys/campaigns/${id}/results`),
  createCampaign: (body: CampaignCreate) =>
    data<{ id: number; status: string; questions: number; division: string | null; type: string }>(
      "/surveys/campaigns", { method: "POST", body: JSON.stringify(body) }),
  launchCampaign: (id: number) =>
    data<{ id: number; status: string; invited: number; trigger_event: string | null }>(
      `/surveys/campaigns/${id}/launch`, { method: "POST", body: "{}" }),
  setCampaignStatus: (id: number, status: string) =>
    data<{ id: number; status: string }>(
      `/surveys/campaigns/${id}/status`, { method: "POST", body: JSON.stringify({ status }) }),
  closeCampaign: (id: number) =>
    data<{ id: number; status: string; as_of_date: string; respondents: number; engagement_rows_written: number }>(
      `/surveys/campaigns/${id}/close`, { method: "POST", body: "{}" }),
};
