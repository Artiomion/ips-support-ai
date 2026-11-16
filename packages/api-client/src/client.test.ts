import { describe, expect, it, vi, afterEach } from "vitest";
import { ApiError, createApiClient } from "./client";

afterEach(() => vi.unstubAllGlobals());
describe("API client", () => {
  it("rejects an HTTP 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{}', { status: 401 })));
    const api = createApiClient({ baseUrl: "https://api.example" });
    await expect(api.get("/api/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
  });
  it("sends CSRF only on staff mutations and bearer only to public API", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response('{}', { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ baseUrl: "https://api.example", csrfToken: "csrf", customerToken: "token" });
    await api.get("/api/v1/auth/me");
    await api.post("/api/v1/auth/logout", { params: { header: { "X-CSRF-Token": "csrf" } } });
    await api.get("/api/v1/public/session");
    const requests = fetchMock.mock.calls.map(([request]) => request as Request);
    expect(requests[0].headers.has("X-CSRF-Token")).toBe(false);
    expect(requests[1].headers.get("X-CSRF-Token")).toBe("csrf");
    expect(requests[1].headers.has("Authorization")).toBe(false);
    expect(requests[2].headers.get("Authorization")).toBe("Bearer token");
    expect(requests[2].headers.has("X-CSRF-Token")).toBe(false);
  });
  it("refuses requests to another origin before sending a bearer", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ baseUrl: "https://api.example", customerToken: "secret" });
    await expect(api.get("https://evil.example/api/v1/public/session" as never)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("does not forward manually supplied sensitive headers on the wrong request", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApiClient({ baseUrl: "https://api.example" });
    await api.get("/api/v1/auth/me", { headers: { Authorization: "Bearer secret", "X-CSRF-Token": "secret" } });
    const request = fetchMock.mock.calls[0][0] as Request;
    expect(request.headers.has("Authorization")).toBe(false);
    expect(request.headers.has("X-CSRF-Token")).toBe(false);
  });
});
