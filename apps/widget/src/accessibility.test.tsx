import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen } from "@testing-library/react";
import { mountWidget } from "./loader";
import { handleEscape } from "./bridge";

afterEach(() => { document.body.innerHTML = ""; });
describe("widget accessibility", () => {
  it("opens using the keyboard and returns focus after Escape closes it", async () => {
    const user = userEvent.setup();
    const script = document.createElement("script"); script.src = "https://widget.example/loader.js"; script.dataset.widgetKey = "public-key";
    mountWidget(script);
    const button = screen.getByRole("button", { name: "Открыть чат" });
    await user.tab(); expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    const frame = screen.getByTitle("Чат поддержки") as HTMLIFrameElement;
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(frame.hidden).toBe(false); expect(document.activeElement).toBe(frame);
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, origin: "https://widget.example", data: { type: "ips:close" } }));
    expect(frame.hidden).toBe(true); expect(button.getAttribute("aria-expanded")).toBe("false"); expect(document.activeElement).toBe(button);
  });
  it("does not close in response to an unrelated origin", async () => {
    const script = document.createElement("script"); script.src = "https://widget.example/loader.js"; script.dataset.widgetKey = "key"; mountWidget(script);
    const button = screen.getByRole("button", { name: "Открыть чат" }); button.click();
    const frame = screen.getByTitle("Чат поддержки") as HTMLIFrameElement;
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, origin: "https://evil.example", data: { type: "ips:close" } }));
    expect(frame.hidden).toBe(false);
  });
  it("sends an Escape request only to the verified parent origin", () => {
    const postMessage = vi.fn();
    const event = new KeyboardEvent("keydown", { key: "Escape", cancelable: true });
    handleEscape(event, { postMessage } as unknown as Window, "https://site.example");
    expect(postMessage).toHaveBeenCalledWith({ type: "ips:close" }, "https://site.example");
    expect(event.defaultPrevented).toBe(true);
  });
});
