import createClient from "openapi-fetch";
import type { paths } from "./generated";

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly body: unknown) {
    super(`API request failed (${status})`);
    this.name = "ApiError";
  }
}

export function createApiClient(options: { baseUrl: string; csrfToken?: string; customerToken?: string }) {
  const base = new URL(options.baseUrl);
  if (!["http:", "https:"].includes(base.protocol) || base.pathname !== "/" || base.search || base.hash || base.username || base.password) {
    throw new Error("baseUrl must be an HTTP(S) origin");
  }
  const client = createClient<paths>({
    baseUrl: base.origin,
    credentials: "include",
    redirect: "error",
    fetch: async (request) => {
      const url = new URL(request.url);
      if (url.origin !== base.origin) throw new Error("API request origin mismatch");
      const headers = new Headers(request.headers);
      const isPublic = url.pathname.startsWith("/api/v1/public/");
      headers.delete("Authorization");
      headers.delete("X-CSRF-Token");
      if (isPublic && options.customerToken) headers.set("Authorization", `Bearer ${options.customerToken}`);
      if (!isPublic && request.method !== "GET" && options.csrfToken) headers.set("X-CSRF-Token", options.csrfToken);
      const response = await fetch(new Request(request, { headers, redirect: "error", credentials: isPublic ? "omit" : "include" }));
      if (!response.ok) {
        let body: unknown;
        try { body = await response.clone().json(); } catch { body = undefined; }
        throw new ApiError(response.status, body);
      }
      return response;
    },
  });
  return { get: client.GET, post: client.POST, put: client.PUT, patch: client.PATCH };
}
