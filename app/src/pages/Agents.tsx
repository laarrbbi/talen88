import { CSSProperties, Fragment, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, AgentInfo, AgentRun, ChatResult, IdentityHit, Recommendation } from "../api/client";
import { NameTag } from "../components/NameTag";
import { Icon } from "../components/icons";
import { riskBand, riskColor, titleCase, usd } from "../lib/format";

// ============================================================================
// The Agents screen — a name-first agentic canvas that boots straight into the
// conversation. A persistent rail shows every reachable agent (active tinted);
// the thread runs a continuous multi-turn dispatch; the dock splits into a
// scoped run-bar + a reply composer. The whole canvas re-tints to the active
// agent's hue. An entrance animation plays on mount — a T88 hub dismantles into
// the agent orbs, which orbit out and fly into the rail before the thread
// reveals. Every turn (typed or run) flows through the dispatcher (/agents/chat)
// or a specific agent run (/agents/<name>/run), grounded in scored, token-keyed
// data, with names resolved only at the UI identity boundary and L3
// recommendations approved by a human.
// ============================================================================

// One item in the thread. A real Q&A turn has a question + the agent's reply
// (answer, matched people, L3 recommendations). A `meta` item is conversational
// scaffolding: an agent's intro ("intro") or a routing divider ("route").
interface ChatMatch { token: string; role?: string; location?: string; flight_risk?: number | null; }
interface Turn {
  id: string;
  question: string;
  answer: string | null;
  matched: ChatMatch[];
  recommendations: Recommendation[];
  agentId: string;           // visual agent that owns this turn (for orb/name/hue)
  kind?: ChatResult["kind"];
  note?: string | null;
  meta?: "intro";            // the agent's opening line (no user bubble)
  pending?: boolean;
  error?: boolean;
}

// Visual identity per agent: a name, a role caption, a hue (h/c) that drives the
// canvas + orb color, a one-line blurb and a grounded intro. The generalist
// "assistant" is the conversational dispatcher; the four specialists map 1:1 to
// the recommending agents.
interface Vis { id: string; name: string; role: string; h: number; c: number; blurb: string; intro: string; }
const AGENT_VIS: Record<string, Vis> = {
  assistant: {
    id: "assistant", name: "Assistant", role: "Workforce Generalist", h: 256, c: 0.11,
    blurb: "Reasons across all your people signals at once — risk, comp, performance and org.",
    intro: "I can reason across your scored people data and hand off to a specialist when it helps. What should we look into?",
  },
  retention: {
    id: "retention", name: "Retention", role: "Flight Risk", h: 25, c: 0.14,
    blurb: "Surfaces high flight-risk people and proposes retention moves.",
    intro: "I rank flight-risk people in your scope and propose retention moves — each needs your approval. Pick a division below, or just ask.",
  },
  career: {
    id: "career", name: "Career", role: "Promotion & Growth", h: 150, c: 0.11,
    blurb: "Assesses promotion readiness and growth for a person.",
    intro: "Tell me who to look at and I'll assess their promotion readiness and next steps.",
  },
  learning: {
    id: "learning", name: "Learning", role: "Skills & Paths", h: 285, c: 0.13,
    blurb: "Finds skill gaps and builds learning paths for a person.",
    intro: "Pick a person and I'll map their skill gaps and a learning path.",
  },
  workforce_planning: {
    id: "workforce_planning", name: "Planning", role: "Succession & Hiring", h: 205, c: 0.11,
    blurb: "Maps succession depth and hiring needs across a division.",
    intro: "Choose a division and I'll surface succession gaps and hiring needs.",
  },
};
function mapMatches(rows: ChatResult["matched"]): ChatMatch[] {
  return rows.map((m) => ({
    token: m.token, role: m.role, location: m.location,
    flight_risk: m.latest_score?.flight_risk ?? null,
  }));
}

const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

// ---- conversations + Claude-style history (persisted to localStorage) ---------------
// Each agent owns its own conversation(s); the history sidebar lists them all, newest first.
interface Conversation { id: string; agentId: string; title: string; updatedAt: number; turns: Turn[]; }

const CONV_KEY = "talent88.agentchats";
let _seq = 0;
const uid = () => `${Date.now().toString(36)}${(_seq++).toString(36)}`;

function loadConvs(): Conversation[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(CONV_KEY) || "null");
    if (Array.isArray(parsed) && parsed.length) return parsed as Conversation[];
  } catch { /* ignore corrupt storage */ }
  return [];
}
function saveConvs(cs: Conversation[]): void {
  try { localStorage.setItem(CONV_KEY, JSON.stringify(cs.slice(0, 40))); } catch { /* quota */ }
}
function introTurn(agentId: string): Turn {
  return { id: uid(), question: "", answer: AGENT_VIS[agentId].intro,
           matched: [], recommendations: [], agentId, meta: "intro" };
}
function freshConv(agentId: string): Conversation {
  return { id: uid(), agentId, title: "New chat", updatedAt: Date.now(), turns: [introTurn(agentId)] };
}
function titleFrom(q: string): string {
  const t = q.trim().replace(/\s+/g, " ");
  return t.length > 40 ? t.slice(0, 39) + "…" : t || "New chat";
}

// Track the document theme so orb/canvas lightness adapts to light/dark.
function useThemeMode(): "light" | "dark" {
  const read = () => (document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light");
  const [mode, setMode] = useState<"light" | "dark">(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setMode(read()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return mode;
}

export function Agents() {
  const theme = useThemeMode();
  const dark = theme === "dark";
  const orbL = dark ? 0.64 : 0.56;       // orb glyph lightness
  const accentL = dark ? 0.74 : 0.52;    // canvas accent lightness

  const [catalog, setCatalog] = useState<{ max_autonomy_level: number; agents: AgentInfo[] } | null>(null);
  const [divisions, setDivisions] = useState<{ id: number; name: string }[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    const loaded = loadConvs();
    return loaded.length ? loaded : [freshConv("assistant")];
  });
  const [activeConvId, setActiveConvId] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [person, setPerson] = useState<IdentityHit | null>(null);   // shared focus / token-agent target
  const [scopeDiv, setScopeDiv] = useState("");                      // division for division-scoped agents
  const [ready, setReady] = useState(false);                        // conv reveal (after intro)
  const [introOn, setIntroOn] = useState(true);                     // entrance overlay mounted
  const msgsRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const introRef = useRef<HTMLDivElement>(null);
  const hubRef = useRef<HTMLDivElement>(null);
  const introPlayingRef = useRef(false);
  const skipReqRef = useRef(false);
  const bootedRef = useRef(false);

  // The open conversation — and the agent that owns it — is derived from the list.
  const activeConv = conversations.find((c) => c.id === activeConvId) ?? conversations[0];
  const activeId = activeConv?.agentId ?? "assistant";
  const chat = activeConv?.turns ?? [];
  const recentConvs = useMemo(
    () => [...conversations].sort((a, b) => b.updatedAt - a.updatedAt), [conversations]);

  useEffect(() => { saveConvs(conversations); }, [conversations]);

  useEffect(() => {
    api.agentsList().then(setCatalog).catch(() => setCatalog({ max_autonomy_level: 3, agents: [] }));
    api.divisions().then(setDivisions).catch(() => setDivisions([]));
  }, []);

  useEffect(() => {
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat, busy]);

  // Which agents the catalog exposes, in a stable order, generalist first.
  const railAgents = useMemo<Vis[]>(() => {
    const specialists = (catalog?.agents ?? [])
      .map((a) => a.name)
      .filter((n) => n !== "conversational" && AGENT_VIS[n]);
    const order = ["assistant", ...specialists];
    return order.filter((id, i) => order.indexOf(id) === i).map((id) => AGENT_VIS[id]);
  }, [catalog]);

  // person-scoped vs division-scoped, from the catalog (fallback: career/learning are token-scoped).
  const scopeOf = useCallback((id: string): "token" | "division" | "none" => {
    if (id === "assistant") return "none";
    const info = catalog?.agents.find((a) => a.name === id);
    if (info?.scope === "token") return "token";
    if (info) return "division";
    return id === "career" || id === "learning" ? "token" : "division";
  }, [catalog]);

  // ---- entrance animation -------------------------------------------------
  const finishIntro = useCallback(() => {
    introPlayingRef.current = false;
    skipReqRef.current = false;
    setReady(true);
    const el = msgsRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    const overlay = introRef.current;
    if (!overlay) { setIntroOn(false); return; }
    const anim = overlay.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 420, easing: "ease", fill: "forwards" });
    anim.finished.then(() => {
      overlay.querySelectorAll(".ag-intro-clone").forEach((c) => c.remove());
      setIntroOn(false);
    }).catch(() => setIntroOn(false));
  }, []);

  const playIntro = useCallback(async () => {
    if (introPlayingRef.current) return;
    introPlayingRef.current = true;
    skipReqRef.current = false;
    setIntroOn(true);
    setReady(false);
    // let the overlay + hub mount/paint
    await new Promise<void>((r) => requestAnimationFrame(() => r()));
    const overlay = introRef.current, hub = hubRef.current, stage = stageRef.current;
    if (!overlay || !hub || !stage) { finishIntro(); return; }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { finishIntro(); return; }
    overlay.querySelectorAll(".ag-intro-clone").forEach((c) => c.remove());

    const sRect = stage.getBoundingClientRect();
    const cx = sRect.width / 2, cy = sRect.height / 2;
    const R = Math.max(140, Math.min(210, sRect.height * 0.30));
    const BIG = 1.7;

    hub.style.opacity = ""; hub.style.transform = "";
    await hub.animate([{ transform: "scale(.4)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }],
      { duration: 520, easing: "cubic-bezier(.34,1.45,.5,1)", fill: "forwards" }).finished;
    await wait(380);
    if (skipReqRef.current) { finishIntro(); return; }

    hub.animate([{ transform: "scale(1)", opacity: 1 }, { transform: "scale(.15)", opacity: 0 }],
      { duration: 420, easing: "cubic-bezier(.5,0,.9,.4)", fill: "forwards" });

    const orbEls = Array.from(stage.querySelectorAll<HTMLElement>(".ag-arail-item .ag-orb"));
    const n = orbEls.length || 1;
    const targets = orbEls.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.left + r.width / 2 - sRect.left, y: r.top + r.height / 2 - sRect.top, size: r.width };
    });
    const clones = orbEls.map((el, i) => {
      const c = document.createElement("div");
      c.className = "ag-intro-clone";
      c.style.left = (cx - 26) + "px";
      c.style.top = (cy - 26) + "px";
      c.innerHTML = el.outerHTML;
      const o = c.firstElementChild as HTMLElement | null;
      if (o) { o.style.width = "52px"; o.style.height = "52px"; }
      overlay.appendChild(c);
      return { el: c, ang0: -Math.PI / 2 + (i / n) * 2 * Math.PI, last: "" };
    });

    // phase A — emanate from center to a ring (staggered), at the bigger size
    await Promise.all(clones.map((c, i) => {
      const dx = R * Math.cos(c.ang0), dy = R * Math.sin(c.ang0);
      c.last = `translate(${dx}px, ${dy}px) scale(${BIG})`;
      return c.el.animate(
        [{ transform: "translate(0,0) scale(.2)", opacity: 0 }, { transform: c.last, opacity: 1 }],
        { duration: 560, delay: i * 70, easing: "cubic-bezier(.34,1.3,.5,1)", fill: "forwards" }).finished;
    }));
    if (skipReqRef.current) { finishIntro(); return; }

    // phase B — circular sweep (~1.3 turns), staying big
    const sweep = 1.3 * 2 * Math.PI, N = 30;
    await Promise.all(clones.map((c) => {
      const frames: Keyframe[] = [];
      for (let k = 0; k <= N; k++) {
        const a = c.ang0 + (k / N) * sweep;
        frames.push({ transform: `translate(${R * Math.cos(a)}px, ${R * Math.sin(a)}px) scale(${BIG})` });
      }
      const end = c.ang0 + sweep;
      c.last = `translate(${R * Math.cos(end)}px, ${R * Math.sin(end)}px) scale(${BIG})`;
      return c.el.animate(frames, { duration: 1150, easing: "cubic-bezier(.45,0,.55,1)", fill: "forwards" }).finished;
    }));
    if (skipReqRef.current) { finishIntro(); return; }

    // phase C — fly each clone into its rail slot
    await Promise.all(clones.map((c, i) => {
      const t = targets[i] ?? { x: cx, y: cy, size: 52 };
      const dx = t.x - cx, dy = t.y - cy, scale = t.size / 52;
      return c.el.animate([{ transform: c.last }, { transform: `translate(${dx}px, ${dy}px) scale(${scale})` }],
        { duration: 600, delay: i * 55, easing: "cubic-bezier(.4,0,.2,1)", fill: "forwards" }).finished;
    }));

    await wait(120);
    finishIntro();
  }, [finishIntro]);

  // Boot the entrance once the catalog (and therefore the full rail) is present.
  useEffect(() => {
    if (bootedRef.current || !catalog) return;
    bootedRef.current = true;
    requestAnimationFrame(() => requestAnimationFrame(() => { void playIntro(); }));
  }, [catalog, playIntro]);

  // ---- conversation actions ----------------------------------------------
  // Shared runner: append a pending turn to the OPEN conversation, run `exec`, fill it in.
  // Prior answered Q&A turns become the model's history (the intro item is excluded). The
  // turn stays owned by the conversation's agent — there is no re-routing/divider.
  const run = useCallback(async (
    question: string,
    exec: (history: { role: "user" | "assistant"; content: string }[]) => Promise<Partial<Turn>>,
  ) => {
    if (busy || !activeConv) return;
    setBusy(true);
    const convId = activeConv.id, agentId = activeConv.agentId, turnId = uid();
    const history = activeConv.turns.flatMap((t) => (!t.meta && t.answer)
      ? [{ role: "user" as const, content: t.question }, { role: "assistant" as const, content: t.answer }]
      : []);
    setConversations((cs) => cs.map((c) => c.id === convId ? {
      ...c, updatedAt: Date.now(),
      title: c.title === "New chat" && question.trim() ? titleFrom(question) : c.title,
      turns: [...c.turns, { id: turnId, question, answer: null, matched: [], recommendations: [], agentId, pending: true }],
    } : c));
    try {
      const patch = await exec(history);
      setConversations((cs) => cs.map((c) => c.id === convId ? {
        ...c, updatedAt: Date.now(),
        turns: c.turns.map((t) => (t.id === turnId ? { ...t, pending: false, ...patch } : t)),
      } : c));
    } catch {
      setConversations((cs) => cs.map((c) => c.id === convId ? {
        ...c, turns: c.turns.map((t) => (t.id === turnId
          ? { ...t, pending: false, error: true, answer: "Sorry — the assistant is unavailable right now. Please try again." }
          : t)),
      } : c));
    } finally {
      setBusy(false);
    }
  }, [busy, activeConv]);

  // Free-text question. The dispatcher still picks the right tool under the hood, but the
  // reply is shown as the agent you're talking to (no "routed to" — the patch sets no agentId).
  const askChat = useCallback((question: string) =>
    run(question, (history) =>
      api.chat({ question, history, person_token: person?.token, division: scopeDiv || undefined }).then((r) => ({
        kind: r.kind, answer: r.answer, note: r.note,
        matched: mapMatches(r.matched), recommendations: r.recommendations,
      }))), [run, person, scopeDiv]);

  // Precise quick-launch of one agent with an explicit person/division scope.
  const launchAgent = useCallback((name: string, scope: { division?: string; token?: string }, label: string) =>
    run(label, () => api.runAgent(name, scope).then((r: AgentRun) => ({
      kind: "recommendation" as const,
      answer: r.narrative || `${r.recommendations.length} grounded ${name.replace("_", " ")} recommendation${r.recommendations.length === 1 ? "" : "s"} below — a human approves each before anything is recorded.`,
      matched: [], recommendations: r.recommendations,
      note: (r.insight?.note as string | undefined) ?? null,
    }))), [run]);

  // Pick an agent from the rail: open its most-recent conversation, or start a fresh one.
  const selectAgent = (id: string) => {
    if (busy || id === activeId) return;
    setScopeDiv("");
    const existing = conversations.filter((c) => c.agentId === id).sort((a, b) => b.updatedAt - a.updatedAt)[0];
    if (existing) { setActiveConvId(existing.id); return; }
    const c = freshConv(id);
    setConversations((cs) => [c, ...cs]);
    setActiveConvId(c.id);
  };

  // Open a past conversation from the history sidebar.
  const openConv = (id: string) => { if (busy) return; setDraft(""); setScopeDiv(""); setActiveConvId(id); };

  // Start a brand-new conversation with the active agent.
  const newChat = (agentId: string = activeId) => {
    setDraft("");
    const c = freshConv(agentId);
    setConversations((cs) => [c, ...cs]);
    setActiveConvId(c.id);
  };

  // Delete a conversation; if it was open, fall back to the most recent remaining one.
  const deleteConv = (id: string) => {
    const remaining = conversations.filter((c) => c.id !== id);
    if (remaining.length) {
      setConversations(remaining);
      if (id === activeConv?.id) setActiveConvId(remaining[0].id);
    } else {
      const c = freshConv("assistant");
      setConversations([c]); setActiveConvId(c.id);
    }
  };

  // Reply composer send: dispatch within the active conversation.
  const dockSend = () => {
    const q = draft.trim();
    if (!q || busy) return;
    setDraft("");
    askChat(q);
  };

  // The active specialist's "Run" button: a precise, scoped agent run.
  const runActive = () => {
    const sc = scopeOf(activeId);
    const a = AGENT_VIS[activeId];
    if (sc === "token") {
      if (!person) return;
      launchAgent(activeId, { token: person.token }, `${a.name} review · ${person.name}`);
    } else {
      launchAgent(activeId, { division: scopeDiv || undefined }, `${a.name} · ${scopeDiv ? titleCase(scopeDiv) : "your scope"}`);
    }
  };

  const active = AGENT_VIS[activeId];
  const canvasStyle = {
    "--ag-l": accentL, "--ag-c": active.c, "--ag-h": active.h,
  } as CSSProperties;

  return (
    <div className="agentapp" style={canvasStyle}>
      <div className="ag-stage" ref={stageRef}>
        <div className={`ag-conv${ready ? " ready" : ""}`}>
          <aside className="ag-arail">
            <div className="ag-arail-cap" title="Replay intro"
                 onClick={() => { if (!introPlayingRef.current) void playIntro(); }}>Agents</div>
            <div className="ag-arail-list">
              {railAgents.map((a) => (
                <button key={a.id} className={`ag-arail-item${a.id === activeId ? " active" : ""}`}
                        title={`${a.name} · ${a.role}`} onClick={() => selectAgent(a.id)}>
                  <Orb a={a} size={a.id === activeId ? 54 : 48} light={orbL} ring={a.id === activeId} />
                  <span className="rl-name">{a.name}</span>
                </button>
              ))}
            </div>
          </aside>

          <aside className="ag-hist">
            <button className="ag-hist-new" onClick={() => newChat()}>
              <Icon name="plus" size={15} /> New chat
            </button>
            <div className="ag-hist-cap">Recent</div>
            <div className="ag-hist-list">
              {recentConvs.map((c) => {
                const va = AGENT_VIS[c.agentId] ?? AGENT_VIS.assistant;
                return (
                  <div key={c.id} className={`ag-hist-item${c.id === activeConv?.id ? " on" : ""}`}
                       onClick={() => openConv(c.id)} title={c.title}>
                    <span className="ag-hist-orb" style={orbVars(va, orbL)}><Orb a={va} size={20} light={orbL} /></span>
                    <span className="ag-hist-t">{c.title}</span>
                    <button className="ag-hist-x" title="Delete chat"
                            onClick={(e) => { e.stopPropagation(); deleteConv(c.id); }}>
                      <Icon name="close" size={11} />
                    </button>
                  </div>
                );
              })}
            </div>
          </aside>

          <div className="ag-thread">
            <div className="ag-head">
              <Orb a={active} size={52} light={orbL} />
              <div className="ag-head-meta">
                <div className="ag-head-name">{active.name}</div>
                <div className="ag-head-role">
                  {active.role}
                  <span className="ag-live"><span className="d" /> active <span className="lvl">· L{catalog?.max_autonomy_level ?? 3} max</span></span>
                </div>
              </div>
              <div className="ag-head-spacer" />
              <div className="ag-head-blurb">{active.blurb}</div>
              <button className="ag-newbtn" onClick={() => newChat()}>
                <Icon name="plus" size={15} /> New
              </button>
            </div>

            <div className="ag-msgs" ref={msgsRef}>
              <div className="ag-msgs-inner">
                {chat.map((t) => {
                  const a = AGENT_VIS[t.agentId] ?? AGENT_VIS.assistant;
                  return (
                    <Fragment key={t.id}>
                      {!t.meta && <div className="ag-msg user"><div className="ag-bubble">{t.question}</div></div>}
                      <div className="ag-msg ai">
                        <Orb a={a} size={38} light={orbL} />
                        <div className="ag-ai-body">
                          <div className="ag-ai-name">
                            {a.name}<span className="tag">{a.role}</span>
                          </div>
                          {t.pending ? (
                            <div className="ag-typing"><i /><i /><i /></div>
                          ) : (
                            <>
                              <div className="ag-ai-text">
                                <AnswerText text={t.answer} />
                                {t.note ? <span className="caveat">{t.note}</span> : null}
                              </div>
                              {t.matched.length > 0 && (
                                <div className="ag-ai-extras">
                                  {t.matched.slice(0, 12).map((m) => <MiniEmp key={m.token} m={m} />)}
                                  {t.matched.length > 12 && <div className="ag-ai-more">+ {t.matched.length - 12} more</div>}
                                </div>
                              )}
                              {t.recommendations.length > 0 && (
                                <div className="ag-ai-recos">
                                  {t.recommendations.slice(0, 6).map((r, i) => (
                                    <RecoCard key={i} agent={t.agentId} reco={r} />
                                  ))}
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </div>
                    </Fragment>
                  );
                })}
              </div>
            </div>

            <div className="ag-dock">
              <div className="ag-dock-inner">
                <ScopeRow
                  scope={scopeOf(activeId)} agent={active} divisions={divisions} busy={busy}
                  person={person} setPerson={setPerson} division={scopeDiv} setDivision={setScopeDiv}
                  onRun={runActive}
                />
                <Composer
                  draft={draft} setDraft={setDraft} onSend={dockSend} busy={busy}
                  placeholder={`Reply to ${active.name}…`}
                  footer={
                    person
                      ? <PersonChip hit={person} onClear={() => setPerson(null)} />
                      : <span className="ag-cm-bottom-picker"><PersonPicker compact value={person} onChange={setPerson} placeholder="Focus on a person…" /></span>
                  }
                />
                <div className="ag-dock-hint">
                  <Icon name="shield" size={14} /> Read-only Q&A + L3 proposals · token-keyed · names resolved per the identity boundary
                </div>
              </div>
            </div>
          </div>
        </div>

        {introOn && (
          <div className="ag-intro" ref={introRef}
               onClick={() => { if (introPlayingRef.current) { skipReqRef.current = true; finishIntro(); } }}>
            <div className="ag-intro-hub" ref={hubRef}>{ORB_GLYPH}</div>
            <div className="ag-intro-skip">click to skip</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational pieces
// ---------------------------------------------------------------------------

function orbVars(a: Vis, light: number): CSSProperties {
  return { "--oh-l": light, "--oh-c": a.c, "--oh-h": a.h } as CSSProperties;
}

const ORB_GLYPH = (
  <svg viewBox="0 0 100 100" fill="none" aria-hidden="true">
    <rect x="23" y="22" width="54" height="11" rx="3" fill="#fff" />
    <rect x="44.5" y="22" width="11" height="30" rx="3" fill="#fff" />
    <g fill="none" stroke="#fff" strokeWidth="7">
      <circle cx="36" cy="64" r="7.6" /><circle cx="36" cy="79" r="9" />
      <circle cx="64" cy="64" r="7.6" /><circle cx="64" cy="79" r="9" />
    </g>
  </svg>
);
function Orb({ a, size, light, ring }: { a: Vis; size: number; light: number; ring?: boolean }) {
  return (
    <span className={`ag-orb${ring ? " ring" : ""}`} style={{ width: size, height: size, ...orbVars(a, light) }}>
      {ORB_GLYPH}
    </span>
  );
}

// Auto-growing textarea composer (the reply card).
function Composer({ draft, setDraft, onSend, busy, placeholder, footer }: {
  draft: string; setDraft: (v: string) => void; onSend: () => void; busy: boolean;
  placeholder: string; footer: ReactNode;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 150) + "px";
  }, [draft]);
  return (
    <div className="ag-composer">
      <div className="ag-cm-top">
        <textarea ref={ref} className="ag-ta" rows={1} value={draft} placeholder={placeholder}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (draft.trim()) onSend(); }
                  }} />
        <button className="ag-send" onClick={onSend} disabled={busy || !draft.trim()} aria-label="Send" title="Send">
          <Icon name="arrowup" size={18} />
        </button>
      </div>
      <div className="ag-cm-bottom">{footer}</div>
    </div>
  );
}

// A per-agent scope control shown as the run-bar: person typeahead for
// token-scoped agents, a division dropdown for division-scoped ones, plus a
// precise "Run in chat" button. The generalist has no run-bar.
function ScopeRow({ scope, agent, divisions, busy, person, setPerson, division, setDivision, onRun }: {
  scope: "token" | "division" | "none";
  agent: Vis;
  divisions: { id: number; name: string }[];
  busy: boolean;
  person: IdentityHit | null;
  setPerson: (h: IdentityHit | null) => void;
  division: string;
  setDivision: (v: string) => void;
  onRun: () => void;
}) {
  if (scope === "none") return null;
  const ready = scope === "token" ? !!person : true;
  return (
    <div className="ag-scope">
      <span className="ag-scope-lbl"><Icon name="target" size={16} /> Run {agent.name} for</span>
      {scope === "token"
        ? <PersonPicker value={person} onChange={setPerson} placeholder="Pick a person…" />
        : (
          <div className="scope-select-wrap">
            <Icon name="division" size={13} style={{ color: "var(--text-4)" }} />
            <select className="scope-select" value={division} onChange={(e) => setDivision(e.target.value)}>
              <option value="">Your scope</option>
              {divisions.map((d) => <option key={d.id} value={d.name}>{titleCase(d.name)}</option>)}
            </select>
          </div>
        )}
      <button className="ag-run" onClick={onRun} disabled={!ready || busy}>
        <Icon name="arrowup" size={14} /> Run in chat
      </button>
    </div>
  );
}

// The agents reason in opaque tokens and, by contract, mention people by token in
// their prose ("the interface resolves tokens to names afterward"). Honor that here:
// any emp_… token embedded in a narrative is rendered through the identity boundary
// (NameTag), so the manager reads names — never a raw token.
function AnswerText({ text }: { text: string | null }) {
  if (!text) return null;
  const parts = text.split(/(emp_[A-Za-z0-9]+)/g);
  return <>{parts.map((p, i) => (/^emp_[A-Za-z0-9]+$/.test(p) ? <NameTag key={i} token={p} /> : p))}</>;
}

function MiniEmp({ m }: { m: ChatMatch }) {
  const risk = m.flight_risk ?? null;
  return (
    <a className="mini-emp" href={`#/profile/${m.token}`}>
      <span className="org-rdot" style={{ background: riskColor(risk) }} />
      <div className="mini-emp-body">
        <div className="mini-emp-name"><NameTag token={m.token} /></div>
        <div className="mini-emp-meta">{[m.role, m.location].filter(Boolean).join(" · ")}</div>
      </div>
      {risk != null && <span className={`band ${riskBand(risk)}`} style={{ fontSize: 10.5 }}><span className="bdot" />{risk}</span>}
      <span className="mini-go"><Icon name="chevright" size={14} /></span>
    </a>
  );
}

// Name -> token typeahead. Real names are shown ONLY here in the browser; the chosen
// person flows onward to the agents as an opaque token. The lookup is the scoped,
// audited /identity/search endpoint — a manager only ever sees their own scope.
function PersonPicker({ value, onChange, placeholder, compact }: {
  value: IdentityHit | null;
  onChange: (hit: IdentityHit | null) => void;
  placeholder?: string;
  compact?: boolean;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<IdentityHit[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setHits([]); setLoading(false); return; }
    let alive = true;
    setLoading(true);
    const h = setTimeout(() => {
      api.searchIdentities(term, 8)
        .then((r) => { if (alive) { setHits(r); setOpen(true); } })
        .catch(() => alive && setHits([]))
        .finally(() => { if (alive) setLoading(false); });
    }, 180);
    return () => { alive = false; clearTimeout(h); };
  }, [q]);

  if (value) return <PersonChip hit={value} onClear={() => { onChange(null); setQ(""); }} />;

  return (
    <div className={`picker${compact ? " compact" : ""}`}>
      <div className="picker-input">
        <Icon name="search" size={14} style={{ color: "var(--text-4)", flexShrink: 0 }} />
        <input value={q} onChange={(e) => setQ(e.target.value)}
               onFocus={() => hits.length > 0 && setOpen(true)}
               onBlur={() => setTimeout(() => setOpen(false), 150)}
               placeholder={placeholder ?? "Search a person by name…"} />
      </div>
      {open && q.trim().length >= 2 && (
        <div className="picker-results">
          {loading && <div className="picker-empty">Searching…</div>}
          {!loading && hits.length === 0 && <div className="picker-empty">No one in your scope matches.</div>}
          {hits.map((h) => (
            <button key={h.token} className="picker-opt" onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { onChange(h); setOpen(false); }}>
              <span className="picker-opt-name">{h.name}</span>
              <span className="picker-opt-meta">{titleCase(h.role)} · {titleCase(h.division)} · {h.location}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PersonChip({ hit, onClear }: { hit: IdentityHit; onClear: () => void }) {
  return (
    <span className="person-chip">
      <Icon name="profile" size={13} style={{ color: "var(--accent)" }} />
      <span className="person-chip-name">{hit.name}</span>
      <span className="person-chip-meta">{titleCase(hit.role)}</span>
      <button className="person-chip-x" onClick={onClear} aria-label="Clear person"><Icon name="close" size={11} /></button>
    </span>
  );
}

function RecoCard({ agent, reco }: { agent: string; reco: Recommendation }) {
  const [state, setState] = useState<"idle" | "approving" | "done" | "error">("idle");
  const [note, setNote] = useState<string>("");

  const approve = async () => {
    if (!reco.proposed_action) return;
    setState("approving");
    try {
      const res = await api.approve(agent, {
        kind: reco.proposed_action.kind,
        employee_token: reco.proposed_action.employee_token,
        params: reco.proposed_action.params,
      });
      const notified = res.notified as { created?: boolean; reason?: string } | undefined;
      setNote(notified?.created ? "Approval recorded · you were notified."
        : `Approval recorded · no notification (${notified?.reason ?? "n/a"}).`);
      setState("done");
    } catch { setState("error"); }
  };

  return (
    <div className="reco">
      <div className="row between">
        <strong>{reco.title}</strong>
        <a className="code" href={`#/profile/${reco.employee_token}`}><NameTag token={reco.employee_token} /></a>
      </div>
      <p style={{ margin: "4px 0" }}>{reco.rationale}</p>
      {!!Object.keys(reco.estimates).length && (
        <p className="subtle" style={{ fontSize: 13 }}>
          {Object.entries(reco.estimates).map(([k, v]) => (
            <span key={k} style={{ marginRight: 12 }}>{titleCase(k.replace(/_usd$/, ""))}: {usd(v)}</span>
          ))}
          {reco.estimates_caveat && <span className="caveat"> ({reco.estimates_caveat})</span>}
        </p>
      )}
      {reco.proposed_action ? (
        state === "done" ? <span className="band low"><span className="bdot" />{note}</span>
        : state === "error" ? <span className="band high"><span className="bdot" />Approval failed.</span>
        : (
          <button className="btn primary" onClick={approve} disabled={state === "approving"}>
            {state === "approving" ? "Approving…" : `Approve · ${titleCase(reco.proposed_action.kind)} (L3)`}
          </button>
        )
      ) : <span className="code">review manually — no grounded action</span>}
    </div>
  );
}
