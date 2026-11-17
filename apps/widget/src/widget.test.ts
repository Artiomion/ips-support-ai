import { afterEach, describe, expect, it, vi } from "vitest";
import { createTokenStore, resolveSession } from "./loader";
import { acceptInit, isTrustedMessage } from "./bridge";
import { createPoller, createSender } from "./features/chat/controller";

afterEach(() => { vi.useRealTimers(); localStorage.clear(); });
describe("widget boundaries", () => {
  it("rejects messages from another origin or window", () => {
    const parent = {} as Window;
    expect(isTrustedMessage({ source: parent, origin: "https://site.example" } as MessageEvent, parent, "https://site.example")).toBe(true);
    expect(isTrustedMessage({ source: parent, origin: "https://evil.example" } as MessageEvent, parent, "https://site.example")).toBe(false);
    expect(isTrustedMessage({ source: {} as Window, origin: "https://site.example" } as MessageEvent, parent, "https://site.example")).toBe(false);
  });
  it("keeps tokens separate for each widget", () => {
    const one = createTokenStore("one", () => localStorage);
    const two = createTokenStore("two", () => localStorage);
    one.set("first"); two.set("second");
    expect(one.get()).toBe("first"); expect(two.get()).toBe("second");
  });
  it("works in memory when localStorage is unavailable", () => {
    const store = createTokenStore("one", () => { throw new Error("denied"); });
    store.set("secret"); expect(store.get()).toBe("secret"); store.clear(); expect(store.get()).toBeNull();
  });
  it("stops polling when closed", async () => {
    vi.useFakeTimers(); const update = vi.fn(); const poller = createPoller(update);
    poller.start(); await vi.advanceTimersByTimeAsync(6000); expect(update).toHaveBeenCalledTimes(2);
    poller.stop(); await vi.advanceTimersByTimeAsync(6000); expect(update).toHaveBeenCalledTimes(2);
  });
  it("ignores repeated submissions while the first is pending", async () => {
    let finish!: () => void; const send = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
    const sender = createSender(send); const first = sender.submit("hello"); await sender.submit("hello");
    expect(send).toHaveBeenCalledTimes(1); finish(); await first;
  });
  it("rejects init when the verified session belongs to a different site", async () => {
    const parent = {} as Window;
    const event = { source: parent, origin: "https://evil.example", data: { type: "ips:init", token: "token", apiOrigin: "https://api.example" } };
    const verify = vi.fn().mockResolvedValue({ site_origin: "https://site.example" });
    await expect(acceptInit(event, parent, verify)).rejects.toThrow("Origin mismatch");
    await expect(acceptInit({ ...event, source: {} as Window }, parent, verify)).rejects.toThrow("Untrusted message");
    expect(verify).toHaveBeenCalledTimes(1);
  });
  it("replaces an expired session after a 401", async () => {
    const store = createTokenStore("old", () => localStorage); store.set("expired");
    const network = vi.fn().mockResolvedValueOnce(new Response(null, { status: 401 })).mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "new" }), { status: 201 }));
    await expect(resolveSession("https://api.example", "old", store, network as typeof fetch)).resolves.toBe("new");
    expect(store.get()).toBe("new"); expect(network).toHaveBeenCalledTimes(2);
  });
  it("preserves the old session if the API is unavailable", async () => {
    const store = createTokenStore("old", () => localStorage); store.set("original");
    const network = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));
    await expect(resolveSession("https://api.example", "old", store, network as typeof fetch)).rejects.toThrow();
    expect(store.get()).toBe("original"); expect(network).toHaveBeenCalledTimes(1);
  });
  it("uses the same message id when retrying a failed send", async () => {
    const send = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(undefined);
    const sender = createSender(send); await expect(sender.submit("hello")).rejects.toThrow(); await sender.submit("hello");
    expect(send.mock.calls[0][1]).toBe(send.mock.calls[1][1]);
  });
});
