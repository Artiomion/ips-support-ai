/// <reference types="vite/client" />
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Chat } from "../chat/Chat";
import { consumeLink } from "./link";
import { openLink, type ContinuationData } from "./open";

const apiOrigin = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const pending = consumeLink(window.location.href, (safe) => window.history.replaceState(null, "", safe), (token, action) => openLink(apiOrigin, token, action)).then((value) => ({ value }), () => ({ value: null }));
function Continuation() {
  const [data, setData] = useState<ContinuationData | null>(null); const [error, setError] = useState(false);
  useEffect(() => { let active = true; pending.then(({ value }) => { if (active && value) setData(value); else if (active) setError(true); }); return () => { active = false; }; }, []);
  if (error) return <main><p role="alert">Не удалось открыть ссылку. Она могла истечь или уже быть использована.</p></main>;
  if (!data) return <main><p role="status">Проверяем ссылку…</p></main>;
  return <Chat token={data.token} apiOrigin={data.apiOrigin} session={data.session} open initialConversationId={data.conversationId} />;
}
createRoot(document.getElementById("root")!).render(<StrictMode><Continuation /></StrictMode>);
