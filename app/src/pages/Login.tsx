import { useState } from "react";
import { api, setToken, Me } from "../api/client";
import { Flash } from "../components/ui";
import { LogoMark } from "../components/icons";

// Dev login (MVP): email only, no password — auth hardening is out of MVP scope.
// The seeded operators below make it easy to try admin vs. a division manager.
const SAMPLE = [
  ["admin@pulsescore.local", "Admin (all divisions)"],
  ["technology.manager@pulsescore.local", "Technology manager"],
  ["risk.manager@pulsescore.local", "Risk & Compliance manager"],
];

export function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [email, setEmail] = useState("admin@pulsescore.local");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (value: string) => {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.login(value);
      setToken(res.token);
      onLogin({ email: res.email, name: res.name, role: res.role, division: res.division });
    } catch {
      setErr("Login failed — unknown operator email.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <div className="card login-card" style={{ padding: 24 }}>
        <div className="login-brand">
          <LogoMark size={38} />
          <div className="brand-name">Talent<b>88</b></div>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>People analytics · sign in</p>
        {err && <Flash kind="err">{err}</Flash>}
        <form onSubmit={(e) => { e.preventDefault(); submit(email); }}>
          <input
            style={{ width: "100%" }}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="operator email"
            autoFocus
          />
          <button className="btn primary" style={{ width: "100%", marginTop: 10, justifyContent: "center" }} disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="muted" style={{ fontSize: 12, marginBottom: 4, marginTop: 16 }}>Sample operators</p>
        {SAMPLE.map(([e, label]) => (
          <button key={e} className="btn opt" onClick={() => { setEmail(e); submit(e); }} disabled={busy}>
            {label} <span className="muted">· {e}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
