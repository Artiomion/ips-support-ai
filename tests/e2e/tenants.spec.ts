import { randomUUID } from "node:crypto";
import { test, expect, type APIRequestContext } from "@playwright/test";
import type { components } from "../../packages/api-client/src/generated";

const panelOrigin = "http://127.0.0.1:5173";
const widgetOrigin = "http://127.0.0.1:5174";
type Widget = components["schemas"]["Widget"];
type Session = components["schemas"]["PublicSession"];
type Companies = {
  alpha: APIRequestContext;
  beta: APIRequestContext;
  alphaWidget: Widget;
  betaWidget: Widget;
  alphaCsrf: string;
  betaCsrf: string;
  betaCredentials: { email: string; password: string };
  customer: (widget: Widget, origin: string) => Promise<APIRequestContext>;
};

// Each context owns its cookie jar; all accounts and content are disposable test data.
const isolationTest = test.extend<{ companies: Companies }>({
  companies: async ({ playwright, baseURL }, use) => {
    const contexts: APIRequestContext[] = [];
    async function context(headers: Record<string, string>) {
      const value = await playwright.request.newContext({ baseURL, extraHTTPHeaders: headers });
      contexts.push(value);
      return value;
    }
    async function company(name: string, siteOrigin: string) {
      const staff = await context({ Origin: panelOrigin });
      const credentials = { email: `e2e-${randomUUID()}@example.org`, password: randomUUID() };
      const registered = await staff.post("/api/v1/auth/register", { data: {
        organization_name: name, ...credentials,
      } });
      expect(registered.status()).toBe(201);
      const session: components["schemas"]["TenantStaffSession"] = await registered.json();
      const created = await staff.post("/api/v1/widgets", { headers: { "X-CSRF-Token": session.csrf_token }, data: {
        name, color: "#123456", greeting: "Welcome", allowed_origins: [siteOrigin], section_ids: [],
      } });
      expect(created.status()).toBe(201);
      return { staff, csrf: session.csrf_token, widget: await created.json() as Widget, credentials };
    }
    try {
      const alpha = await company("E2E Alpha", "https://alpha.example");
      const beta = await company("E2E Beta", "https://beta.example");
      async function customer(widget: Widget, origin: string) {
        const bootstrap = await context({ Origin: origin });
        const response = await bootstrap.post(`/api/v1/public/widgets/${widget.public_key}/sessions`, { data: {} });
        expect(response.status()).toBe(201);
        const session: Session = await response.json();
        return context({ Origin: widgetOrigin, Authorization: `Bearer ${session.access_token}` });
      }
      await use({ alpha: alpha.staff, beta: beta.staff, alphaCsrf: alpha.csrf, betaCsrf: beta.csrf, betaCredentials: beta.credentials, alphaWidget: alpha.widget, betaWidget: beta.widget, customer });
    } finally {
      await Promise.all(contexts.map((value) => value.dispose()));
    }
  },
});

isolationTest("staff and customers cannot read or write another company's conversation", async ({ companies }) => {
  const { alpha, beta, alphaWidget, betaWidget, customer } = companies;
  const alphaCustomer = await customer(alphaWidget, "https://alpha.example");
  const betaCustomer = await customer(betaWidget, "https://beta.example");
  const created = await alphaCustomer.post("/api/v1/public/conversations", { data: {} });
  expect(created.status()).toBe(201);
  const conversation: components["schemas"]["Conversation"] = await created.json();
  const publicPath = `/api/v1/public/conversations/${conversation.id}/messages`;
  const staffPath = `/api/v1/conversations/${conversation.id}/messages`;
  const sent = await alphaCustomer.post(publicPath, { data: { client_message_id: randomUUID(), text: "Only Alpha can read this" } });
  expect(sent.status()).toBe(201);
  expect((await alpha.get(staffPath)).status()).toBe(200);
  expect((await beta.get(staffPath)).status()).toBe(404);
  expect((await betaCustomer.get(publicPath)).status()).toBe(404);
  expect((await betaCustomer.post(publicPath, { data: { client_message_id: randomUUID(), text: "Forbidden" } })).status()).toBe(404);
  expect((await beta.post(staffPath, { headers: { "X-CSRF-Token": companies.betaCsrf }, data: { client_message_id: randomUUID(), text: "Forbidden", internal: false } })).status()).toBe(404);
  const handoff = await alphaCustomer.post(`/api/v1/public/conversations/${conversation.id}/handoff`, { data: { reason: "Please help" } });
  expect(handoff.status()).toBe(200);
  const ticket: components["schemas"]["TicketSummary"] = await handoff.json();
  expect((await alpha.get(`/api/v1/tickets/${ticket.id}`)).status()).toBe(200);
  expect((await beta.get(`/api/v1/tickets/${ticket.id}`)).status()).toBe(404);
  expect((await betaCustomer.get(`/api/v1/public/tickets/${ticket.id}`)).status()).toBe(404);
  const queue = await beta.get("/api/v1/tickets");
  expect(queue.status()).toBe(200);
  expect((await queue.json()).items).toEqual([]);
});

isolationTest("another customer of the same widget cannot read the conversation; notes stay private", async ({ companies }) => {
  const { alpha, alphaCsrf, alphaWidget, customer } = companies;
  const owner = await customer(alphaWidget, "https://alpha.example");
  const stranger = await customer(alphaWidget, "https://alpha.example");
  const created = await owner.post("/api/v1/public/conversations", { data: {} });
  expect(created.status()).toBe(201);
  const { id } = await created.json() as components["schemas"]["Conversation"];
  const publicPath = `/api/v1/public/conversations/${id}/messages`;
  expect((await stranger.get(publicPath)).status()).toBe(404);
  expect((await stranger.post(publicPath, { data: { client_message_id: randomUUID(), text: "Forbidden" } })).status()).toBe(404);
  const staffPath = `/api/v1/conversations/${id}/messages`;
  for (const [text, internal] of [["Public reply", false], ["Private note", true]] as const) {
    const response = await alpha.post(staffPath, { headers: { "X-CSRF-Token": alphaCsrf }, data: { client_message_id: randomUUID(), text, internal } });
    expect(response.status()).toBe(201);
  }
  const publicHistory = await owner.get(publicPath);
  expect(publicHistory.status()).toBe(200);
  expect((await publicHistory.json()).items.map((item: components["schemas"]["PublicMessage"]) => item.text)).toEqual(["Public reply"]);
  const staffHistory = await alpha.get(staffPath);
  expect(staffHistory.status()).toBe(200);
  expect((await staffHistory.json()).items.map((item: components["schemas"]["StaffMessage"]) => item.text)).toEqual(["Public reply", "Private note"]);
});

test("conversation CORS accepts the widget origin and refuses other origins", async ({ request }) => {
  const headers = { "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type" };
  const allowed = await request.fetch("/api/v1/public/conversations", { method: "OPTIONS", headers: { ...headers, Origin: widgetOrigin } });
  expect(allowed.status()).toBe(200);
  expect(allowed.headers()["access-control-allow-origin"]).toBe(widgetOrigin);
  expect(allowed.headers()["access-control-allow-credentials"]).toBeUndefined();
  const denied = await request.fetch("/api/v1/public/conversations", { method: "OPTIONS", headers: { ...headers, Origin: "https://attacker.example" } });
  expect(denied.status()).toBe(403);
  expect(denied.headers()["access-control-allow-origin"]).toBeUndefined();
});

isolationTest("@browser staff sees its own queue, sends a reply, and keeps notes private", async ({ companies, page }) => {
  const alphaCustomer = await companies.customer(companies.alphaWidget, "https://alpha.example");
  const betaCustomer = await companies.customer(companies.betaWidget, "https://beta.example");
  async function createTicket(client: APIRequestContext, text: string) {
    const created = await client.post("/api/v1/public/conversations", { data: {} });
    expect(created.status()).toBe(201);
    const conversation: components["schemas"]["Conversation"] = await created.json();
    const message = await client.post(`/api/v1/public/conversations/${conversation.id}/messages`, { data: { client_message_id: randomUUID(), text } });
    expect(message.status()).toBe(201);
    const handoff = await client.post(`/api/v1/public/conversations/${conversation.id}/handoff`, { data: { reason: "Please help" } });
    expect(handoff.status()).toBe(200);
    return await handoff.json() as components["schemas"]["TicketSummary"];
  }
  const alphaTicket = await createTicket(alphaCustomer, "Alpha request");
  const betaTicket = await createTicket(betaCustomer, "Beta request");

  await page.goto(panelOrigin);
  await page.getByLabel("Электронная почта", { exact: true }).fill(companies.betaCredentials.email);
  await page.getByLabel("Пароль", { exact: true }).fill(companies.betaCredentials.password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(page.getByText("Вы вошли как администратор.", { exact: true })).toBeVisible();
  const ownTicket = page.getByRole("button", { name: new RegExp(betaTicket.id) });
  await expect(ownTicket).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(alphaTicket.id) })).toHaveCount(0);
  await ownTicket.click();
  await expect(page.getByText("Клиент: Beta request", { exact: true })).toBeVisible();

  await page.getByRole("combobox", { name: /Тип сообщения/ }).selectOption("internal");
  await page.getByLabel("Текст сообщения", { exact: true }).fill("Internal Beta note");
  await page.getByRole("button", { name: "Сохранить заметку", exact: true }).click();
  await expect(page.getByText("Внутренняя заметка: Internal Beta note", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: /Тип сообщения/ }).selectOption("reply");
  await page.getByLabel("Текст сообщения", { exact: true }).fill("Public Beta reply");
  await page.getByRole("button", { name: "Отправить ответ", exact: true }).click();
  await expect(page.getByText("Специалист: Public Beta reply", { exact: true })).toBeVisible();

  const history = await betaCustomer.get(`/api/v1/public/conversations/${betaTicket.conversation_id}/messages`);
  expect(history.status()).toBe(200);
  expect((await history.json()).items.map((item: components["schemas"]["PublicMessage"]) => item.text)).toEqual(["Beta request", "Public Beta reply"]);
  await page.reload();
  await expect(ownTicket).toBeVisible();
  await expect(page.getByRole("button", { name: new RegExp(alphaTicket.id) })).toHaveCount(0);
});
