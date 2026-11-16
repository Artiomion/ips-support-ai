/// <reference types="vite/client" />
import { createApiClient } from "../../../packages/api-client/src/client";
import { useMemo } from "react";
import { Inbox } from "./features/inbox/Inbox";
import { createInboxApi } from "./features/inbox/api";
import { AuthApp, type AuthApi } from "./features/auth/AuthApp";

export function createAuthApi(baseUrl: string, siteOrigin: string): AuthApi {
  const client = createApiClient({ baseUrl });
  const origin = () => ({ header: { Origin: siteOrigin } });
  return {
    async session() { const result = await client.get("/api/v1/auth/me"); if (!result.data) throw new Error("Empty session"); return result.data; },
    async login(body) { const result = await client.post("/api/v1/auth/login", { params: origin(), body }); if (!result.data) throw new Error("Empty session"); return result.data; },
    async register(body) { const result = await client.post("/api/v1/auth/register", { params: origin(), body }); if (!result.data) throw new Error("Empty session"); return result.data; },
  };
}
const authApi = createAuthApi(import.meta.env.VITE_API_BASE_URL || window.location.origin, window.location.origin);
function TenantInbox({ csrfToken }: { csrfToken: string }) {
  const api = useMemo(() => createInboxApi(import.meta.env.VITE_API_BASE_URL || window.location.origin, csrfToken), [csrfToken]);
  return <Inbox api={api} />;
}
export function Routes() { return <AuthApp api={authApi} renderTenant={(session) => <TenantInbox csrfToken={session.csrf_token} />} />; }
