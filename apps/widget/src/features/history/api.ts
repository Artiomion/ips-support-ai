import { createApiClient } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";

type Client = ReturnType<typeof createApiClient>;
type Conversation = components["schemas"]["Conversation"];
export async function listConversations(client: Client) {
  const all: Conversation[] = []; let cursor: string | undefined;
  do {
    const response = await client.get("/api/v1/public/conversations", { params: { query: { limit: 100, ...(cursor ? { cursor } : {}) } } });
    if (!response.data) throw new Error("Пустой ответ");
    all.push(...response.data.items); cursor = response.data.next_cursor || undefined;
  } while (cursor);
  return all;
}
