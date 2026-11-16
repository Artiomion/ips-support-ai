import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { Inbox } from "./Inbox";
import { createInboxApi } from "./api";

const origin = "https://api.example";
const ticket = { id: "ticket-1", conversation_id: "conversation-1", status: "waiting_specialist", priority: "normal", category_id: null, assignee_id: null, version: 1, created_at: "2026-11-16T12:00:00Z", updated_at: "2026-11-16T12:00:00Z" };
let requestBody: unknown; let csrf: string | null = null;
const server = setupServer(
  http.get(`${origin}/api/v1/tickets`, () => HttpResponse.json({ items: [ticket], next_cursor: null })),
  http.get(`${origin}/api/v1/tickets/:id`, () => HttpResponse.json(ticket)),
  http.get(`${origin}/api/v1/conversations/:id/messages`, () => HttpResponse.json({ items: [], next_cursor: null })),
  http.post(`${origin}/api/v1/tickets/:id/claim`, async ({ request }) => {
    csrf = request.headers.get("X-CSRF-Token"); requestBody = await request.json();
    return HttpResponse.json({ ...ticket, status: "in_progress", version: 2, assignee_id: "specialist-1" });
  }),
);
beforeAll(() => server.listen());
afterEach(() => { cleanup(); server.resetHandlers(); requestBody = undefined; csrf = null; });
afterAll(() => server.close());

it("claims a ticket through the HTTP client with its version and CSRF token", async () => {
  render(<Inbox api={createInboxApi(origin, "csrf-secret")} />);
  fireEvent.click(await screen.findByRole("button", { name: /ticket-1/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Взять заявку" }));
  await waitFor(() => expect(requestBody).toEqual({ expected_version: 1 }));
  expect(csrf).toBe("csrf-secret");
  expect(await screen.findByText(/specialist-1/)).toBeTruthy();
});
