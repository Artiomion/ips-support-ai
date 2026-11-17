import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../../../packages/api-client/src/client";
import { Inbox, type InboxApi } from "./Inbox";

const ticket = { id: "ticket-1", conversation_id: "conversation-1", status: "waiting_specialist", priority: "normal", category_id: null, assignee_id: null, version: 1, created_at: "2026-11-16T12:00:00Z", updated_at: "2026-11-16T12:00:00Z" } as const;
const note = { id: "message-1", conversation_id: "conversation-1", seq: 1, kind: "internal_note", text: "Только сотрудникам", sources: [], created_at: "2026-11-16T12:00:00Z" } as const;
function makeApi(): InboxApi { return { list: vi.fn().mockResolvedValue({ items: [ticket], next_cursor: null }), get: vi.fn().mockResolvedValue(ticket), messages: vi.fn().mockResolvedValue({ items: [note], next_cursor: null }), claim: vi.fn().mockResolvedValue(ticket), update: vi.fn().mockResolvedValue(ticket), send: vi.fn().mockResolvedValue(note) }; }
afterEach(cleanup);
async function openTicket() { fireEvent.click(await screen.findByRole("button", { name: /ticket-1/ })); return screen.findByRole("heading", { name: /Заявка ticket-1/ }); }

describe("inbox", () => {
  it("visibly distinguishes internal notes", async () => {
    render(<Inbox api={makeApi()} />); await openTicket();
    const noteElement = (await screen.findByText("Внутренняя заметка", { selector: "strong" })).closest("li")!;
    expect(noteElement.textContent).toContain("Только сотрудникам");
    expect(within(noteElement).getByText("Внутренняя заметка")).toBeTruthy();
    expect(noteElement.getAttribute("data-kind")).toBe("internal_note");
  });
  it("reloads a conflicting claim without overwriting another assignee", async () => {
    const api = makeApi(); const taken = { ...ticket, version: 2, status: "in_progress" as const, assignee_id: "other-specialist" };
    vi.mocked(api.claim).mockRejectedValue(new ApiError(409, {}));
    vi.mocked(api.get).mockResolvedValueOnce(ticket).mockResolvedValueOnce(taken);
    render(<Inbox api={api} />); await openTicket(); fireEvent.click(screen.getByRole("button", { name: "Взять заявку" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Заявка изменилась");
    await waitFor(() => expect(screen.getByText(/other-specialist/)).toBeTruthy());
    expect(api.claim).toHaveBeenCalledTimes(1); expect(api.claim).toHaveBeenCalledWith("ticket-1", 1);
    expect(screen.getByText(/Версия: 2/)).toBeTruthy();
    expect(api.update).not.toHaveBeenCalled();
  });
  it("sends a public reply only after an explicit submit", async () => {
    const api = makeApi(); render(<Inbox api={api} />); await openTicket();
    fireEvent.change(screen.getByLabelText("Текст сообщения"), { target: { value: "Ответ клиенту" } });
    expect(api.send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Отправить ответ" }));
    await waitFor(() => expect(api.send).toHaveBeenCalledWith("conversation-1", expect.objectContaining({ text: "Ответ клиенту", internal: false })));
  });
  it("filters the queue by status", async () => {
    const api = makeApi(); render(<Inbox api={api} />); await screen.findByRole("button", { name: /ticket-1/ });
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "in_progress" } });
    await waitFor(() => expect(api.list).toHaveBeenCalledWith(expect.objectContaining({ status: "in_progress" })));
  });
  it("sends internal notes only after choosing that mode", async () => {
    const api = makeApi(); render(<Inbox api={api} />); await openTicket();
    fireEvent.change(screen.getByLabelText("Тип сообщения"), { target: { value: "internal" } });
    fireEvent.change(screen.getByLabelText("Текст сообщения"), { target: { value: "Служебная запись" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить заметку" }));
    await waitFor(() => expect(api.send).toHaveBeenCalledWith("conversation-1", expect.objectContaining({ text: "Служебная запись", internal: true })));
  });
  it("keeps the message identifier when retrying after an error", async () => {
    const api = makeApi(); vi.mocked(api.send).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ ...note, sources: [] });
    render(<Inbox api={api} />); await openTicket();
    fireEvent.change(screen.getByLabelText("Текст сообщения"), { target: { value: "Ответ клиенту" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить ответ" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Отправить ответ" }));
    await waitFor(() => expect(api.send).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.send).mock.calls[0][1].client_message_id).toBe(vi.mocked(api.send).mock.calls[1][1].client_message_id);
  });
});
