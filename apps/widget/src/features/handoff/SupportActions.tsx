import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";
type Ticket = components["schemas"]["TicketSummary"];
export type SupportApi = {
  handoff: (conversationId: string) => Promise<Ticket>; ticket: (ticketId: string) => Promise<Ticket>;
  confirm: (ticketId: string) => Promise<Ticket>; reopen: (ticketId: string) => Promise<Ticket>;
  rate: (ticketId: string, score: number) => Promise<void>; confirmAi: (conversationId: string) => Promise<void>;
  email: (conversationId: string, email: string) => Promise<void>;
};
export function SupportActions({ api, conversationId, open, ticketId, onTicket, botMode = true, onAiConfirmed }: { api: SupportApi; conversationId: string; open: boolean; ticketId: string | null; onTicket: (id: string | null) => void; botMode?: boolean; onAiConfirmed?: () => void }) {
  const [ticket, setTicket] = useState<Ticket | null>(null); const [busy, setBusy] = useState(false); const busyRef = useRef(false);
  const [error, setError] = useState(""); const [pollError, setPollError] = useState(""); const [notice, setNotice] = useState(""); const [email, setEmail] = useState(""); const [score, setScore] = useState("");
  const generation = useRef(0); const [rated, setRated] = useState(false); const [aiConfirmed, setAiConfirmed] = useState(false);
  const currentConversation = useRef(conversationId); currentConversation.current = conversationId;
  useEffect(() => {
    setTicket(null); setError(""); setPollError(""); setNotice(""); setEmail(""); setScore(""); setRated(false); setAiConfirmed(false); generation.current++;
    return () => { generation.current++; };
  }, [conversationId]);
  useEffect(() => {
    if (!open || !ticketId) return;
    const activeTicketId = ticketId;
    let active = true; let running = false;
    async function update() { if (running || busyRef.current) return; running = true; const before = generation.current;
      try { const value = await api.ticket(activeTicketId); if (value.conversation_id !== conversationId) throw new Error("Conversation mismatch"); if (active && before === generation.current) { setTicket(value); setPollError(""); } }
      catch (err) { if (active && before === generation.current) { if (err instanceof ApiError && err.status === 404) { setTicket(null); onTicket(null); setPollError("Заявка недоступна. При необходимости попросите специалиста снова."); } else setPollError("Не удалось обновить состояние заявки."); } }
      finally { running = false; }
    }
    void update(); const timer = setInterval(() => { void update(); }, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [api, open, ticketId, conversationId]);
  async function run(action: (isCurrent: () => boolean) => Promise<void>) {
    if (busyRef.current) return; busyRef.current = true; generation.current++; setBusy(true); setError(""); setNotice("");
    const actionGeneration = generation.current; const isCurrent = () => currentConversation.current === conversationId && actionGeneration === generation.current;
    try { await action(isCurrent); } catch { if (isCurrent()) setError("Не удалось выполнить действие. Попробуйте ещё раз."); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function sendRating(event: FormEvent) { event.preventDefault(); const value = Number(score);
    if (!ticket || !Number.isInteger(value) || value < 1 || value > 5) { setError("Выберите оценку от 1 до 5."); return; }
    await run(async (isCurrent) => { await api.rate(ticket.id, value); if (isCurrent()) { setRated(true); setNotice("Спасибо за оценку."); } });
  }
  async function sendEmail(event: FormEvent) { event.preventDefault(); if (!email.trim()) return;
    await run(async (isCurrent) => { await api.email(conversationId, email.trim()); if (isCurrent()) setNotice("Письмо поставлено в очередь. Проверьте почту позже."); });
  }
  const handoff = () => run(async (isCurrent) => { const value = await api.handoff(conversationId); if (value.conversation_id !== conversationId) throw new Error("Conversation mismatch"); if (isCurrent()) { setTicket(value); onTicket(value.id); } });
  const change = (operation: (id: string) => Promise<Ticket>) => run(async (isCurrent) => { if (ticket) { const value = await operation(ticket.id); if (value.conversation_id !== conversationId) throw new Error("Conversation mismatch"); if (isCurrent()) setTicket(value); } });
  return <section aria-label="Действия с обращением"><h2>Помощь специалиста</h2>
    {error && <p role="alert">{error}</p>}{pollError && <p role="alert">{pollError}</p>}{notice && <p role="status">{notice}</p>}
    {!ticketId && <button type="button" disabled={busy} onClick={() => { void handoff(); }}>Позвать специалиста</button>}
    {ticketId && !ticket && <p role="status">Загружаем состояние заявки…</p>}
    {ticket && <><p role="status">{{ new: "Заявка создана", waiting_specialist: "Ожидаем специалиста", in_progress: "Специалист работает над обращением", waiting_customer: "Специалист ожидает вашего ответа", resolved: "Специалист отметил обращение решённым. Подтвердите или откройте его снова.", closed: "Обращение закрыто" }[ticket.status]}</p>
      {ticket.status === "resolved" && <><button type="button" disabled={busy} onClick={() => { void change(api.confirm); }}>Подтвердить решение</button><button type="button" disabled={busy} onClick={() => { void change(api.reopen); }}>Проблема не решена</button></>}
      {ticket.status === "closed" && !rated && <form onSubmit={(e) => { void sendRating(e); }}><label>Оценка от 1 до 5 <input type="number" min="1" max="5" step="1" required value={score} onChange={(e) => setScore(e.target.value)} /></label><button disabled={busy}>Отправить оценку</button></form>}
    </>}
    {!ticketId && botMode && !aiConfirmed && <button type="button" disabled={busy} onClick={() => { void run(async (isCurrent) => { await api.confirmAi(conversationId); if (isCurrent()) { setAiConfirmed(true); onAiConfirmed?.(); setNotice("Решение подтверждено."); } }); }}>Подтвердить ответ помощника</button>}
    <form onSubmit={(e) => { void sendEmail(e); }}><label>Email для продолжения <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label><button disabled={busy}>Получить ссылку</button></form>
  </section>;
}
