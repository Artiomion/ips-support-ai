import { http, HttpResponse } from "msw/http";
import type { components } from "../../../../packages/api-client/src/generated";

type Scenario = "bot" | "waiting" | "human" | "timeout";
type Message = components["schemas"]["PublicMessage"];
const conversationId = "00000000-0000-4000-8000-000000000001";
const message: Message = { id: "00000000-0000-4000-8000-000000000003", conversation_id: conversationId,
  seq: 1, kind: "assistant", text: "Срок возврата указан в статье.",
  sources: [{ article_id: "00000000-0000-4000-8000-000000000004", revision: 1, title: "Возвраты" }],
  created_at: "2026-11-16T12:00:01Z", attachment_ids: [] };

export function widgetHandlers(scenario: Scenario, origin: string) {
  return [
    http.get(`${origin}/api/v1/public/conversations/:conversation_id/messages`, () => {
      if (scenario === "timeout") return HttpResponse.json({ error: { code: "provider_unavailable", message: "ИИ недоступен", request_id: "mock" } }, { status: 503 });
      const items: Message[] = scenario === "human" ? [message, { ...message, id: "00000000-0000-4000-8000-000000000005", seq: 2, kind: "specialist", text: "Здравствуйте! Я помогу вам.", sources: [] }] : [message];
      return HttpResponse.json({ items, next_cursor: null } satisfies components["schemas"]["PublicMessagePage"]);
    }),
    http.get(`${origin}/api/v1/public/tickets/:ticket_id`, () => {
      if (scenario === "bot" || scenario === "timeout") return HttpResponse.json({ error: { code: "not_found", message: "Заявка не найдена", request_id: "mock" } }, { status: 404 });
      return HttpResponse.json({ id: "00000000-0000-4000-8000-000000000006", conversation_id: conversationId,
        status: scenario === "waiting" ? "waiting_specialist" : "in_progress", priority: "normal", category_id: null,
        assignee_id: scenario === "human" ? "00000000-0000-4000-8000-000000000007" : null,
        version: 1, created_at: "2026-11-16T12:00:03Z", updated_at: "2026-11-16T12:00:03Z" } satisfies components["schemas"]["TicketSummary"]);
    }),
  ];
}
