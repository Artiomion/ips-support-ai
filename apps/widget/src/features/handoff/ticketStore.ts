export function ticketStore(scope: { apiOrigin: string; siteOrigin: string; widgetId: string; customerId: string }, storage: () => Storage = () => window.localStorage) {
  const key = `ips:tickets:${[scope.apiOrigin, scope.siteOrigin, scope.widgetId, scope.customerId].map(encodeURIComponent).join(":")}`;
  return {
    read(): Record<string, string> {
      try {
        const value: unknown = JSON.parse(storage().getItem(key) || "{}");
        if (!value || typeof value !== "object" || Array.isArray(value)) return {};
        return Object.fromEntries(Object.entries(value).filter(([id, ticket]) => id.length <= 128 && typeof ticket === "string" && ticket.length <= 128).slice(-100));
      } catch { return {}; }
    },
    write(value: Record<string, string>) { try { storage().setItem(key, JSON.stringify(Object.fromEntries(Object.entries(value).slice(-100)))); } catch { /* memory remains usable */ } },
  };
}
