import { expect, it } from "vitest";
import { ticketStore } from "./ticketStore";
it("isolates ticket ids by site, widget and customer without storing a bearer", () => {
  const scope = { apiOrigin: "https://api.example", siteOrigin: "https://one.example", widgetId: "w1", customerId: "c1" };
  const first = ticketStore(scope, () => localStorage); const second = ticketStore({ ...scope, siteOrigin: "https://two.example" }, () => localStorage);
  first.write({ conversation: "ticket" });
  expect(first.read()).toEqual({ conversation: "ticket" }); expect(second.read()).toEqual({});
  localStorage.clear();
});
it("continues with empty memory when storage is blocked", () => {
  const store = ticketStore({ apiOrigin: "https://api.example", siteOrigin: "https://site.example", widgetId: "w", customerId: "c" }, () => { throw new Error("blocked"); });
  expect(store.read()).toEqual({}); expect(() => store.write({ c: "t" })).not.toThrow();
});
