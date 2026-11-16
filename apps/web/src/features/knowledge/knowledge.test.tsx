import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../../../../packages/api-client/src/client";
import { Knowledge, type KnowledgeApi } from "./Knowledge";

afterEach(cleanup);
it("marks drafts and preserves edits on a revision conflict", async () => {
  const article = { id: "a1", section_id: "s1", title: "Доставка", body: "Текст", visibility: "public" as const, status: "draft" as const, revision: 1 };
  const api: KnowledgeApi = { sections: vi.fn().mockResolvedValue([{ id: "s1", name: "Общее" }]), createSection: vi.fn(), articles: vi.fn().mockResolvedValue([article]), get: vi.fn(), create: vi.fn(), update: vi.fn().mockRejectedValue(new ApiError(409, {})), revisions: vi.fn().mockResolvedValue([]), restore: vi.fn(), importFile: vi.fn(), importStatus: vi.fn() };
  render(<Knowledge api={api} />);
  expect(await screen.findByText("Черновик")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Доставка/ }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Сохранить статью" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Название статьи"), { target: { value: "Новый заголовок" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить статью" }));
  await waitFor(() => expect(api.update).toHaveBeenCalled());
  expect((await screen.findByRole("alert")).textContent).toContain("изменилась");
  expect((screen.getByLabelText("Название статьи") as HTMLInputElement).value).toBe("Новый заголовок");
});
