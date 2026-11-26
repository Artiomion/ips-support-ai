// @vitest-environment node
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { createOverviewLoader } from "./api";

const server = setupServer(http.get("https://api.example/api/v1/analytics/overview", () => HttpResponse.json({
  conversations: 2, tickets: 1, ai_confirmed: 1, unconfirmed: 1, handoffs: 0,
  first_response_seconds_avg: null, resolution_seconds_avg: null,
})));
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

it("loads the overview using the agreed API endpoint", async () => {
  const data = await createOverviewLoader("https://api.example")();
  expect(data.conversations).toBe(2);
  expect(data.first_response_seconds_avg).toBeNull();
});

it("reports a server error instead of inventing metrics", async () => {
  server.use(http.get("https://api.example/api/v1/analytics/overview", () => HttpResponse.json({ error: { code: "unavailable", message: "Unavailable", request_id: "test" } }, { status: 503 })));
  await expect(createOverviewLoader("https://api.example")()).rejects.toMatchObject({ status: 503 });
});
