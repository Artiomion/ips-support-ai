/// <reference types="vite/client" />
import { createApiClient } from "../../../packages/api-client/src/client";
import { useMemo } from "react";
import { Administration } from "./features/admin/Administration";
import { Knowledge } from "./features/knowledge/Knowledge";
import { createAdminApis } from "./features/admin/api";
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
function TenantInbox({ csrfToken, isAdmin }: { csrfToken: string; isAdmin: boolean }) {
  const api = useMemo(() => createInboxApi(import.meta.env.VITE_API_BASE_URL || window.location.origin, csrfToken), [csrfToken]);
  const adminApis = useMemo(() => createAdminApis(import.meta.env.VITE_API_BASE_URL || window.location.origin, csrfToken), [csrfToken]);
  const apiOrigin = import.meta.env.VITE_API_BASE_URL || window.location.origin;
  const widgetOrigin = import.meta.env.VITE_WIDGET_ORIGIN || window.location.origin;
  return <><Inbox api={api} />{isAdmin && <><Administration api={adminApis.admin} apiOrigin={apiOrigin} widgetOrigin={widgetOrigin} /><Knowledge api={adminApis.knowledge} /></>}</>;
}
export function Routes() { return <AuthApp api={authApi} renderTenant={(session) => <TenantInbox csrfToken={session.csrf_token} isAdmin={session.role === "admin"} />} />; }
