import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as RMouseEvent } from "react";
import { api, EmployeeRow } from "../api/client";
import { NameTag } from "../components/NameTag";
import { Avatar } from "../components/Avatar";
import { Skeleton } from "../components/ui";
import { Icon } from "../components/icons";
import { riskBand, riskColor } from "../lib/format";
import { avatarFill, initials } from "../lib/avatar";

type Band = "all" | "high" | "med" | "low";

// One conversational exchange: the user's question + the agent's grounded answer and the
// token set it matched (which lights up the graph). `null` answer = still thinking.
interface Turn {
  id: number;
  question: string;
  answer: string | null;
  tokens: string[];
  note?: string | null;
  unsupported?: string[];   // requested fields the schema doesn't carry (flagged, not faked)
  error?: boolean;
  pending?: boolean;
}

// Starter prompts — multi-field, natural-language people searches across the canonical record.
const SUGGESTIONS: { tag: string; color: string; q: string }[] = [
  { tag: "Skills", color: "var(--accent)", q: "Senior people in risk & compliance with a CFA" },
  { tag: "Risk", color: "var(--risk-high)", q: "High-value people in technology who aren't a flight risk" },
  { tag: "Location", color: "var(--text-3)", q: "Engineers in Singapore at level 5 or above" },
  { tag: "Credentials", color: "var(--accent)", q: "Who holds an FRM license in front office?" },
  { tag: "Mixed", color: "var(--risk-high)", q: "People who speak French and have a visa to work in Latin America — are they available?" },
];

interface TNode {
  token: string;
  row: EmployeeRow | null;
  isRoot: boolean;
  children: TNode[];
  x: number;
  y: number;
  depth: number;
}

// Build the reporting tree from manager_token. A single org root is used as-is;
// multiple roots are gathered under one synthetic, non-clickable "Organization" node.
function buildTree(rows: EmployeeRow[]): TNode | null {
  if (!rows.length) return null;
  const byToken = new Map(rows.map((r) => [r.token, r]));
  const nodes = new Map<string, TNode>(
    rows.map((r) => [r.token, { token: r.token, row: r, isRoot: false, children: [], x: 0, y: 0, depth: 0 }])
  );
  const roots: TNode[] = [];
  for (const r of rows) {
    const node = nodes.get(r.token)!;
    const parent = r.manager_token;
    if (parent && byToken.has(parent) && parent !== r.token) nodes.get(parent)!.children.push(node);
    else roots.push(node);
  }
  const sortRec = (n: TNode) => {
    n.children.sort((a, b) => (b.row!.latest_score?.flight_risk ?? -1) - (a.row!.latest_score?.flight_risk ?? -1));
    n.children.forEach(sortRec);
  };
  roots.forEach(sortRec);
  roots.sort((a, b) => b.children.length - a.children.length);
  if (roots.length === 1) {
    roots[0].isRoot = true;
    return roots[0];
  }
  return { token: "__root__", row: null, isRoot: true, children: roots, x: 0, y: 0, depth: 0 };
}

// ── tidy-tree layout (ported from the design reference) ──────────────────────
const NODE_W = 190, NODE_H = 66, H_GAP = 22, V_GAP = 66, PAD = 40;

function layout(root: TNode) {
  const all: TNode[] = [];
  let leafX = 0;
  const walk = (n: TNode, depth: number) => {
    n.depth = depth;
    if (!n.children.length) { n.x = leafX * (NODE_W + H_GAP); leafX++; }
    else {
      n.children.forEach((c) => walk(c, depth + 1));
      n.x = (n.children[0].x + n.children[n.children.length - 1].x) / 2;
    }
    n.y = depth * (NODE_H + V_GAP);
    all.push(n);
  };
  walk(root, 0);
  const maxX = Math.max(...all.map((n) => n.x)) + NODE_W;
  const maxY = Math.max(...all.map((n) => n.y)) + NODE_H;
  return { nodes: all, w: maxX + PAD * 2, h: maxY + PAD * 2 };
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function clip(s: string, n: number): string { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

// Build the org SVG. `names` resolves tokens→display names (identity boundary); `photos`
// holds the real profile pictures (those that exist); `matched` (when present) highlights
// filter hits and dims the rest. A node shows the photo when available, else a deterministic
// colored-initials avatar, plus name · role · division/location and the risk score.
function buildSvg(root: TNode, names: Map<string, string>, photos: Map<string, string>,
                  matched: Set<string> | null) {
  const L = layout(root);
  let s = `<svg id="orgsvg" viewBox="0 0 ${L.w} ${L.h}" width="${L.w}" height="${L.h}" style="display:block">`;
  L.nodes.forEach((n) => {
    n.children.forEach((c) => {
      const x1 = n.x + PAD + NODE_W / 2, y1 = n.y + PAD + NODE_H;
      const x2 = c.x + PAD + NODE_W / 2, y2 = c.y + PAD;
      const my = (y1 + y2) / 2;
      const dim = matched && !matched.has(c.token) && !c.isRoot;
      s += `<path d="M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}" fill="none" stroke="var(--border-2)" stroke-width="1.4" opacity="${dim ? 0.25 : 0.9}"/>`;
    });
  });
  L.nodes.forEach((n) => {
    const x = n.x + PAD, y = n.y + PAD;
    const r = n.row;
    const risk = r?.latest_score?.flight_risk ?? null;
    const hit = !matched || matched.has(n.token) || n.isRoot;
    const isMatch = !!matched && matched.has(n.token);
    const op = hit ? 1 : 0.32;
    const stroke = isMatch ? "var(--accent)" : "var(--border)";
    const sw = isMatch ? 2.2 : 1;
    const name = names.get(n.token) ?? (n.token === "__root__" ? "Organization" : n.token);
    const role = n.isRoot && n.token === "__root__" ? `${L.nodes.length - 1} people` : (r?.role ?? "");
    const meta = n.isRoot ? "" : [r?.division, r?.location].filter(Boolean).join(" · ");
    const tx = x + 58;                                  // text column (right of the avatar)
    const ax = x + 32, ay = y + NODE_H / 2, ar = 18;    // avatar center + radius
    s += `<g class="orgnode" data-id="${n.token}" data-root="${n.isRoot ? 1 : 0}" style="cursor:${n.isRoot ? "default" : "pointer"};opacity:${op}">`;
    s += `<rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="11" fill="var(--surface)" stroke="${stroke}" stroke-width="${sw}"${isMatch ? ' filter="url(#mglow)"' : ""}/>`;
    if (!n.isRoot) s += `<rect x="${x}" y="${y}" width="4" height="${NODE_H}" rx="2" fill="${riskColor(risk)}"/>`;
    // avatar: real photo (clipped to a circle) when present, else a colored-initials disc
    const photo = n.isRoot ? undefined : photos.get(n.token);
    if (photo) {
      const cid = `av-${n.token}`;
      s += `<clipPath id="${cid}"><circle cx="${ax}" cy="${ay}" r="${ar}"/></clipPath>`;
      s += `<image href="${photo}" x="${ax - ar}" y="${ay - ar}" width="${ar * 2}" height="${ar * 2}" clip-path="url(#${cid})" preserveAspectRatio="xMidYMid slice"/>`;
      s += `<circle cx="${ax}" cy="${ay}" r="${ar}" fill="none" stroke="var(--border)" stroke-width="1"/>`;
    } else {
      const fill = n.isRoot ? "var(--surface-3)" : avatarFill(n.token);
      const tcol = n.isRoot ? "var(--text-3)" : "#fff";
      s += `<circle cx="${ax}" cy="${ay}" r="${ar}" fill="${fill}" stroke="var(--border)" stroke-width="1"/>`;
      s += `<text x="${ax}" y="${ay + 4}" text-anchor="middle" font-size="12.5" font-weight="600" fill="${tcol}" font-family="var(--sans)">${esc(initials(name))}</text>`;
    }
    s += `<text x="${tx}" y="${y + 24}" font-size="12.5" font-weight="600" fill="var(--text)" font-family="var(--sans)">${esc(clip(name, 17))}</text>`;
    s += `<text x="${tx}" y="${y + 39}" font-size="10" fill="var(--text-3)" font-family="var(--sans)">${esc(clip(role, 22))}</text>`;
    if (meta) s += `<text x="${tx}" y="${y + 53}" font-size="9" fill="var(--text-4)" font-family="var(--sans)">${esc(clip(meta, 26))}</text>`;
    if (!n.isRoot && risk != null) s += `<text x="${x + NODE_W - 9}" y="${y + 17}" text-anchor="end" font-size="9.5" font-weight="600" fill="${riskColor(risk)}" font-family="var(--mono)">${risk}</text>`;
    s += "</g>";
  });
  s += '<defs><filter id="mglow" x="-30%" y="-30%" width="160%" height="160%"><feDropShadow dx="0" dy="0" stdDeviation="5" flood-color="var(--accent)" flood-opacity="0.5"/></filter></defs>';
  s += "</svg>";
  return { html: s, w: L.w, h: L.h, rootCx: root.x + PAD + NODE_W / 2 };
}

export function Graph() {
  const [rows, setRows] = useState<EmployeeRow[] | null>(null);
  const [err, setErr] = useState(false);
  const [draft, setDraft] = useState("");
  const [chat, setChat] = useState<Turn[]>([]);
  const [asking, setAsking] = useState(false);
  const [aiTokens, setAiTokens] = useState<Set<string> | null>(null);
  const [div, setDiv] = useState("all");
  const [loc, setLoc] = useState("all");
  const [band, setBand] = useState<Band>("all");
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const [photos, setPhotos] = useState<Map<string, string>>(new Map());
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.employees().then(setRows).catch(() => setErr(true));
  }, []);

  // Resolve every visible token once, through the single identity boundary, so the
  // SVG can label nodes by name (falls back to the token when resolution is denied).
  useEffect(() => {
    if (!rows?.length) return;
    let alive = true;
    api.resolveNames(rows.map((r) => r.token))
      .then((m) => { if (alive) setNames(new Map(Object.entries(m))); })
      .catch(() => {});
    return () => { alive = false; };
  }, [rows]);

  // Profile pictures, through the same identity boundary — only those that exist come back,
  // so nodes without one keep the generated avatar.
  useEffect(() => {
    if (!rows?.length) return;
    let alive = true;
    api.resolvePhotos(rows.map((r) => r.token))
      .then((m) => { if (alive) setPhotos(new Map(Object.entries(m))); })
      .catch(() => {});
    return () => { alive = false; };
  }, [rows]);

  // Keep the transcript pinned to the newest message.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat, asking]);

  const byToken = useMemo(() => new Map((rows ?? []).map((r) => [r.token, r])), [rows]);
  const divisions = useMemo(() => rows ? Array.from(new Set(rows.map((r) => r.division))).sort() : [], [rows]);
  const locations = useMemo(() => rows ? Array.from(new Set(rows.map((r) => r.location))).sort() : [], [rows]);
  const tree = useMemo(() => rows ? buildTree(rows) : null, [rows]);

  const chipActive = div !== "all" || loc !== "all" || band !== "all";
  const aiActive = aiTokens !== null;
  const hasCriteria = chipActive || aiActive;

  // The graph/list highlight = structured chips AND (when an AI question is focused) its
  // matched token set. So the chips refine an AI result, and either works on its own.
  const matches = useMemo(() => {
    if (!rows) return new Set<string>();
    const set = new Set<string>();
    for (const r of rows) {
      if (div !== "all" && r.division !== div) continue;
      if (loc !== "all" && r.location !== loc) continue;
      if (band !== "all" && riskBand(r.latest_score?.flight_risk) !== band) continue;
      if (aiTokens && !aiTokens.has(r.token)) continue;
      set.add(r.token);
    }
    return set;
  }, [rows, div, loc, band, aiTokens]);

  const matchedRows = useMemo(
    () => (rows ?? []).filter((r) => matches.has(r.token))
      .sort((a, b) => (b.latest_score?.flight_risk ?? -1) - (a.latest_score?.flight_risk ?? -1)),
    [rows, matches]
  );

  const graph = useMemo(
    () => (tree ? buildSvg(tree, names, photos, hasCriteria ? matches : null) : null),
    [tree, names, photos, matches, hasCriteria]
  );

  const pz = usePanZoom(graph?.w ?? 0, graph?.h ?? 0, graph?.rootCx ?? 0);

  // Ask the LLM-backed people-search agent. It parses the free-text request into structured
  // criteria across ALL canonical fields, retrieves the matching people through the scoped
  // query API (token-only — the model never sees a name), and returns a grounded NL `answer`
  // plus the matched token set (which lights up the graph and intersects with the chips).
  // Fields the schema doesn't carry come back in `unsupported_fields` — surfaced, never faked.
  const ask = useCallback(async (text?: string) => {
    const question = (text ?? draft).trim();
    if (!question || asking) return;
    setDraft("");
    setAsking(true);
    const id = Date.now();
    // Prior turns, so the agent can follow the thread (answered turns only).
    const history = chat.flatMap((t) => t.answer
      ? [{ role: "user" as const, content: t.question }, { role: "assistant" as const, content: t.answer }]
      : []);
    setChat((c) => [...c, { id, question, answer: null, tokens: [], pending: true }]);
    try {
      const res = await api.searchPeople(question, history);
      const tokens = (res.matched ?? []).map((m) => m.token);
      setChat((c) => c.map((t) => t.id === id
        ? { ...t, pending: false, answer: String(res.answer ?? ""), tokens,
            note: res.note ?? null, unsupported: res.unsupported_fields }
        : t));
      setAiTokens(new Set(tokens));  // focus the result (empty set = "matched no one")
    } catch {
      setChat((c) => c.map((t) => t.id === id
        ? { ...t, pending: false, answer: "Sorry — the search agent could not answer. Try the filters above.", tokens: [], error: true }
        : t));
      setAiTokens(null);
    } finally {
      setAsking(false);
    }
  }, [draft, asking, chat]);

  const renderMini = (r: EmployeeRow | null | undefined, key?: string) => {
    if (!r) return null;
    const risk = r.latest_score?.flight_risk ?? null;
    const b = riskBand(risk);
    return (
      <a key={key ?? r.token} className="mini-emp" href={`#/profile/${r.token}`}>
        <span className="mini-emp-av">
          <Avatar token={r.token} name={names.get(r.token)} photo={photos.get(r.token)} size={30} />
          <span className="org-rdot" style={{ background: riskColor(risk) }} />
        </span>
        <div className="mini-emp-body">
          <div className="mini-emp-name"><NameTag token={r.token} /></div>
          <div className="mini-emp-meta">{r.role} · {r.location}</div>
        </div>
        {risk != null && <span className={`band ${b}`} style={{ fontSize: 10.5 }}><span className="bdot" />{risk}</span>}
        <span className="mini-go"><Icon name="chevright" size={14} /></span>
      </a>
    );
  };

  const reset = () => { setDraft(""); setChat([]); setAiTokens(null); setDiv("all"); setLoc("all"); setBand("all"); };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Company Graph</div>
          <div className="page-sub">
            <span>{rows ? `${rows.length} people` : "—"} · reporting hierarchy</span>
            <span className="dot-sep" />
            <span>Ask in natural language · structured filters · token-keyed</span>
          </div>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={reset}><Icon name="close" size={14} /> Clear</button>
        </div>
      </div>

      <div className="gfilters">
        <select className="chip-filter" value={div} onChange={(e) => setDiv(e.target.value)}>
          <option value="all">All divisions</option>
          {divisions.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select className="chip-filter" value={loc} onChange={(e) => setLoc(e.target.value)}>
          <option value="all">All locations</option>
          {locations.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        <select className="chip-filter" value={band} onChange={(e) => setBand(e.target.value as Band)}>
          <option value="all">Any risk</option>
          <option value="high">High risk</option>
          <option value="med">Medium risk</option>
          <option value="low">Low risk</option>
        </select>
        <select className="chip-filter" disabled title="Language data not yet available" style={{ opacity: 0.5 }}>
          <option>Any language (n/a)</option>
        </select>
        <select className="chip-filter" disabled title="Certification data not yet available" style={{ opacity: 0.5 }}>
          <option>Any certification (n/a)</option>
        </select>
        {hasCriteria && <span className="g-count">{matchedRows.length} match{matchedRows.length === 1 ? "" : "es"}</span>}
      </div>

      {err ? (
        <p className="flash err">Could not load the company graph.</p>
      ) : !rows ? (
        <div className="graph-layout">
          <div className="card graph-card">
            <div className="graph-viewport" style={{ display: "grid", placeItems: "center" }}>
              <div className="sk-stagger" style={{ display: "grid", gap: 30, justifyItems: "center" }}>
                <Skeleton className="sk-circle" w={58} h={58} />
                <div style={{ display: "flex", gap: 34 }}>
                  {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="sk-circle" w={44} h={44} />)}
                </div>
                <div style={{ display: "flex", gap: 22 }}>
                  {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="sk-circle" w={32} h={32} />)}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="graph-layout">
          <div className="card graph-card">
            <div
              className="graph-viewport"
              ref={pz.viewportRef}
              onMouseDown={pz.onMouseDown}
              onClick={(e) => {
                const g = (e.target as HTMLElement).closest(".orgnode") as HTMLElement | null;
                if (g && g.dataset.root !== "1" && g.dataset.id) window.location.hash = `#/profile/${g.dataset.id}`;
              }}
            >
              <div className="graph-pan" ref={pz.panRef} dangerouslySetInnerHTML={{ __html: graph?.html ?? "" }} />
              <div className="graph-controls">
                <button className="icon-btn" title="Zoom in" onClick={() => pz.zoom(1.2)}><Icon name="zoomin" size={16} /></button>
                <button className="icon-btn" title="Zoom out" onClick={() => pz.zoom(0.83)}><Icon name="zoomout" size={16} /></button>
                <button className="icon-btn" title="Fit" onClick={() => pz.fit()}><Icon name="target" size={16} /></button>
              </div>
              <div className="graph-legend">
                <span className="legend-item"><span className="sw" style={{ background: "var(--risk-high)" }} /> High</span>
                <span className="legend-item"><span className="sw" style={{ background: "var(--risk-med)" }} /> Med</span>
                <span className="legend-item"><span className="sw" style={{ background: "var(--risk-low)" }} /> Low</span>
              </div>
            </div>
          </div>

          <div className="graph-rail">
            <div className="card chat-card">
              <div className="chat-head">
                <div className="chat-head-l">
                  <span className="chat-orb"><Icon name="spark2" size={15} /></span>
                  <div>
                    <div className="chat-head-t">Ask the graph</div>
                    <div className="chat-head-s">Conversational · grounded in scores · token-keyed</div>
                  </div>
                </div>
                <span className="chat-live"><span className="chat-live-dot" />{rows.length} indexed</span>
              </div>

              <div className="chat-scroll" ref={scrollRef}>
                {chat.length === 0 ? (
                  chipActive ? (
                    <div className="msg-results">
                      {matchedRows.length === 0 && <div className="subtle">No people match these filters.</div>}
                      {matchedRows.slice(0, 40).map((r) => renderMini(r))}
                      {matchedRows.length > 40 && <div className="msg-more">+ {matchedRows.length - 40} more highlighted on the graph →</div>}
                    </div>
                  ) : (
                    <>
                      <div className="chat-intro">
                        <div className="chat-intro-t">Ask about your people</div>
                        <div className="chat-intro-s">Ask in plain language — I read the scored data and light up the matching people on the graph. Try:</div>
                      </div>
                      <div className="suggest-list">
                        {SUGGESTIONS.map((s) => (
                          <button key={s.q} className="suggest" onClick={() => ask(s.q)}>
                            <span className="suggest-tag" style={{ color: s.color }}>{s.tag}</span>
                            <span className="suggest-q">{s.q}</span>
                            <span className="suggest-go"><Icon name="arrowup" size={13} style={{ transform: "rotate(45deg)" }} /></span>
                          </button>
                        ))}
                      </div>
                    </>
                  )
                ) : (
                  chat.map((t) => (
                    <Fragment key={t.id}>
                      <div className="msg user">{t.question}</div>
                      <div className="msg ai">
                        <div className="msg-ai-head">
                          <span className="chat-orb sm"><Icon name="spark2" size={11} /></span>
                          Talent88 AI
                        </div>
                        {t.pending ? (
                          <div className="msg-desc subtle">Thinking…</div>
                        ) : (
                          <>
                            <div className="msg-desc">{t.answer}</div>
                            {t.unsupported && t.unsupported.length > 0 && (
                              <div className="caveat" style={{ marginTop: 6 }}>
                                Not stored yet (so not searched): {t.unsupported.join(", ")} — flagged to add.
                              </div>
                            )}
                            {t.tokens.length > 0 && (
                              <div className="msg-results">
                                {t.tokens.slice(0, 12).map((tok) => renderMini(byToken.get(tok), tok))}
                                {t.tokens.length > 12 && <div className="msg-more">+ {t.tokens.length - 12} more highlighted on the graph →</div>}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    </Fragment>
                  ))
                )}
              </div>

              <div className="chat-bar">
                <div className="chat-input-wrap">
                  <Icon name="spark2" size={15} style={{ color: "var(--text-4)", flexShrink: 0 }} />
                  <input value={draft} onChange={(e) => setDraft(e.target.value)}
                         onKeyDown={(e) => e.key === "Enter" && ask()}
                         placeholder="Find people: skills, certs, location, level, risk…" />
                  <button className="chat-send" onClick={() => ask()} disabled={asking || !draft.trim()}
                          title="Ask" aria-label="Ask">
                    <Icon name="arrowup" size={16} />
                  </button>
                </div>
                <div className="chat-hint">
                  {aiActive ? (
                    <button type="button" onClick={() => setAiTokens(null)}
                            style={{ background: "none", border: "none", color: "var(--accent)", font: "inherit", cursor: "pointer", padding: 0 }}>
                      Clear focus · {matchedRows.length} on graph
                    </button>
                  ) : (
                    <><Icon name="shield" size={12} /> Token-keyed · names resolved per the identity boundary</>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// rAF-smoothed pan/zoom for the org SVG (ported from the design reference): eased
// drag with inertia, cursor-anchored wheel zoom, and fit-to-viewport.
function usePanZoom(w: number, h: number, rootCx: number) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const panRef = useRef<HTMLDivElement>(null);
  const dims = useRef({ w, h, rootCx });
  dims.current = { w, h, rootCx };
  const cur = useRef({ zoom: 1, x: 0, y: 0 });
  const tgt = useRef({ zoom: 1, x: 0, y: 0 });
  const vel = useRef({ x: 0, y: 0 });
  const raf = useRef<number | null>(null);
  const dragging = useRef(false);

  const apply = useCallback(() => {
    const p = panRef.current, c = cur.current;
    if (p) p.style.transform = `translate3d(${c.x.toFixed(2)}px,${c.y.toFixed(2)}px,0) scale(${c.zoom.toFixed(4)})`;
  }, []);

  const loop = useCallback(() => {
    const ease = 0.18, c = cur.current, t = tgt.current, v = vel.current;
    const dz = t.zoom - c.zoom, dx = t.x - c.x, dy = t.y - c.y;
    if (!dragging.current && (Math.abs(v.x) > 0.1 || Math.abs(v.y) > 0.1)) {
      t.x += v.x; t.y += v.y; v.x *= 0.92; v.y *= 0.92;
    }
    c.zoom += dz * ease; c.x += dx * ease; c.y += dy * ease;
    apply();
    if (Math.abs(dz) > 0.0005 || Math.abs(dx) > 0.3 || Math.abs(dy) > 0.3 || Math.abs(v.x) > 0.1 || Math.abs(v.y) > 0.1) {
      raf.current = requestAnimationFrame(loop);
    } else { c.zoom = t.zoom; c.x = t.x; c.y = t.y; apply(); raf.current = null; }
  }, [apply]);

  const kick = useCallback(() => { if (raf.current == null) raf.current = requestAnimationFrame(loop); }, [loop]);

  const fit = useCallback((snap?: boolean) => {
    const vp = viewportRef.current, { w, h } = dims.current;
    if (!vp || !w || !h) return;
    const pad = 30;
    const z = Math.min((vp.clientWidth - pad * 2) / w, (vp.clientHeight - pad * 2) / h, 1);
    tgt.current = { zoom: z, x: (vp.clientWidth - w * z) / 2, y: pad };
    vel.current.x = vel.current.y = 0;
    if (snap) { cur.current = { ...tgt.current }; apply(); } else kick();
  }, [apply, kick]);

  // Initial readable view: a deep org spreads very wide, so don't fit-to-all (which
  // would shrink nodes to nothing). Pick a legible zoom and center on the root —
  // the user pans to explore (the Fit button still zooms to the whole tree).
  const home = useCallback((snap?: boolean) => {
    const vp = viewportRef.current, { h, rootCx } = dims.current;
    if (!vp || !h) return;
    const pad = 30;
    const z = Math.max(0.5, Math.min(1, (vp.clientHeight - pad * 2) / h));
    tgt.current = { zoom: z, x: vp.clientWidth / 2 - rootCx * z, y: pad };
    vel.current.x = vel.current.y = 0;
    if (snap) { cur.current = { ...tgt.current }; apply(); } else kick();
  }, [apply, kick]);

  const zoom = useCallback((factor: number, cx?: number, cy?: number) => {
    const vp = viewportRef.current; if (!vp) return;
    const r = vp.getBoundingClientRect();
    const px = cx == null ? r.width / 2 : cx - r.left;
    const py = cy == null ? r.height / 2 : cy - r.top;
    const t = tgt.current;
    const nz = Math.max(0.3, Math.min(2.4, t.zoom * factor));
    t.x = px - (px - t.x) * (nz / t.zoom);
    t.y = py - (py - t.y) * (nz / t.zoom);
    t.zoom = nz; vel.current.x = vel.current.y = 0; kick();
  }, [kick]);

  // Position to the readable home view whenever the rendered graph changes.
  useEffect(() => { if (w && h) home(true); }, [w, h, rootCx, home]);

  // Native, non-passive wheel listener so preventDefault works (keeps the gesture
  // inside the map instead of scrolling the page). Depends on w/h so it re-attaches
  // once the viewport actually mounts — the canvas renders only after data loads.
  useEffect(() => {
    const vp = viewportRef.current; if (!vp || !w || !h) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // Trackpad pinch arrives as ctrl+wheel; plain two-finger / wheel pans, ⌘/ctrl zooms.
      if (e.ctrlKey || e.metaKey) { zoom(e.deltaY < 0 ? 1.12 : 0.89, e.clientX, e.clientY); return; }
      tgt.current.x -= e.deltaX;
      tgt.current.y -= e.deltaY;
      vel.current.x = vel.current.y = 0;
      kick();
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, [zoom, kick, w, h]);

  useEffect(() => () => { if (raf.current != null) cancelAnimationFrame(raf.current); }, []);

  const onMouseDown = useCallback((e: RMouseEvent) => {
    const t = e.target as HTMLElement;
    if (t.closest(".orgnode") || t.closest(".graph-controls")) return;
    e.preventDefault();
    const vp = viewportRef.current;
    const st = { sx: e.clientX, sy: e.clientY, ox: tgt.current.x, oy: tgt.current.y, lx: e.clientX, ly: e.clientY, lt: performance.now() };
    dragging.current = true; vel.current.x = vel.current.y = 0;
    if (vp) vp.style.cursor = "grabbing";
    const move = (ev: globalThis.MouseEvent) => {
      tgt.current.x = st.ox + (ev.clientX - st.sx);
      tgt.current.y = st.oy + (ev.clientY - st.sy);
      const now = performance.now(), dt = Math.max(8, now - st.lt);
      vel.current.x = (ev.clientX - st.lx) * (16 / dt);
      vel.current.y = (ev.clientY - st.ly) * (16 / dt);
      st.lx = ev.clientX; st.ly = ev.clientY; st.lt = now;
      kick();
    };
    const upFn = () => {
      dragging.current = false;
      if (vp) vp.style.cursor = "grab";
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", upFn);
      kick();
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", upFn);
  }, [kick]);

  return { viewportRef, panRef, onMouseDown, zoom, fit };
}
