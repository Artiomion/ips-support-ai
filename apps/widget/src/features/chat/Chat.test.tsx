import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("creates a conversation and sends a message through the API client", async () => {
  render(<Chat token="test-token" apiOrigin={origin} open session={{ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Поддержка", color: "#000000", greeting: "Здравствуйте" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Новая проблема" }));
  const input = await screen.findByLabelText("Сообщение");
  fireEvent.change(input, { target: { value: "Здравствуйте" } });
  fireEvent.submit(screen.getByRole("button", { name: "Отправить" }).closest("form")!);
  await waitFor(() => expect(sent).toMatchObject({ text: "Здравствуйте", client_message_id: expect.any(String) }));
  expect(await screen.findByText("Здравствуйте", { selector: "li" })).toBeTruthy();
});
