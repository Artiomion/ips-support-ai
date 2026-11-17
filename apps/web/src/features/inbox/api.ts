import { createApiClient } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";
import type { InboxApi } from "./Inbox";

type Ticket = components["schemas"]["TicketSummary"];
type Message = components["schemas"]["StaffMessage"];
function required<T>(value: T | undefined): T { if (value === undefined) throw new Error("Пустой ответ сервера"); return value; }
export function createInboxApi(baseUrl: string, csrfToken: string): InboxApi {
  const client = createApiClient({ baseUrl, csrfToken });
  const headers = { "X-CSRF-Token": csrfToken };
  return {
    async list(filters) { const r = await client.get("/api/v1/tickets", { params: { query: { limit: 30, ...filters } } }); return required(r.data); },
    async get(id) { const r = await client.get("/api/v1/tickets/{ticket_id}", { params: { path: { ticket_id: id } } }); return required(r.data) satisfies Ticket; },
    async messages(id, cursor) { const r = await client.get("/api/v1/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id }, query: { limit: 100, ...(cursor ? { cursor } : {}) } } }); return required(r.data); },
    async claim(id, version) { const r = await client.post("/api/v1/tickets/{ticket_id}/claim", { params: { path: { ticket_id: id }, header: headers }, body: { expected_version: version } }); return required(r.data); },
    async update(id, body) { const r = await client.put("/api/v1/tickets/{ticket_id}", { params: { path: { ticket_id: id }, header: headers }, body }); return required(r.data); },
    async send(id, body) { const r = await client.post("/api/v1/conversations/{conversation_id}/messages", { params: { path: { conversation_id: id }, header: headers }, body }); return required(r.data) satisfies Message; },
  };
}
