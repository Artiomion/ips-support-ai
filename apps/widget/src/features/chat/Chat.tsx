import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { createApiClient } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";
import { createPoller, createSender } from "./controller";
import { listConversations } from "../history/api";
import { SupportActions } from "../handoff/SupportActions";
import { createSupportApi } from "../handoff/api";
import { ticketStore } from "../handoff/ticketStore";

type Conversation = components["schemas"]["Conversation"];
type Message = components["schemas"]["PublicMessage"];
type Session = components["schemas"]["CurrentPublicSession"];
export function Chat({ token, apiOrigin, session, open, initialConversationId }: { token: string; apiOrigin: string; session: Session; open: boolean; initialConversationId?: string }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<string | null>(initialConversationId || null);
  const store = useMemo(() => ticketStore({ apiOrigin, siteOrigin: session.site_origin, widgetId: session.widget_id, customerId: session.customer_id }), [apiOrigin, session.site_origin, session.widget_id, session.customer_id]);
  const [ticketIds, setTicketIds] = useState<Record<string, string>>(() => store.read());
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const client = useRef(createApiClient({ baseUrl: apiOrigin, customerToken: token })).current;
  const supportApi = useMemo(() => createSupportApi(client), [client]);
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const selectionVersion = useRef(0); const reloadVersion = useRef(0);
  const reload = useCallback(async () => {
    const id = selectedRef.current; if (!id) return;
    const selection = selectionVersion.current; const request = ++reloadVersion.current;
    const current = () => selectedRef.current === id && selectionVersion.current === selection && reloadVersion.current === request;
    try {
      const all: Message[] = []; const seen = new Set<string>(); let cursor: string | undefined;
      do {
        const result = await client.get("/api/v1/public/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id }, query: { limit: 100, ...(cursor ? { cursor } : {}) } } });
        if (!result.data) throw new Error("Пустой ответ");
        if (!current()) return;
        all.push(...result.data.items); cursor = result.data.next_cursor || undefined;
        if (cursor && seen.has(cursor)) throw new Error("Повтор курсора");
        if (cursor) seen.add(cursor);
      } while (cursor);
      if (current()) { setMessages(all); setError(""); }
    } catch { if (current()) setError("Не удалось загрузить сообщения."); }
  }, [client]);
  const sender = useMemo(() => createSender(async (content, messageId) => {
    const id = selected; if (!id) return;
    await client.post("/api/v1/public/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id } }, body: { client_message_id: messageId, text: content } });
    if (selectedRef.current === id) await reload();
  }), [client, reload, selected]);
  useEffect(() => { if (!open || initialConversationId) return; let active = true; listConversations(client).then((items) => { if (active) setConversations((old) => [...old.filter((entry) => !items.some((item) => item.id === entry.id)), ...items]); }).catch(() => { if (active) setError("Не удалось загрузить историю."); }); return () => { active = false; }; }, [client, open, initialConversationId]);
  useEffect(() => {
    if (!open || !selected) return;
    void reload(); const poller = createPoller(() => { void reload(); }); poller.start();
    return () => { selectionVersion.current++; reloadVersion.current++; poller.stop(); };
  }, [open, selected, reload]);
  async function newConversation() {
    if (busy) return; setBusy(true); setError("");
    try {
      const result = await client.post("/api/v1/public/conversations", { body: {} });
      if (!result.data) throw new Error("Пустой ответ");
      selectionVersion.current++; setConversations((old) => [result.data!, ...old]); setMessages([]); setText(""); setSelected(result.data.id);
    } catch { setError("Не удалось создать диалог."); } finally { setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || !text.trim()) return;
    const selection = selectionVersion.current; const content = text;
    setBusy(true); setError("");
    try { await sender.submit(content); if (selection === selectionVersion.current) setText((current) => current === content ? "" : current); }
    catch { if (selection === selectionVersion.current) setError("Сообщение не отправлено. Повторите попытку."); }
    finally { setBusy(false); }
  }
  return <main><h1>{session.name}</h1><p>{session.greeting}</p>
    {error && <p role="alert">{error}</p>}
    {!initialConversationId && <><button type="button" disabled={busy} onClick={() => { void newConversation(); }}>Новая проблема</button>
    <h2>История диалогов</h2><ul>{conversations.map((item) => <li key={item.id}><button type="button" onClick={() => { if (selectedRef.current !== item.id) { selectionVersion.current++; setMessages([]); setText(""); setError(""); setSelected(item.id); } }}>Диалог от {new Date(item.created_at).toLocaleString()}</button></li>)}</ul></>}
    {selected && <section><h2>Сообщения</h2><ul>{messages.map((message) => <li key={message.id}><strong>{message.kind === "customer" ? "Вы" : message.kind === "specialist" ? "Специалист" : "Помощник"}:</strong> {message.text}{message.sources.map((source) => <small key={`${source.article_id}-${source.revision}`}> Источник: {source.title}</small>)}</li>)}</ul>
      <SupportActions key={selected} api={supportApi} conversationId={selected} open={open} ticketId={ticketIds[selected] || null} botMode={!initialConversationId && conversations.some((item) => item.id === selected && item.mode === "bot" && item.outcome === "unconfirmed") && messages.some((message) => message.kind === "assistant")} onAiConfirmed={() => setConversations((items) => items.map((item) => item.id === selected ? { ...item, outcome: "ai_confirmed" } : item))} onTicket={(id) => setTicketIds((previous) => { const next = { ...previous }; if (id) next[selected] = id; else delete next[selected]; store.write(next); return next; })} />
      <form onSubmit={(event) => { void submit(event); }}><label>Сообщение <input value={text} onChange={(event) => setText(event.target.value)} maxLength={8000} required /></label><button disabled={busy}>Отправить</button></form>
    </section>}
  </main>;
}
