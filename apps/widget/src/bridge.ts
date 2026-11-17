export function isTrustedMessage(event: Pick<MessageEvent, "source" | "origin">, source: Window, origin: string) {
  return event.source === source && event.origin === origin;
}

export type InitMessage = { type: "ips:init"; token: string; apiOrigin: string };
export function isInitMessage(value: unknown): value is InitMessage {
  if (!value || typeof value !== "object") return false;
  const data = value as Record<string, unknown>;
  if (data.type !== "ips:init" || typeof data.token !== "string" || !data.token || typeof data.apiOrigin !== "string") return false;
  try { const url = new URL(data.apiOrigin); return ["http:", "https:"].includes(url.protocol) && url.origin === data.apiOrigin; } catch { return false; }
}

export async function acceptInit<T extends { site_origin: string }>(event: Pick<MessageEvent, "source" | "origin" | "data">, parent: Window, verify: (token: string, apiOrigin: string) => Promise<T>) {
  if (event.source !== parent || !isInitMessage(event.data)) throw new Error("Untrusted message");
  const { token, apiOrigin } = event.data;
  const session = await verify(token, apiOrigin);
  if (session.site_origin !== event.origin) throw new Error("Origin mismatch");
  return { token, apiOrigin, parentOrigin: event.origin, session };
}
