import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { openLink } from "./open";
const origin = "https://api.example";
let received: unknown; let bearer: string | null = null;
const server = setupServer(
  http.post(`${origin}/api/v1/public/customer/links/exchange`, async ({ request }) => { received = await request.json(); return HttpResponse.json({ access_token: "scoped", conversation_id: "c1", expires_at: "2030-01-01T00:00:00Z" }); }),
  http.get(`${origin}/api/v1/public/session`, ({ request }) => { bearer = request.headers.get("Authorization"); return HttpResponse.json({ customer_id: "customer", widget_id: "widget", site_origin: "https://site.example", expires_at: "2030-01-01T00:00:00Z", name: "Чат", color: "#000000", greeting: "Привет" }); }),
);
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); received = undefined; bearer = null; });
afterAll(() => server.close());
it("exchanges a one-time link and validates the scoped session", async () => {
  const result = await openLink(origin, "one-time", "exchange");
  expect(received).toEqual({ token: "one-time" }); expect(bearer).toBe("Bearer scoped");
  expect(result.conversationId).toBe("c1"); expect(result.token).toBe("scoped");
});
