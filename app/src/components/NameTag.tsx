import { useEffect, useState } from "react";
import { api } from "../api/client";

/**
 * The single name-render boundary in the UI.
 *
 * Analytics payloads are token-only; a real name is fetched on demand here through
 * the data layer's `/identity/resolve` seam (authorized records only, audited per
 * call). A tiny in-memory cache avoids re-resolving the same token within a session.
 * If resolution is denied or unavailable, we fall back to the token — never invent
 * a name. Locking down resolution server-side changes nothing in any other screen.
 */
const cache = new Map<string, string>();

export function NameTag({ token, mono }: { token: string; mono?: boolean }) {
  const [name, setName] = useState<string | null>(cache.get(token) ?? null);

  useEffect(() => {
    let alive = true;
    if (cache.has(token)) {
      setName(cache.get(token)!);
      return;
    }
    api
      .resolveNames([token])
      .then((m) => {
        const resolved = m[token];
        if (resolved) cache.set(token, resolved);
        if (alive) setName(resolved ?? null);
      })
      .catch(() => alive && setName(null));
    return () => {
      alive = false;
    };
  }, [token]);

  if (name) return <strong>{name}</strong>;
  return <span style={{ fontFamily: mono ? "ui-monospace, monospace" : undefined }} className="muted">{token}</span>;
}
