import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../../../../packages/api-client/src/client";
import { Administration, type AdminApi } from "./Administration";

afterEach(cleanup);
it("shows a validation error returned for an invalid widget origin", async () => {
  const api: AdminApi = { members: vi.fn().mockResolvedValue([]), widgets: vi.fn().mockResolvedValue([]), sections: vi.fn().mockResolvedValue([]), categories: vi.fn().mockResolvedValue([]), createCategory: vi.fn(), invite: vi.fn(), updateMember: vi.fn(), createWidget: vi.fn().mockRejectedValue(new ApiError(422, {})), updateWidget: vi.fn() };
  render(<Administration api={api} widgetOrigin="https://widget.example" apiOrigin="https://api.example" />);
  fireEvent.change(await screen.findByLabelText("Название виджета"), { target: { value: "Поддержка" } });
  fireEvent.change(screen.getByLabelText("Разрешённые адреса сайтов"), { target: { value: "not-an-origin" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать виджет" }));
  await waitFor(() => expect(api.createWidget).toHaveBeenCalled());
  expect((await screen.findByRole("alert")).textContent).toContain("адрес");
});

it("shows a safe embed snippet with the public key", async () => {
  const widget = { id: "w1", public_key: "public-123", name: "Чат", color: "#2563eb", greeting: "Здравствуйте", allowed_origins: ["https://site.example"], section_ids: [], enabled: true };
  const api: AdminApi = { members: vi.fn().mockResolvedValue([]), widgets: vi.fn().mockResolvedValue([widget]), sections: vi.fn().mockResolvedValue([]), categories: vi.fn().mockResolvedValue([]), createCategory: vi.fn(), invite: vi.fn(), updateMember: vi.fn(), createWidget: vi.fn(), updateWidget: vi.fn() };
  render(<Administration api={api} widgetOrigin="https://widget.example" apiOrigin="https://api.example" />);
  const snippet = await screen.findByLabelText("Код виджета Чат") as HTMLTextAreaElement;
  expect(snippet.value).toContain('src="https://widget.example/loader.js"');
  expect(snippet.value).toContain('data-widget-key="public-123"');
  expect(snippet.value).toContain('data-api-origin="https://api.example"');
});
