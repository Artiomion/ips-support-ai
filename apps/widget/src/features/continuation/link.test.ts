import { expect, it, vi } from "vitest";
import { consumeLink } from "./link";
it("removes the fragment before using the one-time token", async () => {
  const replaceState = vi.fn(); const exchange = vi.fn().mockResolvedValue({ access_token: "scoped", conversation_id: "c1" });
  const result = await consumeLink<{ access_token: string; conversation_id: string }>("https://widget.example/continuation.html?source=mail#token=one-time&action=exchange", replaceState, exchange);
  expect(replaceState).toHaveBeenCalledWith("https://widget.example/continuation.html?source=mail");
  expect(exchange).toHaveBeenCalledWith("one-time", "exchange");
  expect(replaceState.mock.invocationCallOrder[0]).toBeLessThan(exchange.mock.invocationCallOrder[0]);
  expect(result.access_token).toBe("scoped");
});

it("clears an invalid or failed link without retaining the fragment", async () => {
  const replace = vi.fn(); const exchange = vi.fn();
  await expect(consumeLink("https://widget.example/continuation.html#token=secret&action=invalid", replace, exchange)).rejects.toThrow();
  expect(replace).toHaveBeenCalledWith("https://widget.example/continuation.html"); expect(exchange).not.toHaveBeenCalled();
  exchange.mockRejectedValueOnce(new Error("unavailable"));
  await expect(consumeLink("https://widget.example/continuation.html#token=secret&action=confirm", replace, exchange)).rejects.toThrow();
  expect(replace).toHaveBeenLastCalledWith("https://widget.example/continuation.html");
});
