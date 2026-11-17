import { isTrustedMessage } from "./bridge";

type StorageProvider = () => Storage;
export function createTokenStore(key: string, storage: StorageProvider = () => localStorage) {
  const name = `ips:${key}:session`;
  let memory: string | null = null;
  let available = true;
  return {
    get() { if (available) try { memory = storage().getItem(name); } catch { available = false; } return memory; },
    set(value: string) { memory = value; if (available) try { storage().setItem(name, value); } catch { available = false; } },
    clear() { memory = null; if (available) try { storage().removeItem(name); } catch { available = false; } },
  };
}

export async function resolveSession(apiOrigin: string, key: string, store: ReturnType<typeof createTokenStore>, fetcher: typeof fetch = fetch) {
  const token = store.get();
  if (token) {
    const response = await fetcher(`${apiOrigin}/api/v1/public/session`, { headers: { Authorization: `Bearer ${token}` }, credentials: "omit", redirect: "error" });
    if (response.ok) return token;
    if (response.status !== 401) throw new Error("Не удалось восстановить сессию");
    store.clear();
  }
  const response = await fetcher(`${apiOrigin}/api/v1/public/widgets/${encodeURIComponent(key)}/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", credentials: "omit", redirect: "error" });
  if (!response.ok) throw new Error("Не удалось открыть чат");
  const body: unknown = await response.json();
  if (!body || typeof body !== "object" || !("access_token" in body) || typeof body.access_token !== "string" || !body.access_token) throw new Error("Некорректный ответ сервера");
  store.set(body.access_token); return body.access_token;
}

let widgetCounter = 0;
export function mountWidget(script: HTMLScriptElement) {
  const key = script.dataset.widgetKey || "";
  if (!key) return;
  const scriptUrl = new URL(script.src);
  const widgetOrigin = scriptUrl.origin;
  let apiOrigin: string;
  try { const url = new URL(script.dataset.apiOrigin || widgetOrigin); if (!["https:", "http:"].includes(url.protocol) || url.origin !== url.href.replace(/\/$/, "")) throw Error(); apiOrigin = url.origin; } catch { return; }
  const button = document.createElement("button"); button.type = "button"; button.textContent = "Открыть чат";
  const frame = document.createElement("iframe"); frame.title = "Чат поддержки"; frame.id = `ips-widget-${++widgetCounter}`;
  const frameUrl = new URL("./index.html", scriptUrl); frameUrl.searchParams.set("parent_origin", window.location.origin); frame.src = frameUrl.href;
  button.setAttribute("aria-controls", frame.id); button.setAttribute("aria-expanded", "false");
  frame.hidden = true;
  Object.assign(frame.style, { position: "fixed", right: "16px", bottom: "64px", width: "min(360px, calc(100vw - 32px))", height: "min(540px, calc(100vh - 80px))", border: "1px solid #bbb", background: "white", zIndex: "2147483646" });
  Object.assign(button.style, { position: "fixed", right: "16px", bottom: "16px", zIndex: "2147483647" });
  document.body.append(button, frame);
  const store = createTokenStore(key);
  let opened = false; let ready = false; let pending = false; let queued = false;
  async function sendInit() {
    if (!opened || !ready) return;
    if (pending) { queued = true; return; }
    pending = true;
    try {
      const token = await resolveSession(apiOrigin, key, store);
      if (opened) frame.contentWindow?.postMessage({ type: "ips:init", token, apiOrigin }, widgetOrigin);
    } catch { opened = false; frame.hidden = true; button.textContent = "Чат недоступен — повторить"; button.setAttribute("aria-expanded", "false"); button.focus(); }
    finally { pending = false; if (queued) { queued = false; void sendInit(); } }
  }
  function close() {
    opened = false; frame.hidden = true; button.textContent = "Открыть чат"; button.setAttribute("aria-expanded", "false");
    frame.contentWindow?.postMessage({ type: "ips:closed" }, widgetOrigin); button.focus();
  }
  button.addEventListener("click", () => {
    if (opened) { close(); return; }
    opened = true; frame.hidden = false; button.textContent = "Закрыть чат"; button.setAttribute("aria-expanded", "true");
    frame.focus(); void sendInit();
  });
  window.addEventListener("message", (event) => {
    if (!frame.contentWindow || !isTrustedMessage(event, frame.contentWindow, widgetOrigin)) return;
    if (event.data?.type === "ips:ready") { ready = true; void sendInit(); }
    else if (event.data?.type === "ips:close" && opened) close();
  });
}

const currentScript = document.currentScript;
if (currentScript instanceof HTMLScriptElement && currentScript.dataset.widgetKey) mountWidget(currentScript);
