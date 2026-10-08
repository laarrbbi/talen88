import { useEffect, useState } from "react";
import { api, Notification, NotificationPref } from "../api/client";
import { Skeleton } from "../components/ui";
import { Icon } from "../components/icons";
import { titleCase } from "../lib/format";

export function Inbox() {
  const [items, setItems] = useState<Notification[] | null>(null);
  const [prefs, setPrefs] = useState<NotificationPref[] | null>(null);

  const load = () => {
    api.notifications().then(setItems).catch(() => setItems([]));
    api.prefs().then(setPrefs).catch(() => setPrefs([]));
  };
  useEffect(load, []);

  const markRead = async (id: number) => {
    await api.markRead(id).catch(() => {});
    setItems((cur) => cur?.map((n) => (n.id === id ? { ...n, read: true } : n)) ?? null);
  };

  const toggle = async (kind: string, opted_in: boolean) => {
    await api.setPref(kind, opted_in).catch(() => {});
    setPrefs((cur) => cur?.map((p) => (p.kind === kind ? { ...p, opted_in } : p)) ?? null);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Notifications</div>
          <div className="page-sub">
            <span>In-app and opt-in</span>
            <span className="dot-sep" />
            <span>Each note shows why you received it · nothing is sent for a kind you have not opted into</span>
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ alignItems: "start" }}>
        <div className="card">
          <div className="card-head"><div className="card-title"><Icon name="bell" size={16} style={{ color: "var(--text-3)" }} /> Inbox</div></div>
          {!items ? (
            <div className="card-body sk-stagger" style={{ display: "grid", gap: 18 }}>
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} style={{ display: "grid", gap: 7 }}>
                  <Skeleton className="sk-line" w="38%" h={12} />
                  <Skeleton className="sk-line sm" w="82%" h={10} />
                </div>
              ))}
            </div>
          ) : !items.length ? (
            <div className="card-body"><div className="nodata"><Icon name="bell" /> <span>No notifications yet. Approve an agent recommendation (with the matching kind opted in) to see one here.</span></div></div>
          ) : (
            <div className="tbl-wrap">
              <table className="tbl">
                <tbody>
                  {items.map((n) => (
                    <tr key={n.id} style={{ cursor: "default" }}>
                      <td style={{ width: 14 }}>{!n.read && <span className="dot unread" />}</td>
                      <td>
                        <div className="row between">
                          <strong>{titleCase(n.kind)}</strong>
                          <span className="subtle">{new Date(n.created_ts).toLocaleString()}</span>
                        </div>
                        <div className="subtle" style={{ marginTop: 2 }}>{n.why}</div>
                      </td>
                      <td className="td-num" style={{ width: 100 }}>
                        {!n.read && <button className="btn ghost" onClick={() => markRead(n.id)}>Mark read</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-head"><div className="card-title"><Icon name="sliders" size={16} style={{ color: "var(--text-3)" }} /> Preferences</div></div>
          <div className="card-body">
            <p className="subtle" style={{ marginTop: 0 }}>Opt in per kind. Default is off — you control what reaches you.</p>
            {!prefs ? (
              <div className="sk-stagger">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
                    <span style={{ display: "grid", gap: 6 }}>
                      <Skeleton className="sk-line" w={140} h={12} />
                      <Skeleton className="sk-line sm" w={220} h={9} />
                    </span>
                    <Skeleton w={18} h={18} r={4} />
                  </div>
                ))}
              </div>
            ) : prefs.map((p) => (
              <label key={p.kind} className="row between" style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
                <span>
                  <strong>{titleCase(p.kind)}</strong>
                  <div className="subtle" style={{ marginTop: 2 }}>{p.description} · channel: {p.channel}</div>
                </span>
                <input type="checkbox" checked={p.opted_in} onChange={(e) => toggle(p.kind, e.target.checked)} style={{ width: 18, height: 18 }} />
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
