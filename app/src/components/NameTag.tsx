import { useEffect, useState } from "react";
import { api, getToken } from "../api/client";

/**
 * The single name-render boundary in the UI.
 *
 * Analytics payloads are token-only; a real name is fetched on demand here through
 * the data layer's `/identity/resolve` seam (authorized records only, audited per
 * call). Every NameTag that mounts in the same tick is coalesced into ONE batched
 * request (a 400-row table is one call, not 400), and an in-memory cache avoids
 * re-resolving the same token within a session. The cache is bound to the signed-in
 * session, so names resolved under one login are never shown to the next. If
 * resolution is denied or unavailable, we fall back to the token — never invent a name.
 * Locking down resolution server-side changes nothing in any other screen.
 */

// Server caps a resolve request at 1000 tokens; stay well under it.
const MAX_BATCH = 500;

// token -> name, or null when the server withheld it (out of scope / unknown).
let cache = new Map<string, string | null>();
let inflight = new Map<string, Promise<string | null>>();
let queue = new Map<string, (name: string | null) => void>();
let sessionKey: string | null = null;
let flushScheduled = false;

/** Drop everything resolved under a previous login. */
function bindSession(): void {
  const current = getToken();
  if (current === sessionKey) return;
  sessionKey = current;
  cache = new Map();
  inflight = new Map();
  queue = new Map();
}

function flush(): void {
  flushScheduled = false;
  const batch = queue;
  queue = new Map();
  const tokens = [...batch.keys()];
  for (let i = 0; i < tokens.length; i += MAX_BATCH) {
    const chunk = tokens.slice(i, i + MAX_BATCH);
    const owner = sessionKey;
    api
      .resolveNames(chunk)
      .then((m) => {
        for (const t of chunk) {
          const name = m[t] ?? null;
          if (owner === sessionKey) {
            cache.set(t, name);
            inflight.delete(t);
          }
          batch.get(t)!(name);
        }
      })
      .catch(() => {
        // Transient failure: don't cache, so a later mount can retry.
        for (const t of chunk) {
          if (owner === sessionKey) inflight.delete(t);
          batch.get(t)!(null);
        }
      });
  }
}

function resolveName(token: string): Promise<string | null> {
  bindSession();
  if (cache.has(token)) return Promise.resolve(cache.get(token)!);
  const pending = inflight.get(token);
  if (pending) return pending;
  const p = new Promise<string | null>((resolve) => queue.set(token, resolve));
  inflight.set(token, p);
  if (!flushScheduled) {
    flushScheduled = true;
    setTimeout(flush, 0);
  }
  return p;
}

function cachedName(token: string): string | null {
  bindSession();
  return cache.get(token) ?? null;
}

export function NameTag({ token, mono }: { token: string; mono?: boolean }) {
  const [name, setName] = useState<string | null>(() => cachedName(token));

  useEffect(() => {
    let alive = true;
    resolveName(token).then((n) => alive && setName(n));
    return () => {
      alive = false;
    };
  }, [token]);

  if (name) return <strong>{name}</strong>;
  return <span style={{ fontFamily: mono ? "ui-monospace, monospace" : undefined }} className="muted">{token}</span>;
}
