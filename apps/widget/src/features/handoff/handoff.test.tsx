import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../../../packages/api-client/src/client";
import { SupportActions, type SupportApi } from "./SupportActions";
const ticket = { id: "t1", conversation_id: "c1", status: "waiting_specialist", priority: "normal", category_id: null, assignee_id: null, version: 1, created_at: "2026-11-16T12:00:00Z", updated_at: "2026-11-16T12:00:00Z" } as const;
function makeApi(): SupportApi { return { conversationTicket: vi.fn().mockResolvedValue(null), handoff: vi.fn().mockResolvedValue(ticket), ticket: vi.fn().mockResolvedValue(ticket), confirm: vi.fn(), reopen: vi.fn(), rate: vi.fn(), confirmAi: vi.fn(), email: vi.fn() }; }
afterEach(() => { cleanup(); vi.useRealTimers(); });
describe("customer support actions", () => {
  it("restores a resolved ticket without creating another handoff", async () => {
    const api = makeApi(); const onTicket = vi.fn();
    vi.mocked(api.conversationTicket).mockResolvedValue({ ...ticket, status: "resolved" });
    render(<SupportActions api={api} conversationId="c1" open restore ticketId={null} onTicket={onTicket} />);
    expect(await screen.findByRole("button", { name: "Подтвердить решение" })).toBeTruthy();
    expect(onTicket).toHaveBeenCalledWith("t1");
    expect(api.handoff).not.toHaveBeenCalled();
  });
  it("retries a failed recovery without creating a ticket", async () => {
    const api = makeApi(); const onTicket = vi.fn();
    vi.mocked(api.conversationTicket).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(ticket);
    render(<SupportActions api={api} conversationId="c1" open restore ticketId={null} onTicket={onTicket} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Позвать специалиста" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Повторить проверку заявки" }));
    expect(await screen.findByText(/Ожидаем специалиста/)).toBeTruthy();
    expect(api.handoff).not.toHaveBeenCalled();
  });
  it("does not apply a handoff after its conversation view was removed", async () => {
    const api = makeApi(); const onTicket = vi.fn(); let resolve!: (value: typeof ticket) => void;
    vi.mocked(api.handoff).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const view = render(<SupportActions api={api} conversationId="c1" open ticketId={null} onTicket={onTicket} />);
    fireEvent.click(screen.getByRole("button", { name: "Позвать специалиста" }));
    view.unmount();
    await act(async () => { resolve(ticket); });
    expect(onTicket).not.toHaveBeenCalled();
  });
  it("does not apply a pending handoff to a different conversation", async () => {
    const api = makeApi(); const onTicket = vi.fn(); let resolve!: (value: typeof ticket) => void;
    vi.mocked(api.handoff).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const view = render(<SupportActions api={api} conversationId="c1" open ticketId={null} onTicket={onTicket} />);
    fireEvent.click(screen.getByRole("button", { name: "Позвать специалиста" }));
    view.rerender(<SupportActions api={api} conversationId="c2" open ticketId={null} onTicket={onTicket} />);
    await act(async () => { resolve(ticket); });
    expect(onTicket).not.toHaveBeenCalled();
    expect(screen.queryByText(/Ожидаем специалиста/)).toBeNull();
  });
  it("does not show a previous conversation's error or retain its email", async () => {
    const api = makeApi(); let reject!: (reason: Error) => void;
    vi.mocked(api.email).mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    const onTicket = vi.fn();
    const view = render(<SupportActions api={api} conversationId="c1" open ticketId={null} onTicket={onTicket} />);
    fireEvent.change(screen.getByLabelText("Email для продолжения"), { target: { value: "client@example.com" } });
    fireEvent.submit(screen.getByRole("button", { name: "Получить ссылку" }).closest("form")!);
    view.rerender(<SupportActions api={api} conversationId="c2" open ticketId={null} onTicket={onTicket} />);
    await act(async () => { reject(new Error("unavailable")); });
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByLabelText("Email для продолжения") as HTMLInputElement).value).toBe("");
  });
  it("lets the customer request a person and displays waiting", async () => {
    const api = makeApi(); render(<SupportActions api={api} conversationId="c1" open ticketId={null} onTicket={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Позвать специалиста" }));
    await waitFor(() => expect(api.handoff).toHaveBeenCalledWith("c1"));
    expect(await screen.findByText(/Ожидаем специалиста/)).toBeTruthy();
  });
  it("does not confirm a resolved ticket until an explicit click", async () => {
    const api = makeApi(); vi.mocked(api.ticket).mockResolvedValue({ ...ticket, status: "resolved" });
    render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={vi.fn()} />);
    const button = await screen.findByRole("button", { name: "Подтвердить решение" });
    expect(api.confirm).not.toHaveBeenCalled(); fireEvent.click(button);
    await waitFor(() => expect(api.confirm).toHaveBeenCalledWith("t1"));
  });
  it("rejects ratings outside 1–5", async () => {
    const api = makeApi(); vi.mocked(api.ticket).mockResolvedValue({ ...ticket, status: "closed" });
    render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={vi.fn()} />);
    const score = await screen.findByLabelText("Оценка от 1 до 5");
    for (const value of ["0", "6"]) { fireEvent.change(score, { target: { value } }); fireEvent.submit(screen.getByRole("button", { name: "Отправить оценку" }).closest("form")!); }
    expect(api.rate).not.toHaveBeenCalled();
    fireEvent.change(score, { target: { value: "5" } }); fireEvent.submit(screen.getByRole("button", { name: "Отправить оценку" }).closest("form")!);
    await waitFor(() => expect(api.rate).toHaveBeenCalledWith("t1", 5));
  });
  it("does not claim an email was delivered when it is queued", async () => {
    const api = makeApi(); vi.mocked(api.email).mockResolvedValue(); render(<SupportActions api={api} conversationId="c1" open ticketId={null} onTicket={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Email для продолжения"), { target: { value: "client@example.com" } });
    fireEvent.submit(screen.getByRole("button", { name: "Получить ссылку" }).closest("form")!);
    expect(await screen.findByText(/поставлено в очередь/)).toBeTruthy();
  });
  it("stops polling when the widget closes", async () => {
    vi.useFakeTimers(); const api = makeApi(); const onTicket = vi.fn();
    const view = render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={onTicket} />);
    await act(async () => { await Promise.resolve(); });
    expect(api.ticket).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(api.ticket).toHaveBeenCalledTimes(2);
    view.rerender(<SupportActions api={api} conversationId="c1" open={false} ticketId="t1" onTicket={onTicket} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(api.ticket).toHaveBeenCalledTimes(2);
  });
  it("rejects a restored ticket belonging to another conversation", async () => {
    const api = makeApi(); vi.mocked(api.ticket).mockResolvedValue({ ...ticket, conversation_id: "other" });
    render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={vi.fn()} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/Ожидаем специалиста/)).toBeNull();
  });
  it("reopens a resolved ticket only after the customer requests it", async () => {
    const api = makeApi(); vi.mocked(api.ticket).mockResolvedValue({ ...ticket, status: "resolved" });
    vi.mocked(api.reopen).mockResolvedValue({ ...ticket, status: "in_progress", version: 2 });
    render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={vi.fn()} />);
    const button = await screen.findByRole("button", { name: "Проблема не решена" });
    expect(api.reopen).not.toHaveBeenCalled(); fireEvent.click(button);
    expect(await screen.findByText(/Специалист работает/)).toBeTruthy();
    expect(api.reopen).toHaveBeenCalledWith("t1");
  });
  it("forgets a restored ticket that is no longer accessible", async () => {
    const api = makeApi(); const onTicket = vi.fn(); vi.mocked(api.ticket).mockRejectedValue(new ApiError(404, {}));
    render(<SupportActions api={api} conversationId="c1" open ticketId="t1" onTicket={onTicket} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(onTicket).toHaveBeenCalledWith(null);
  });
});
