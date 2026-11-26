import { createApiClient } from "../../../../../packages/api-client/src/client";

export function createOverviewLoader(baseUrl: string) {
  const client = createApiClient({ baseUrl });
  return async () => {
    const result = await client.get("/api/v1/analytics/overview");
    if (!result.data) throw new Error("Пустой ответ сервера");
    return result.data;
  };
}
