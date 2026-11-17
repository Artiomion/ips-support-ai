import type { components } from "../../../../../packages/api-client/src/generated";
import type { createApiClient } from "../../../../../packages/api-client/src/client";
import type { SupportApi } from "./SupportActions";

type Client = ReturnType<typeof createApiClient>;
function required<T>(value: T | undefined): T { if (value === undefined) throw new Error("Пустой ответ"); return value; }
export function createSupportApi(client: Client): SupportApi {
  return {
    async handoff(conversationId) { return required((await client.post("/api/v1/public/conversations/{conversation_id}/handoff", { params: { path: { conversation_id: conversationId } }, body: { reason: "Клиент запросил специалиста" } })).data); },
    async ticket(ticketId) { return required((await client.get("/api/v1/public/tickets/{ticket_id}", { params: { path: { ticket_id: ticketId } } })).data); },
    async confirm(ticketId) { return required((await client.post("/api/v1/public/tickets/{ticket_id}/confirm", { params: { path: { ticket_id: ticketId } } })).data); },
    async reopen(ticketId) { return required((await client.post("/api/v1/public/tickets/{ticket_id}/reopen", { params: { path: { ticket_id: ticketId } } })).data); },
    async rate(ticketId, score) { await client.post("/api/v1/public/tickets/{ticket_id}/rating", { params: { path: { ticket_id: ticketId } }, body: { score } satisfies components["schemas"]["RatingInput"] }); },
    async confirmAi(conversationId) { await client.post("/api/v1/public/conversations/{conversation_id}/confirm-ai", { params: { path: { conversation_id: conversationId } } }); },
    async email(conversationId, email) { await client.post("/api/v1/public/customer/email/request", { body: { conversation_id: conversationId, email } }); },
  };
}
