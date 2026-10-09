import { useEffect, useMemo, useState } from "react";
import { api, EmployeeRow } from "../api/client";
import { NameTag } from "../components/NameTag";
import { SkeletonTable } from "../components/ui";
import { Icon } from "../components/icons";
import { riskBand, riskColor, titleCase } from "../lib/format";

type SortKey = "division" | "flight_risk" | "delta" | "value" | "comp" | "tenure";
type Band = "all" | "high" | "med" | "low";

const BANDS: [Band, string, string | null][] = [
  ["all", "All", null],
  ["high", "High", "high"],
  ["med", "Medium", "med"],
  ["low", "Low", "low"],
];

// Business value 0–100 → 5-pip tier; 0 = no score.
function valueTier(v: number | null | undefined): number {
  if (v == null) return 0;
  return Math.max(1, Math.min(5, Math.round(v / 20)));
}

export function Watchlist() {
  const [all, setAll] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [band, setBand] = useState<Band>("all");
  const [q, setQ] = useState("");
  const [div, setDiv] = useState("all");
  const [sort, setSort] = useState<SortKey>("flight_risk");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  // One in-scope fetch; band/search/division/sort applied client-side so the
  // segmented counts stay accurate. No per-row /360 calls.
  useEffect(() => {
    api.employees().then(setAll).catch(() => setErr(true));
  }, []);

  const divisions = useMemo(
    () => (all ? Array.from(new Set(all.map((r) => r.division))).sort() : []),
    [all]
  );

  const counts = useMemo(() => {
    const c: Record<Band, number> = { all: 0, high: 0, med: 0, low: 0 };
    (all ?? []).forEach((r) => {
      c.all++;
      const b = riskBand(r.latest_score?.flight_risk);
      if (b === "high") c.high++;
      else if (b === "med") c.med++;
      else if (b === "low") c.low++;
    });
    return c;
  }, [all]);

  const rows = useMemo(() => {
    if (!all) return [];
    const ql = q.trim().toLowerCase();
    const out = all.filter((r) => {
      const b = riskBand(r.latest_score?.flight_risk);
      if (band !== "all" && b !== band) return false;
      if (div !== "all" && r.division !== div) return false;
      if (ql && !`${r.token} ${r.role} ${r.division}`.toLowerCase().includes(ql)) return false;
      return true;
    });
    const val = (r: EmployeeRow): number | string => {
      switch (sort) {
        case "division": return r.division;
        case "flight_risk": return r.latest_score?.flight_risk ?? -1;
        case "delta": return r.latest_score?.risk_trend ?? 0;
        case "value": return r.latest_score?.value_score ?? -1;
        case "comp": return r.features?.comp_gap ?? Number.NEGATIVE_INFINITY;
        case "tenure": return r.features?.tenure_months ?? -1;
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
  }, [all, band, div, q, sort, dir]);

  const onSort = (k: SortKey) => {
    if (k === sort) setDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSort(k); setDir("desc"); }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Flight-Risk Watchlist</div>
          <div className="page-sub">
            <span><b style={{ color: "var(--text)" }}>{rows.length}</b> employees shown</span>
            <span className="dot-sep" />
            <span className="mono">{counts.high} high · {counts.med} medium · {counts.low} low</span>
            <span className="dot-sep" />
            <span>Ranked by predictive flight-risk score</span>
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
        <div className="seg">
          {BANDS.map(([key, label, sev]) => (
            <button key={key} className={band === key ? "on" : ""} onClick={() => setBand(key)}>
              {sev && (
                <span className="bdot" style={{ width: 7, height: 7, borderRadius: "50%", background: `var(--risk-${sev === "med" ? "med" : sev})` }} />
              )}
              {label} <span className="mono" style={{ fontSize: 11, color: "var(--text-4)" }}>{counts[key]}</span>
            </button>
          ))}
        </div>
        <div className="search" style={{ width: 220 }}>
          <Icon name="search" size={15} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search roles, divisions, tokens…" />
        </div>
        <select className="chip-filter" value={div} onChange={(e) => setDiv(e.target.value)}
                style={{ appearance: "none", paddingRight: 26 }}>
          <option value="all">All divisions</option>
          {divisions.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <div className="spacer" />
        <span className="subtle">Sorted by <b style={{ color: "var(--text-2)" }}>{sort === "comp" ? "comp gap" : sort === "delta" ? "Δ QoQ" : sort.replace("_", " ")}</b> ({dir})</span>
      </div>

      {err ? (
        <p className="flash err">Could not load the watchlist.</p>
      ) : !all ? (
        <SkeletonTable rows={9} cols={8} widths={["46%", "60%", "70%", "40%", "70%", "55%", "50%", "62%"]} />
      ) : (
        <>
          <div className="card" style={{ overflow: "hidden" }}>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Employee</th>
                    <Th k="division" sort={sort} dir={dir} onSort={onSort}>Division</Th>
                    <Th k="flight_risk" sort={sort} dir={dir} onSort={onSort} num>Flight Risk</Th>
                    <Th k="delta" sort={sort} dir={dir} onSort={onSort} num>Δ QoQ</Th>
                    <Th k="value" sort={sort} dir={dir} onSort={onSort} num>Value</Th>
                    <Th k="comp" sort={sort} dir={dir} onSort={onSort} num>Comp Gap</Th>
                    <Th k="tenure" sort={sort} dir={dir} onSort={onSort} num>Tenure</Th>
                    <th>Top Risk Drivers</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const risk = r.latest_score?.flight_risk ?? null;
                    const delta = r.latest_score?.risk_trend ?? null;
                    const dRound = delta == null ? null : Math.round(delta);
                    const tier = valueTier(r.latest_score?.value_score);
                    const comp = r.features?.comp_gap ?? null;
                    return (
                      <tr key={r.token} onClick={() => { window.location.hash = `#/profile/${r.token}`; }}>
                        <td>
                          <div className="emp-cell">
                            <div>
                              <div className="emp-name"><NameTag token={r.token} /></div>
                              <div className="emp-meta">{r.role} <span className="emp-id">{r.token}</span></div>
                            </div>
                          </div>
                        </td>
                        <td><span className="subtle" style={{ color: "var(--text-2)" }}>{r.division}</span></td>
                        <td className="td-num">
                          {risk == null ? <span className="code">no score</span> : (
                            <div className="scorebar">
                              <span className="track"><span className="fill" style={{ width: `${risk}%`, background: riskColor(risk) }} /></span>
                              <span className="sv" style={{ color: riskColor(risk) }}>{risk}</span>
                            </div>
                          )}
                        </td>
                        <td className="td-num">
                          {dRound == null ? <span className="subtle">—</span> : (
                            <span className={`delta ${dRound > 0 ? "bad-up" : dRound < 0 ? "good-down" : ""}`}>
                              {dRound !== 0 && <Icon name={dRound > 0 ? "arrowup" : "arrowdown"} size={11} />}
                              {dRound > 0 ? "+" : ""}{dRound}
                            </span>
                          )}
                        </td>
                        <td className="td-num">
                          <span className="tier" title={`Business value tier ${tier}/5`}>
                            {[1, 2, 3, 4, 5].map((n) => <i key={n} className={n <= tier ? "on" : ""} />)}
                          </span>
                        </td>
                        <td className="td-num mono" style={{ color: comp != null && comp > 0 ? "var(--risk-high)" : "var(--text-2)" }}>
                          {comp == null ? "—" : `${Math.round(comp * 100)}%`}
                        </td>
                        <td className="td-num mono" style={{ color: "var(--text-2)" }}>
                          {r.features ? `${(r.features.tenure_months / 12).toFixed(1)}y` : "—"}
                        </td>
                        <td>
                          <div className="codes">
                            {r.reason_codes.length === 0 && <span className="subtle">—</span>}
                            {r.reason_codes.slice(0, 3).map((c, i) => (
                              <span key={i} className={`code ${c.direction === "increases" ? "sev" : "mod"}`}>{titleCase(c.label)}</span>
                            ))}
                            {r.reason_codes.length > 3 && <span className="code">+{r.reason_codes.length - 3}</span>}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!rows.length && (
                    <tr><td colSpan={8} className="subtle" style={{ padding: 18 }}>No employees match this filter.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="subtle" style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name="shield" size={13} /> Predictions are decision-support only. Access logged · reviewed by People Analytics governance.
          </div>
        </>
      )}
    </div>
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
