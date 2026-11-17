import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";

type Ticket = components["schemas"]["TicketSummary"];
type Message = components["schemas"]["StaffMessage"];
type Page<T> = { items: T[]; next_cursor: string | null };
type Filters = { status?: string; q?: string; cursor?: string };
export type InboxApi = {
  list: (filters: Filters) => Promise<Page<Ticket>>; get: (id: string) => Promise<Ticket>;
  messages: (conversationId: string, cursor?: string) => Promise<Page<Message>>;
  claim: (id: string, version: number) => Promise<Ticket>;
  update: (id: string, body: components["schemas"]["TicketUpdate"]) => Promise<Ticket>;
  send: (conversationId: string, body: components["schemas"]["StaffMessageInput"]) => Promise<Message>;
};
const labels: Record<Ticket["status"], string> = { new: "Новая", waiting_specialist: "Ожидает специалиста", in_progress: "В работе", waiting_customer: "Ожидает клиента", resolved: "Решена", closed: "Закрыта" };
export function Inbox({ api }: { api: InboxApi }) {
  const [tickets, setTickets] = useState<Ticket[]>([]); const [cursor, setCursor] = useState<string | null>(null);
  const [filter, setFilter] = useState(""); const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true); const [queueError, setQueueError] = useState("");
  const [ticket, setTicket] = useState<Ticket | null>(null); const [messages, setMessages] = useState<Message[]>([]);
  const [detailLoading, setDetailLoading] = useState(false); const [error, setError] = useState("");
  const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [text, setText] = useState(""); const [internal, setInternal] = useState(false);
  const selectedId = useRef<string | null>(null); const queueGeneration = useRef(0);
  const retry = useRef<{ id: string; text: string; internal: boolean; conversation: string } | null>(null);
  const filters = useCallback((): Filters => ({ ...(filter ? { status: filter } : {}), ...(query.trim() ? { q: query.trim() } : {}) }), [filter, query]);
  const loadQueue = useCallback(async (next?: string) => {
    const generation = ++queueGeneration.current; setLoading(true); setQueueError("");
    try { const page = await api.list({ ...filters(), ...(next ? { cursor: next } : {}) });
      if (generation === queueGeneration.current) { setTickets((previous) => next ? [...previous, ...page.items] : page.items); setCursor(page.next_cursor); }
    } catch { if (generation === queueGeneration.current) setQueueError("Не удалось загрузить очередь."); }
    finally { if (generation === queueGeneration.current) setLoading(false); }
  }, [api, filters]);
  useEffect(() => { void loadQueue(); return () => { queueGeneration.current++; }; }, [loadQueue]);
  async function loadMessages(conversationId: string) {
    const all: Message[] = []; let next: string | undefined; const seen = new Set<string>();
    do { const page = await api.messages(conversationId, next); all.push(...page.items); next = page.next_cursor || undefined;
      if (next && seen.has(next)) throw new Error("Повтор курсора"); if (next) seen.add(next);
    } while (next);
    return all;
  }
  async function select(id: string) {
    selectedId.current = id; setTicket(null); setMessages([]); setError(""); setDetailLoading(true); retry.current = null; setText("");
    try { const detail = await api.get(id); const history = await loadMessages(detail.conversation_id);
      if (selectedId.current === id) { setTicket(detail); setMessages(history); }
    } catch { if (selectedId.current === id) setError("Не удалось загрузить заявку и сообщения."); }
    finally { if (selectedId.current === id) setDetailLoading(false); }
  }
  function saveTicket(next: Ticket) {
    if (selectedId.current === next.id) setTicket(next);
    setTickets((items) => items.map((item) => item.id === next.id ? next : item));
  }
  async function mutate(action: (current: Ticket) => Promise<Ticket>) {
    if (!ticket || busyRef.current) return; const original = ticket; busyRef.current = true; setBusy(true); setError("");
    try { saveTicket(await action(original)); }
    catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        try { saveTicket(await api.get(original.id)); if (selectedId.current === original.id) setError("Заявка изменилась. Данные обновлены — проверьте назначение и повторите действие при необходимости."); }
        catch { if (selectedId.current === original.id) setError("Заявка изменилась, но обновить её не удалось."); }
      } else if (selectedId.current === original.id) setError("Не удалось обновить заявку.");
    } finally { busyRef.current = false; setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!ticket || !text.trim() || busyRef.current) return;
    const original = ticket; const content = text; const note = internal;
    const old = retry.current; const attempt = old && old.conversation === original.conversation_id && old.text === content && old.internal === note ? old : { id: crypto.randomUUID(), text: content, internal: note, conversation: original.conversation_id };
    busyRef.current = true; setBusy(true); setError("");
    try { await api.send(original.conversation_id, { client_message_id: attempt.id, text: content, internal: note }); retry.current = null;
      if (selectedId.current === original.id) { setText(""); try { const history = await loadMessages(original.conversation_id); if (selectedId.current === original.id) setMessages(history); } catch { setError("Сообщение отправлено, но не удалось обновить историю."); } }
    } catch { retry.current = attempt; if (selectedId.current === original.id) setError("Не удалось отправить сообщение. Повторите попытку."); }
    finally { busyRef.current = false; setBusy(false); }
  }
  return <section aria-label="Рабочее место специалиста"><h2>Очередь обращений</h2>
    <label>Статус <select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="">Все</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{" "}
    <label>Поиск <input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
    {loading && <p role="status">Загружаем обращения…</p>}{queueError && <p role="alert">{queueError}</p>}
    {!loading && !queueError && tickets.length === 0 && <p>Обращений пока нет.</p>}
    <ul>{tickets.map((item) => <li key={item.id}><button type="button" onClick={() => { void select(item.id); }}>Заявка {item.id} — {labels[item.status]}</button></li>)}</ul>
    {cursor && <button type="button" disabled={loading} onClick={() => { void loadQueue(cursor); }}>Показать ещё</button>}
    {detailLoading && <p role="status">Загружаем диалог…</p>}{error && <p role="alert">{error}</p>}
    {ticket && <article><h3>Заявка {ticket.id}</h3><p>Статус: {labels[ticket.status]}. Версия: {ticket.version}. Назначена: {ticket.assignee_id || "никому"}.</p>
      {ticket.status === "waiting_specialist" && <button type="button" disabled={busy} onClick={() => { void mutate((current) => api.claim(current.id, current.version)); }}>Взять заявку</button>}
      {(["in_progress", "waiting_customer", "resolved"] as const).filter((status) => ticket.status !== status && ticket.status !== "closed" && ticket.status !== "new" && ticket.status !== "waiting_specialist" && ticket.status !== "resolved").map((status) => <button type="button" key={status} disabled={busy} onClick={() => { void mutate((current) => api.update(current.id, { expected_version: current.version, status })); }}>{labels[status]}</button>)}
      <h4>Переписка</h4>{messages.length === 0 && <p>Сообщений пока нет.</p>}<ul>{messages.map((message) => <li key={message.id} data-kind={message.kind} style={message.kind === "internal_note" ? { border: "2px solid #8a5800", background: "#fff3cd", padding: "0.5rem" } : undefined}><strong>{message.kind === "internal_note" ? "Внутренняя заметка" : message.kind === "customer" ? "Клиент" : message.kind === "specialist" ? "Специалист" : "Помощник"}</strong>: {message.text}</li>)}</ul>
      <form onSubmit={(event) => { void submit(event); }}><label>Тип сообщения <select value={internal ? "internal" : "reply"} onChange={(event) => setInternal(event.target.value === "internal")}><option value="reply">Ответ клиенту</option><option value="internal">Внутренняя заметка</option></select></label><p><label>Текст сообщения <textarea required maxLength={8000} value={text} onChange={(event) => setText(event.target.value)} /></label></p><button disabled={busy} type="submit">{internal ? "Сохранить заметку" : "Отправить ответ"}</button></form>
    </article>}
  </section>;
}
