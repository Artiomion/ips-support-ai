import { http, HttpResponse } from "msw/http";
import type { components } from "../../../../packages/api-client/src/generated";

export function webHandlers(origin: string) {
  return [http.get(`${origin}/api/v1/auth/me`, () => HttpResponse.json({ error: {
    code: "unauthorized", message: "Требуется вход", request_id: "mock",
  } } satisfies components["schemas"]["Error"], { status: 401 }))];
}
