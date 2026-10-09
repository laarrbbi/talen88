/**
 * Connectors — the source directory for the Data Management section.
 *
 * ADDITIVE, UI-only. This is a new tab that presents the full connector
 * inventory grouped by category, reusing the existing connector-card pattern
 * (the dm-conn-* classes from the Re-ingestion tab). It does NOT touch the
 * Re-ingestion tab, the Document Import tab, any adapter, or the canonical
 * schema — every connector here is a directory entry that conceptually lands in
 * the SAME canonical employee record and the SAME existing dashboards / profile.
 *
 * Everything is mocked and badged "demo — not persisted": Workday and Peakon
 * are shown Connected (a read-only reflection of their Re-ingestion state); the
 * rest are Available with a mocked connect flow. Comms-metadata sources are
 * badged consent-gated / metadata-only; the section 2.7 work-signal sources are
 * flagged native / differentiating; the long tail is covered by one Unified
 * HRIS API card rather than many native adapters. The document-native intake
 * mode is referenced (not duplicated) — it lives in the Document Import tab.
 */
import { useMemo, useState } from "react";
import { Icon } from "../components/icons";
import { DemoBadge, useToast } from "../components/demo";

type ConnStatus = "connected" | "available";
interface ConnDef {
  name: string;
  kind: string;            // sub-label shown on the card
  status: ConnStatus;
  native?: boolean;        // differentiating native adapter (section 2.7)
  consent?: boolean;       // consent-gated, metadata only
  unified?: boolean;       // reachable through the Unified HRIS API
}
interface ConnGroup { key: string; title: string; icon: string; blurb: string; items: ConnDef[]; }

// The inventory from section 2 of the connector-extension spec. Status reflects
// only what is live today: Workday + Peakon (mirrored from Re-ingestion).
const GROUPS: ConnGroup[] = [
  {
    key: "hris", title: "Core HRIS / HCM", icon: "globe",
    blurb: "Native depth for the top systems; the long tail comes through the Unified HRIS API.",
    items: [
      { name: "Workday", kind: "Core HRIS", status: "connected", native: true },
      { name: "Oracle HCM / Fusion", kind: "Core HRIS", status: "available", native: true },
      { name: "SAP SuccessFactors", kind: "Core HRIS", status: "available", unified: true },
      { name: "UKG", kind: "HCM", status: "available", unified: true },
      { name: "Ceridian Dayforce", kind: "HCM", status: "available", unified: true },
      { name: "BambooHR", kind: "HRIS", status: "available", unified: true },
      { name: "HiBob", kind: "HRIS", status: "available", unified: true },
      { name: "Paylocity", kind: "HCM", status: "available", unified: true },
      { name: "Paycom", kind: "HCM", status: "available", unified: true },
      { name: "Namely", kind: "HRIS", status: "available", unified: true },
      { name: "Personio", kind: "HRIS", status: "available", unified: true },
      { name: "Darwinbox", kind: "HCM", status: "available", unified: true },
      { name: "Sage People", kind: "HRIS", status: "available", unified: true },
      { name: "Rippling", kind: "HRIS", status: "available", unified: true },
      { name: "Gusto", kind: "HRIS / payroll", status: "available", unified: true },
    ],
  },
  {
    key: "payroll", title: "Payroll & PEO", icon: "comp",
    blurb: "Pay, benefits and PEO sources feeding compensation and lifecycle fields.",
    items: [
      { name: "ADP", kind: "Payroll", status: "available", unified: true },
      { name: "Paychex", kind: "Payroll", status: "available", unified: true },
      { name: "Deel", kind: "Global payroll / EOR", status: "available", unified: true },
      { name: "TriNet", kind: "PEO", status: "available", unified: true },
      { name: "Insperity", kind: "PEO", status: "available", unified: true },
      { name: "Justworks", kind: "PEO", status: "available", unified: true },
      { name: "Remote", kind: "EOR", status: "available", unified: true },
      { name: "iSolved", kind: "Payroll", status: "available", unified: true },
    ],
  },
  {
    key: "ats", title: "ATS / Recruiting", icon: "users",
    blurb: "Hiring funnel and candidate sources for pre-hire and req signals.",
    items: [
      { name: "Greenhouse", kind: "ATS", status: "available", unified: true },
      { name: "Lever", kind: "ATS", status: "available", unified: true },
      { name: "Ashby", kind: "ATS", status: "available", unified: true },
      { name: "iCIMS", kind: "ATS", status: "available", unified: true },
      { name: "Workday Recruiting", kind: "ATS", status: "available", unified: true },
      { name: "SmartRecruiters", kind: "ATS", status: "available", unified: true },
      { name: "Workable", kind: "ATS", status: "available", unified: true },
    ],
  },
  {
    key: "survey", title: "Engagement / Survey", icon: "survey",
    blurb: "Precomputed engagement scores, drivers and themes — ingested verbatim, never recomputed.",
    items: [
      { name: "Workday Peakon", kind: "Engagement survey", status: "connected" },
      { name: "Qualtrics", kind: "Experience mgmt", status: "available" },
      { name: "Medallia", kind: "Experience mgmt", status: "available" },
      { name: "Microsoft Viva Glint", kind: "Engagement survey", status: "available" },
      { name: "Culture Amp", kind: "Engagement survey", status: "available" },
      { name: "Lattice", kind: "Performance / engagement", status: "available" },
      { name: "Leapsome", kind: "Performance / engagement", status: "available" },
      { name: "15Five", kind: "Performance / engagement", status: "available" },
      { name: "Perceptyx", kind: "Engagement survey", status: "available" },
    ],
  },
  {
    key: "comp", title: "Comp benchmark / labour-market", icon: "graph",
    blurb: "Licensed market data mapping role / level / region to comp percentiles.",
    items: [
      { name: "Radford McLagan (Aon)", kind: "Comp benchmark", status: "available" },
      { name: "Mercer", kind: "Comp benchmark", status: "available" },
      { name: "Pave", kind: "Comp benchmark", status: "available" },
      { name: "Payscale", kind: "Comp benchmark", status: "available" },
      { name: "Lightcast", kind: "Labour-market data", status: "available" },
      { name: "Revelio Labs", kind: "Labour-market data", status: "available" },
      { name: "LinkedIn Talent Insights", kind: "Labour-market data", status: "available" },
    ],
  },
  {
    key: "equity", title: "Cap table / equity", icon: "award",
    blurb: "Vesting and equity signals that feed deferred-comp and retention context.",
    items: [
      { name: "Carta", kind: "Cap table", status: "available" },
      { name: "Shareworks", kind: "Equity", status: "available" },
      { name: "Pulley", kind: "Cap table", status: "available" },
      { name: "Ledgy", kind: "Equity", status: "available" },
    ],
  },
  {
    key: "work", title: "Work apps & collaboration", icon: "grid",
    blurb: "The differentiating work-signal sources — built natively because the unified APIs do not carry them.",
    items: [
      { name: "ClickUp", kind: "Project / work", status: "available", native: true },
      { name: "Asana", kind: "Project / work", status: "available", native: true },
      { name: "Monday", kind: "Project / work", status: "available", native: true },
      { name: "Linear", kind: "Project / work", status: "available", native: true },
      { name: "Jira", kind: "Project / work", status: "available", native: true },
      { name: "Trello", kind: "Project / work", status: "available", native: true },
      { name: "Wrike", kind: "Project / work", status: "available", native: true },
      { name: "Smartsheet", kind: "Project / work", status: "available", native: true },
      { name: "Notion", kind: "Project / work", status: "available", native: true },
      { name: "GitHub", kind: "Engineering signal", status: "available", native: true },
      { name: "GitLab", kind: "Engineering signal", status: "available", native: true },
      { name: "Bitbucket", kind: "Engineering signal", status: "available", native: true },
      { name: "Azure DevOps", kind: "Engineering signal", status: "available", native: true },
      { name: "PagerDuty", kind: "On-call / incident", status: "available", native: true },
      { name: "Microsoft 365 / Graph", kind: "Comms metadata", status: "available", native: true, consent: true },
      { name: "Google Workspace", kind: "Comms metadata", status: "available", native: true, consent: true },
      { name: "Slack", kind: "Comms metadata", status: "available", native: true, consent: true },
      { name: "Zoom", kind: "Comms metadata", status: "available", native: true, consent: true },
      { name: "Salesforce", kind: "CRM / sales", status: "available", native: true },
      { name: "HubSpot", kind: "CRM / sales", status: "available", native: true },
      { name: "Dynamics", kind: "CRM / sales", status: "available", native: true },
      { name: "Cornerstone", kind: "LMS", status: "available", native: true },
      { name: "Docebo", kind: "LMS", status: "available", native: true },
      { name: "LinkedIn Learning", kind: "LMS", status: "available", native: true },
      { name: "Degreed", kind: "LMS", status: "available", native: true },
    ],
  },
  {
    key: "warehouse", title: "Storage / warehouse", icon: "dashboard",
    blurb: "Data-in and data-out: warehouses, databases and object storage.",
    items: [
      { name: "Snowflake", kind: "Warehouse", status: "available" },
      { name: "Databricks", kind: "Lakehouse", status: "available" },
      { name: "BigQuery", kind: "Warehouse", status: "available" },
      { name: "Redshift", kind: "Warehouse", status: "available" },
      { name: "Amazon S3", kind: "Object storage", status: "available" },
      { name: "Microsoft SQL Server", kind: "Database", status: "available" },
      { name: "Postgres", kind: "Database", status: "available" },
      { name: "MySQL", kind: "Database", status: "available" },
      { name: "Oracle DB", kind: "Database", status: "available" },
      { name: "Azure Blob / GCS", kind: "Object storage", status: "available" },
    ],
  },
  {
    key: "generic", title: "Generic intake", icon: "download",
    blurb: "Table-stakes intake channels for anything without a dedicated connector.",
    items: [
      { name: "REST API", kind: "API pull", status: "available" },
      { name: "SFTP", kind: "Scheduled file drop", status: "available" },
      { name: "JDBC", kind: "Database pull", status: "available" },
      { name: "Manual file upload", kind: "CSV / XLSX", status: "available" },
      { name: "Webhook receiver", kind: "Event push", status: "available" },
    ],
  },
];

const TOTAL = GROUPS.reduce((n, g) => n + g.items.length, 0);

// Real brand marks. Each connector maps to the platform's own domain and we
// render its actual favicon logo via Google's favicon service — a colour mark
// for the real product (Workday, Slack, Salesforce, Snowflake…). This covers
// every named brand and degrades gracefully: anything without a domain, or
// whose logo fails to load, falls back to a tidy brand-coloured monogram so the
// grid never shows a broken image. Generic intake channels keep a plain icon.
const LOGO_DOMAIN: Record<string, string> = {
  // Core HRIS / HCM
  "Workday": "workday.com", "Oracle HCM / Fusion": "oracle.com", "SAP SuccessFactors": "sap.com",
  "UKG": "ukg.com", "Ceridian Dayforce": "dayforce.com", "BambooHR": "bamboohr.com",
  "HiBob": "hibob.com", "Paylocity": "paylocity.com", "Paycom": "paycom.com",
  "Namely": "namely.com", "Personio": "personio.com", "Darwinbox": "darwinbox.com",
  "Sage People": "sage.com", "Rippling": "rippling.com", "Gusto": "gusto.com",
  // Payroll & PEO
  "ADP": "adp.com", "Paychex": "paychex.com", "Deel": "deel.com", "TriNet": "trinet.com",
  "Insperity": "insperity.com", "Justworks": "justworks.com", "Remote": "remote.com",
  "iSolved": "isolvedhcm.com",
  // ATS / Recruiting
  "Greenhouse": "greenhouse.io", "Lever": "lever.co", "Ashby": "ashbyhq.com",
  "iCIMS": "icims.com", "Workday Recruiting": "workday.com", "SmartRecruiters": "smartrecruiters.com",
  "Workable": "workable.com",
  // Engagement / Survey
  "Workday Peakon": "peakon.com", "Qualtrics": "qualtrics.com", "Medallia": "medallia.com",
  "Microsoft Viva Glint": "microsoft.com", "Culture Amp": "cultureamp.com", "Lattice": "lattice.com",
  "Leapsome": "leapsome.com", "15Five": "15five.com", "Perceptyx": "perceptyx.com",
  // Comp benchmark / labour-market
  "Radford McLagan (Aon)": "aon.com", "Mercer": "mercer.com", "Pave": "pave.com",
  "Payscale": "payscale.com", "Lightcast": "lightcast.io", "Revelio Labs": "reveliolabs.com",
  "LinkedIn Talent Insights": "linkedin.com",
  // Cap table / equity
  "Carta": "carta.com", "Shareworks": "shareworks.com", "Pulley": "pulley.com", "Ledgy": "ledgy.com",
  // Work apps & collaboration
  "ClickUp": "clickup.com", "Asana": "asana.com", "Monday": "monday.com", "Linear": "linear.app",
  "Jira": "atlassian.com", "Trello": "trello.com", "Wrike": "wrike.com", "Smartsheet": "smartsheet.com",
  "Notion": "notion.so", "GitHub": "github.com", "GitLab": "gitlab.com", "Bitbucket": "bitbucket.org",
  "Azure DevOps": "azure.microsoft.com", "PagerDuty": "pagerduty.com",
  "Microsoft 365 / Graph": "microsoft.com", "Google Workspace": "google.com",
  "Slack": "slack.com", "Zoom": "zoom.us", "Salesforce": "salesforce.com", "HubSpot": "hubspot.com",
  "Dynamics": "microsoft.com", "Cornerstone": "cornerstoneondemand.com", "Docebo": "docebo.com",
  "LinkedIn Learning": "linkedin.com", "Degreed": "degreed.com",
  // Storage / warehouse
  "Snowflake": "snowflake.com", "Databricks": "databricks.com", "BigQuery": "cloud.google.com",
  "Redshift": "aws.amazon.com", "Amazon S3": "aws.amazon.com", "Microsoft SQL Server": "microsoft.com",
  "Postgres": "postgresql.org", "MySQL": "mysql.com", "Oracle DB": "oracle.com",
  "Azure Blob / GCS": "azure.microsoft.com",
};
// Brandless intake channels — keep the category icon rather than a monogram.
const GENERIC = new Set(["REST API", "SFTP", "JDBC", "Manual file upload", "Webhook receiver"]);

function initials(name: string): string {
  const words = name.replace(/[^A-Za-z0-9 ]/g, " ").trim().split(/\s+/);
  if (words.length > 1) return (words[0][0] + words[1][0]).toUpperCase();
  const w = words[0] ?? "?";
  return (w[0] ?? "?").toUpperCase() + (w[1] ? w[1].toLowerCase() : "");
}
function monoColor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 360;
  return `oklch(0.55 0.13 ${h})`;
}

// A connector's visual mark: the real brand logo when we have a domain, else a
// monogram. Logos are the product's own favicon (Google's favicon service); if
// one fails to load we fall back to a brand-coloured monogram so the grid never
// shows a broken image.
function BrandMark({ name, icon }: { name: string; icon: string }) {
  const domain = LOGO_DOMAIN[name];
  const [failed, setFailed] = useState(false);
  if (domain && !failed) {
    return (
      <div className="dm-conn-ico logo">
        <img src={`https://www.google.com/s2/favicons?domain=${domain}&sz=64`} alt="" loading="lazy"
             onError={() => setFailed(true)} />
      </div>
    );
  }
  if (GENERIC.has(name)) return <div className="dm-conn-ico"><Icon name={icon} size={18} /></div>;
  return <div className="dm-conn-ico mono" style={{ background: monoColor(name) }}>{initials(name)}</div>;
}

export function Connectors() {
  const { push } = useToast();
  const [q, setQ] = useState("");
  const [activeGroup, setActiveGroup] = useState<string>("all");
  // Connectors connected during this session (mock) — flips Available cards to Connected.
  const [connected, setConnected] = useState<Set<string>>(new Set());
  const [connecting, setConnecting] = useState<ConnDef | null>(null);

  const isConnected = (c: ConnDef) => c.status === "connected" || connected.has(c.name);

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return GROUPS
      .filter((g) => activeGroup === "all" || g.key === activeGroup)
      .map((g) => ({
        ...g,
        items: needle ? g.items.filter((c) => c.name.toLowerCase().includes(needle) || c.kind.toLowerCase().includes(needle)) : g.items,
      }))
      .filter((g) => g.items.length > 0);
  }, [q, activeGroup]);

  const connectedCount = GROUPS.reduce((n, g) => n + g.items.filter((c) => c.status === "connected").length, 0) + connected.size;

  const doConnect = (c: ConnDef) => {
    setConnected((s) => new Set(s).add(c.name));
    setConnecting(null);
    push(`${c.name} connected (local only, demo)`, "demo");
  };

  return (
    <div className="dir-wrap">
      <div className="dir-intro card">
        <div className="dir-intro-main">
          <div className="dir-intro-title"><Icon name="path" size={16} /> Connector directory <DemoBadge small /></div>
          <p className="dir-intro-sub">
            Every source here lands in the <b>same canonical employee record</b> and feeds the same
            Employees list, Profile, dashboards and score panel — connectors are an intake surface,
            not separate products. Adding a source is registration plus field mapping, never pipeline surgery.
          </p>
        </div>
        <div className="dir-intro-stat">
          <div><span className="dir-stat-n" style={{ color: "var(--risk-low)" }}>{connectedCount}</span> connected</div>
          <div><span className="dir-stat-n">{TOTAL}</span> available</div>
        </div>
      </div>

      <div className="dir-featured card">
        <div className="dir-featured-ico"><Icon name="globe" size={20} /></div>
        <div className="dir-featured-main">
          <div className="dir-featured-h">Unified HRIS API <span className="dir-tag native">breadth</span></div>
          <p className="dir-featured-sub">
            One adapter (Merge / Knit / Finch / Apideck) covers the long tail of HRIS, payroll and ATS systems
            through a single normalized contract — instant breadth without hand-building every native adapter.
          </p>
        </div>
        <button className="btn" onClick={() => setConnecting({ name: "Unified HRIS API", kind: "Merge / Knit / Finch / Apideck", status: "available" })}>
          <Icon name="plus" size={14} /> Connect
        </button>
      </div>

      <div className="dir-filter">
        <div className="dir-search">
          <Icon name="search" size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${TOTAL} connectors…`} />
        </div>
        <div className="dir-chips">
          <button className={`dir-chip${activeGroup === "all" ? " on" : ""}`} onClick={() => setActiveGroup("all")}>All</button>
          {GROUPS.map((g) => (
            <button key={g.key} className={`dir-chip${activeGroup === g.key ? " on" : ""}`} onClick={() => setActiveGroup(g.key)}>
              <Icon name={g.icon} size={12} /> {g.title}
            </button>
          ))}
        </div>
      </div>

      {groups.map((g) => (
        <div className="dir-group" key={g.key}>
          <div className="dir-group-h">
            <Icon name={g.icon} size={15} />
            <span className="dir-group-title">{g.title}</span>
            <span className="dir-group-count">{g.items.length}</span>
            <span className="dir-group-blurb">{g.blurb}</span>
          </div>
          <div className="dm-conn-grid">
            {g.items.map((c) => {
              const conn = isConnected(c);
              return (
                <div className="dm-conn-tile" key={c.name}>
                  <div className="dm-conn-top">
                    <BrandMark name={c.name} icon={g.icon} />
                    <span className={`dm-conn-status ${conn ? "connected" : "available"}`}>{conn ? "Connected" : "Available"}</span>
                  </div>
                  <div className="dm-conn-name">{c.name}</div>
                  <div className="dm-conn-kind">{c.kind}</div>
                  {(c.native || c.consent) && (
                    <div className="dir-tags">
                      {c.native && <span className="dir-tag native">native</span>}
                      {c.consent && <span className="dir-tag consent"><Icon name="lock" size={10} /> consent-gated · metadata only</span>}
                    </div>
                  )}
                  <div className="dm-conn-foot">
                    <span className="dm-conn-last">{conn ? "Synced · in pipeline" : "Not configured"}</span>
                    {conn
                      ? <button className="btn ghost sm" onClick={() => push(`${c.name} settings are read-only in the demo`, "demo")}><Icon name="settings" size={13} /> Configure</button>
                      : <button className="btn ghost sm" onClick={() => setConnecting(c)}><Icon name="plus" size={13} /> Connect</button>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      {/* Tier 0 document-native intake mode — referenced, not duplicated. */}
      <div className="dir-doc card">
        <div className="dir-doc-ico"><Icon name="file" size={18} /></div>
        <div className="dir-doc-main">
          <div className="dir-doc-h">Document-native import <span className="dir-tag native">new intake mode</span></div>
          <p className="dir-doc-sub">
            For companies with no HRIS: PDFs, scans, contracts, CVs and spreadsheets routed through
            classify, extract, normalize and resolve into the same canonical schema. This intake mode
            already lives in the <b>Document Import</b> tab — it coexists with the connectors above.
          </p>
        </div>
        <span className="dir-doc-ref"><Icon name="arrowup" size={13} className="dir-doc-arrow" /> Document Import tab</span>
      </div>

      {connecting && <ConnectModal conn={connecting} onClose={() => setConnecting(null)} onConnect={() => doConnect(connecting)} />}
    </div>
  );
}

// Mocked connect flow. No credentials are submitted or stored — the fields are
// illustrative of the auth handshake and the whole modal is badged demo.
const AUTH_BY_KIND = (kind: string): string => {
  if (/comms metadata/i.test(kind)) return "OAuth (consent-gated, metadata scopes only)";
  if (/sftp|file/i.test(kind)) return "SFTP key-pair (per-tenant drop zone)";
  if (/database|jdbc|warehouse|lakehouse|object storage/i.test(kind)) return "Connection string (read-only role)";
  if (/api pull|event push/i.test(kind)) return "API key / OAuth client-credentials";
  return "OAuth 2.0 client-credentials";
};

function ConnectModal({ conn, onClose, onConnect }: { conn: ConnDef; onClose: () => void; onConnect: () => void }) {
  return (
    <div className="dir-modal-backdrop" onClick={onClose}>
      <div className="dir-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dir-modal-head">
          <div className="card-title"><Icon name="path" size={15} /> Connect {conn.name}</div>
          <button className="dm-rowbtn" onClick={onClose}><Icon name="close" size={15} /></button>
        </div>
        <p className="dir-modal-sub">{conn.kind} · maps to the canonical schema and lands in BRONZE → SILVER → GOLD alongside every other source.</p>

        <div className="dir-modal-field">
          <label>Authentication</label>
          <div className="dir-modal-auth">{AUTH_BY_KIND(conn.kind)}</div>
        </div>
        {conn.consent && (
          <div className="dir-modal-consent">
            <Icon name="shield" size={14} />
            <span>Comms-metadata only — message bodies, keystrokes and content are never ingested. Requires explicit per-employee monitoring consent before any signal is stored.</span>
          </div>
        )}
        <div className="dir-modal-field">
          <label>Field mapping</label>
          <div className="dir-modal-map">Source fields auto-suggested against the canonical schema — you confirm before anything imports. New fields are added nullable; existing fields are never renamed or dropped.</div>
        </div>

        <div className="dir-modal-foot">
          <DemoBadge />
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={onConnect}><Icon name="check" size={14} /> Connect (demo)</button>
        </div>
      </div>
    </div>
  );
}
