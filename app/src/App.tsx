import { createContext, useContext, useEffect, useState } from "react";

function SplashScreen() {
  return (
    <div className="splash">
      <div className="splash-logo"><LogoMark size={68} /></div>
      <div className="splash-wordmark">Talent<b>88</b></div>
      <div className="splash-dots"><span /><span /><span /></div>
    </div>
  );
}
import { api, getToken, setToken, Me } from "./api/client";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Analytics } from "./pages/Analytics";
import { Watchlist } from "./pages/Watchlist";
import { Profile } from "./pages/Profile";
import { Agents } from "./pages/Agents";
import { Inbox } from "./pages/Inbox";
import { Graph } from "./pages/Graph";
import { Surveys } from "./pages/Surveys";
import { DataManagement } from "./pages/DataManagement";
import { Icon, LogoMark } from "./components/icons";

// ---- minimal auth context ----------------------------------------------------
interface AuthCtx { me: Me; logout: () => void; }
const Ctx = createContext<AuthCtx | null>(null);
export const useAuth = () => useContext(Ctx)!;

// ---- tiny hash router (no dependency) ---------------------------------------
function useHashRoute(): [string, string[]] {
  const [hash, setHash] = useState(window.location.hash || "#/dashboard");
  useEffect(() => {
    const on = () => setHash(window.location.hash || "#/dashboard");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const parts = hash.replace(/^#\//, "").split("/").filter(Boolean);
  return [parts[0] ?? "dashboard", parts.slice(1)];
}

function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = useState(localStorage.getItem("talent88.theme") ?? "light");
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("talent88.theme", theme);
  }, [theme]);
  return [theme, setTheme];
}

// Intelligence-section nav. Screens added per build phase; Workspace items
// (Divisions/Reports/Settings) are intentionally inert placeholders.
const NAV = [
  ["dashboard", "Dashboard", "dashboard"],
  ["analytics", "Analytics", "grid"],
  ["watchlist", "Watchlist", "watchlist"],
  ["graph", "Company Graph", "graph"],
  ["surveys", "Surveys", "survey"],
  ["agents", "Agents", "spark2"],
] as const;
// Data Management is the one live Workspace screen; the rest stay inert placeholders.
const NAV_WORKSPACE = [
  ["reports", "Reports", "report"],
  ["settings", "Settings", "settings"],
] as const;

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase() || "?";
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);
  const [route, params] = useHashRoute();
  const [theme, setTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    // Hold the splash for at least one logo beat (pop + wordmark fade) so the
    // boot reads as an intentional loading moment rather than a flash.
    const minSplash = new Promise<void>((r) => setTimeout(r, 750));
    if (!getToken()) {
      minSplash.then(() => setBooting(false));
      return;
    }
    Promise.all([
      api.me().then(setMe).catch(() => setToken(null)),
      minSplash,
    ]).finally(() => setBooting(false));
  }, []);

  // Unread badge on the bell — reflects the same opt-in Inbox feed.
  useEffect(() => {
    if (!me) return;
    api.notifications(true).then((n) => setUnread(n.length)).catch(() => setUnread(0));
  }, [me, route]);

  if (booting) return <SplashScreen />;
  if (!me) return <Login onLogin={setMe} />;

  const logout = () => {
    setToken(null);
    setMe(null);
    window.location.hash = "#/dashboard";
  };

  let page;
  if (route === "analytics") page = <Analytics />;
  else if (route === "watchlist") page = <Watchlist />;
  else if (route === "profile") page = <Profile token={params[0]} />;
  else if (route === "graph") page = <Graph />;
  else if (route === "surveys") page = <Surveys sub={params[0]} />;
  else if (route === "agents") page = <Agents />;
  else if (route === "data") page = <DataManagement />;
  else if (route === "inbox") page = <Inbox />;
  else page = <Dashboard />;

  return (
    <Ctx.Provider value={{ me, logout }}>
      <div className={`app${collapsed ? " rail-collapsed" : ""}`}>
        <aside className="rail">
          <div className="rail-brand">
            <LogoMark size={34} />
            {!collapsed && <div className="brand-name">Talent<b>88</b></div>}
          </div>
          <nav className="nav">
            {!collapsed && <div className="nav-section">Intelligence</div>}
            {NAV.map(([key, label, ic]) => (
              <a key={key} href={`#/${key}`} title={label}
                 className={`nav-item${route === key ? " active" : ""}`}>
                <Icon name={ic} size={17} className="nav-ico" />
                {!collapsed && <span className="nav-label">{label}</span>}
              </a>
            ))}
            {!collapsed && <div className="nav-section">Workspace</div>}
            <a href="#/data" title="Data Management"
               className={`nav-item${route === "data" ? " active" : ""}`}>
              <Icon name="division" size={17} className="nav-ico" />
              {!collapsed && <span className="nav-label">Data</span>}
            </a>
            {NAV_WORKSPACE.map(([key, label, ic]) => (
              <a key={key} title={`${label} — coming soon`} aria-disabled="true"
                 className="nav-item" style={{ opacity: 0.5, cursor: "default" }}
                 onClick={(e) => e.preventDefault()}>
                <Icon name={ic} size={17} className="nav-ico" />
                {!collapsed && <span className="nav-label">{label}</span>}
              </a>
            ))}
          </nav>
          <div className="rail-foot">
            <div className="rail-user">
              <div className="avatar" style={{ width: 32, height: 32, fontSize: 12 }}>{initialsOf(me.name)}</div>
              {!collapsed && (
                <div className="rail-foot-text">
                  <div className="nm">{me.name}</div>
                  <div className="rl">{me.role}{me.division ? ` · ${me.division}` : ""}</div>
                </div>
              )}
            </div>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <button className="icon-btn" title="Toggle menu" onClick={() => setCollapsed((c) => !c)}>
              <Icon name="watchlist" size={17} />
            </button>
            <div className="search">
              <span style={{ display: "flex" }}><Icon name="search" size={15} /></span>
              <input placeholder="Search employees, divisions, risk drivers…" />
              <kbd>⌘K</kbd>
            </div>
            <div className="spacer" />
            <div className="seg">
              <button className={theme === "light" ? "on" : ""} onClick={() => setTheme("light")}>
                <Icon name="sun" size={14} /> Light
              </button>
              <button className={theme === "dark" ? "on" : ""} onClick={() => setTheme("dark")}>
                <Icon name="moon" size={14} /> Dark
              </button>
            </div>
            <a className="icon-btn" href="#/inbox" title="Notifications" style={{ position: "relative" }}>
              <Icon name="bell" size={17} />
              {unread > 0 && (
                <span style={{ position: "absolute", top: 5, right: 5, minWidth: 7, height: 7, borderRadius: "50%", background: "var(--risk-high)", boxShadow: "0 0 0 2px var(--surface)" }} />
              )}
            </a>
            <button className="icon-btn" title="Sign out" onClick={logout}><Icon name="enter" size={17} /></button>
          </header>
          <div key={route} className="scroll">{page}</div>
        </div>
      </div>
    </Ctx.Provider>
  );
}
