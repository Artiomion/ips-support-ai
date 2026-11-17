// @vitest-environment node
import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { createAdminApis } from "./api";

let upload: { csrf: string | null; section: FormDataEntryValue | null; visibility: FormDataEntryValue | null; filename: string } | null = null;
const origin = "https://api.example";
let widgetRequest: { csrf: string | null; body: unknown } | null = null;
const server = setupServer(http.post(`${origin}/api/v1/knowledge/imports`, async ({ request }) => {
  const data = await request.formData(); const file = data.get("file");
  upload = { csrf: request.headers.get("X-CSRF-Token"), section: data.get("section_id"), visibility: data.get("visibility"), filename: file && typeof file !== "string" ? file.name : "" };
  return HttpResponse.json({ id: "import-1", state: "queued", error_code: null }, { status: 202 });
}), http.post(`${origin}/api/v1/widgets`, async ({ request }) => {
  widgetRequest = { csrf: request.headers.get("X-CSRF-Token"), body: await request.json() };
  return HttpResponse.json({ error: { code: "validation", message: "Bad origin", request_id: "test" } }, { status: 422 });
}));
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); upload = null; widgetRequest = null; });
afterAll(() => server.close());

it("uploads TXT as multipart with the selected section and CSRF", async () => {
  const { knowledge } = createAdminApis(origin, "csrf-secret");
  const result = await knowledge.importFile(new File(["Текст"], "help.txt", { type: "text/plain" }), "section-1", "internal");
  expect(result.state).toBe("queued");
  expect(upload).toEqual({ csrf: "csrf-secret", section: "section-1", visibility: "internal", filename: "help.txt" });
});

it("preserves the 422 and sends CSRF when saving a widget", async () => {
  const { admin } = createAdminApis(origin, "csrf-secret");
  const body = { name: "Чат", color: "#2563eb", greeting: "Здравствуйте", allowed_origins: ["bad-origin"], section_ids: [] };
  await expect(admin.createWidget(body)).rejects.toMatchObject({ status: 422 });
  expect(widgetRequest).toEqual({ csrf: "csrf-secret", body });
});
