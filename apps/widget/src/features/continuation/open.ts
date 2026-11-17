import { createApiClient } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";
import type { LinkAction } from "./link";
export type ContinuationData = { token: string; conversationId: string; session: components["schemas"]["CurrentPublicSession"]; apiOrigin: string };
export async function openLink(apiOrigin: string, token: string, action: LinkAction): Promise<ContinuationData> {
  const api = createApiClient({ baseUrl: apiOrigin });
  const response = action === "confirm"
    ? await api.post("/api/v1/public/customer/email/confirm", { body: { token } })
    : await api.post("/api/v1/public/customer/links/exchange", { body: { token } });
  if (!response.data) throw new Error("Пустой ответ");
  const scoped = createApiClient({ baseUrl: apiOrigin, customerToken: response.data.access_token });
  const session = await scoped.get("/api/v1/public/session");
  if (!session.data) throw new Error("Пустая сессия");
  return { token: response.data.access_token, conversationId: response.data.conversation_id, session: session.data, apiOrigin };
}
