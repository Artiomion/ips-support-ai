import { afterAll, afterEach, beforeAll, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { Chat } from "./Chat";

const origin = "https://api.example";
const conversation = { id: "conv", widget_id: "widget", mode: "bot", outcome: "unconfirmed", created_at: "2026-11-16T12:00:00Z", updated_at: "2026-11-16T12:00:00Z" } as const;
const message = { id: "message", conversation_id: "conv", seq: 1, kind: "customer", text: "Здравствуйте", sources: [], created_at: "2026-11-16T12:00:00Z" };
let sent: unknown;
let messages: unknown[] = [];
const server = setupServer(
  http.get(`${origin}/api/v1/public/conversations`, () => HttpResponse.json({ items: [], next_cursor: null })),
  http.post(`${origin}/api/v1/public/conversations`, () => HttpResponse.json(conversation, { status: 201 })),
  http.get(`${origin}/api/v1/public/conversations/:id/messages`, () => HttpResponse.json({ items: messages, next_cursor: null })),
  http.post(`${origin}/api/v1/public/conversations/:id/messages`, async ({ request }) => { sent = await request.json(); messages = [message]; return HttpResponse.json(message, { status: 201 }); }),
);
beforeAll(() => server.listen());
afterEach(() => { cleanup(); server.resetHandlers(); sent = undefined; messages = []; });
afterAll(() => server.close());

it("preserves the new conversation draft when an old send finishes", async () => {
  let release!: () => void; const pending = new Promise<void>((resolve) => { release = resolve; });
  const started = vi.fn();
  server.use(
    http.get(`${origin}/api/v1/public/conversations`, () => HttpResponse.json({ items: [conversation, { ...conversation, id: "second", created_at: "2026-11-17T12:00:00Z" }], next_cursor: null })),
    http.post(`${origin}/api/v1/public/conversations/:id/messages`, async () => { started(); await pending; return HttpResponse.json(message, { status: 201 }); }),
  );
  render(<Chat token="test-token" apiOrigin={origin} open session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  const history = await screen.findAllByRole("button", { name: /Диалог от/ });
  fireEvent.click(history[0]);
  fireEvent.change(screen.getByLabelText("Сообщение"), { target: { value: "Old question" } });
  fireEvent.submit(screen.getByRole("button", { name: "Отправить" }).closest("form")!);
  await waitFor(() => expect(started).toHaveBeenCalled());
  fireEvent.click(history[1]);
  fireEvent.change(screen.getByLabelText("Сообщение"), { target: { value: "New draft" } });
  await act(async () => { release(); });
  await waitFor(() => expect((screen.getByRole("button", { name: "Отправить" }) as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText("Сообщение") as HTMLInputElement).value).toBe("New draft");
});

it("stops message pagination when the server repeats a cursor", async () => {
  const load = vi.fn(() => HttpResponse.json({ items: [], next_cursor: null }))
    .mockImplementationOnce(() => HttpResponse.json({ items: [], next_cursor: "same" }))
    .mockImplementationOnce(() => HttpResponse.json({ items: [], next_cursor: "same" }));
  server.use(http.get(`${origin}/api/v1/public/conversations/:id/messages`, load));
  const view = render(<Chat token="test-token" apiOrigin={origin} open initialConversationId="conv" session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  try {
    expect(await screen.findByText("Не удалось загрузить сообщения.")).toBeTruthy();
    expect(load).toHaveBeenCalledTimes(2);
  } finally { view.unmount(); }
});

it("creates a conversation and sends a message through the API client", async () => {
  render(<Chat token="test-token" apiOrigin={origin} open session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Новая проблема" }));
  const input = await screen.findByLabelText("Сообщение");
  fireEvent.change(input, { target: { value: "Здравствуйте" } });
  fireEvent.submit(screen.getByRole("button", { name: "Отправить" }).closest("form")!);
  await waitFor(() => expect(sent).toMatchObject({ text: "Здравствуйте", client_message_id: expect.any(String) }));
  expect(await screen.findByText("Здравствуйте", { selector: "li" })).toBeTruthy();
});

it("can request a person when messages are unavailable", async () => {
  const ticket = { id: "t1", conversation_id: "conv", status: "waiting_specialist", priority: "normal", category_id: null, assignee_id: null, version: 1, created_at: "2026-11-16T12:00:00Z", updated_at: "2026-11-16T12:00:00Z" };
  server.use(
    http.get(`${origin}/api/v1/public/conversations/:id/messages`, () => HttpResponse.json({ error: { code: "unavailable", message: "Error", request_id: "test" } }, { status: 503 })),
    http.post(`${origin}/api/v1/public/conversations/:id/handoff`, () => HttpResponse.json(ticket)),
    http.get(`${origin}/api/v1/public/tickets/:id`, () => HttpResponse.json(ticket)),
  );
  render(<Chat token="test-token" apiOrigin={origin} open session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Новая проблема" }));
  const handoff = await screen.findByRole("button", { name: "Позвать специалиста" });
  expect(screen.queryByRole("button", { name: "Подтвердить ответ помощника" })).toBeNull();
  await screen.findByText("Не удалось загрузить сообщения.");
  fireEvent.click(handoff);
  expect(await screen.findByText(/Ожидаем специалиста/)).toBeTruthy();
});

it("limits email continuation to its conversation", async () => {
  const list = vi.fn(() => HttpResponse.json({ items: [], next_cursor: null }));
  server.use(http.get(`${origin}/api/v1/public/conversations`, list));
  render(<Chat token="scoped-token" apiOrigin={origin} open initialConversationId="conv" session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  expect(await screen.findByLabelText("Сообщение")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Новая проблема" })).toBeNull();
  expect(screen.queryByText("История диалогов")).toBeNull();
  expect(list).not.toHaveBeenCalled();
});
