import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { setupServer } from "msw/node";
import { widgetHandlers } from "./handlers";

const origin = "https://api.example";
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("widget scenarios", () => {
  it.each(["bot", "waiting", "human"] as const)("%s returns public messages only", async (scenario) => {
    server.use(...widgetHandlers(scenario, origin));
    const response = await fetch(`${origin}/api/v1/public/conversations/123/messages`);
    const body = await response.json();
    expect(response.status).toBe(200);
    expect(body.items.length).toBeGreaterThan(0);
    expect(body.items.every((item: { kind: string }) => ["customer", "assistant", "specialist"].includes(item.kind))).toBe(true);
  });
  it.each([["waiting", "waiting_specialist"], ["human", "in_progress"]] as const)("%s returns %s", async (scenario, status) => {
    server.use(...widgetHandlers(scenario, origin));
    const response = await fetch(`${origin}/api/v1/public/tickets/123`);
    expect((await response.json()).status).toBe(status);
  });
  it("shows an unavailable service response", async () => {
    server.use(...widgetHandlers("timeout", origin));
    expect((await fetch(`${origin}/api/v1/public/conversations/123/messages`)).status).toBe(503);
  });
});
