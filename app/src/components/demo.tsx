/**
 * Shared "this is a showcase mock" primitives for the Data Management section.
 *
 * The Data section displays REAL synthetic data for every view (through the same
 * scoped + audited query and identity seams as the rest of the app), but every
 * WRITE/UPLOAD/SEND action is mocked against local component state — no backend
 * endpoint is added. These badges/toasts make that boundary unmistakable in the UI.
 */
import { createContext, useCallback, useContext, useState, ReactNode } from "react";
import { Icon } from "./icons";

/** Pill marking an action that runs the full UI flow but is not persisted. */
export function DemoBadge({ small }: { small?: boolean }) {
  return (
    <span className="dm-badge demo" style={small ? { fontSize: 9.5, padding: "1px 6px" } : undefined}>
      <span className="dm-badge-dot" /> demo — not persisted
    </span>
  );
}

/** Pill marking displayed records that are illustrative (no backing table exists). */
export function SampleBadge() {
  return (
    <span className="dm-badge sample">
      <span className="dm-badge-dot" /> sample data
    </span>
  );
}

/** Lock indicator for sensitive fields (comp + bias-walled) — value never shown/fetched. */
export function LockTag({ reason }: { reason: string }) {
  return (
    <span className="dm-lock" title={reason}>
      <Icon name="lock" size={11} /> locked
    </span>
  );
}

// ---- toast host -------------------------------------------------------------
interface Toast { id: number; text: string; kind: "demo" | "ok" | "err"; }
interface ToastCtx { push: (text: string, kind?: Toast["kind"]) => void; }
const Ctx = createContext<ToastCtx | null>(null);

/** Wrap the Data section so any tab can fire a confirmation toast. */
export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "demo") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3600);
  }, []);
  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="dm-toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`dm-toast ${t.kind}`}>
            <Icon name={t.kind === "ok" ? "check" : t.kind === "err" ? "alert" : "spark2"} size={14} />
            <span>{t.text}</span>
            {t.kind === "demo" && <DemoBadge small />}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastCtx {
  return useContext(Ctx) ?? { push: () => {} };
}
