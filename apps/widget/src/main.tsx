import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createApiClient } from "../../../packages/api-client/src/client";
import type { components } from "../../../packages/api-client/src/generated";
import { acceptInit, handleEscape, isInitMessage, isTrustedMessage } from "./bridge";
import { Chat } from "./features/chat/Chat";

type Active = { token: string; apiOrigin: string; parentOrigin: string; session: components["schemas"]["CurrentPublicSession"] };
function Widget() {
  const [active, setActive] = useState<Active | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let generation = 0;
    function handle(event: MessageEvent) {
      if (event.source !== window.parent || window.parent === window) return;
      if (event.data?.type === "ips:closed") {
        if (active && isTrustedMessage(event, window.parent, active.parentOrigin)) { generation++; setOpen(false); }
        else if (!active) generation++;
        return;
      }
      if (!isInitMessage(event.data)) return;
      const current = ++generation;
      void acceptInit(event, window.parent, async (token, apiOrigin) => {
        const api = createApiClient({ baseUrl: apiOrigin, customerToken: token });
        const result = await api.get("/api/v1/public/session");
        if (!result.data) throw new Error("Empty session");
        return result.data;
      }).then((verified) => {
        if (generation !== current) return;
        setActive(verified); setError(""); setOpen(true);
      }).catch(() => { if (generation === current) { setActive(null); setOpen(false); setError("Не удалось подтвердить сессию чата."); } });
    }
    function onKeyDown(event: KeyboardEvent) {
      const parentOrigin = active?.parentOrigin || new URL(window.location.href).searchParams.get("parent_origin");
      if (parentOrigin && window.parent !== window) handleEscape(event, window.parent, parentOrigin);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("message", handle);
    window.parent.postMessage({ type: "ips:ready" }, "*");
    return () => { generation++; window.removeEventListener("message", handle); window.removeEventListener("keydown", onKeyDown); };
  }, [active?.parentOrigin]);
  if (error) return <p role="alert">{error}</p>;
  if (!active) return <p role="status">Подключаем чат…</p>;
  return <Chat key={`${active.apiOrigin}:${active.token}`} token={active.token} apiOrigin={active.apiOrigin} session={active.session} open={open} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode><Widget /></StrictMode>);
