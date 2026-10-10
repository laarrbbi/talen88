import { useState } from "react";
import { api, ApiError, setToken, Me } from "../api/client";
import { Flash } from "../components/ui";
import { LogoMark } from "../components/icons";

// Seeded demo operators, offered as shortcuts in local development only. They share
// the demo password printed by `python -m data.generate` (saved to data/.demo-password).
const SAMPLE = [
  ["admin@pulsescore.local", "Admin (all divisions)"],
  ["technology.manager@pulsescore.local", "Technology manager"],
  ["risk.manager@pulsescore.local", "Risk & Compliance manager"],
];

export function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [email, setEmail] = useState(import.meta.env.DEV ? SAMPLE[0][0] : "");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await api.login(email, password);
      setToken(res.token);
      onLogin({ email: res.email, name: res.name, role: res.role, division: res.division });
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 429
        ? "Too many failed attempts. Try again in 15 minutes."
        : "Sign-in failed. Check your email and password.");
      setPassword("");
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
        <form onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <input
            style={{ width: "100%" }}
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            aria-label="Email"
            required
            autoFocus={!email}
          />
          <input
            style={{ width: "100%", marginTop: 8 }}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            aria-label="Password"
            required
            autoFocus={!!email}
          />
          <button className="btn primary" style={{ width: "100%", marginTop: 10, justifyContent: "center" }}
                  disabled={busy || !email || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        {import.meta.env.DEV && (
          <>
            <p className="muted" style={{ fontSize: 12, marginBottom: 4, marginTop: 16 }}>
              Demo operators (local development only). The password is in <code>data/.demo-password</code>.
            </p>
            {SAMPLE.map(([e, label]) => (
              <button key={e} type="button" className="btn opt" onClick={() => setEmail(e)} disabled={busy}>
                <span>{label}</span>
                <span className="muted">{e}</span>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
