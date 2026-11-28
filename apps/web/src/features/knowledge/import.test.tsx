import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Knowledge, type KnowledgeApi } from "./Knowledge";

afterEach(() => { cleanup(); vi.useRealTimers(); });
it("reports import progress and server processing errors", async () => {
  const api: KnowledgeApi = { sections: vi.fn().mockResolvedValue([{ id: "s1", name: "Общее" }]), createSection: vi.fn(), articles: vi.fn().mockResolvedValue([]), get: vi.fn(), create: vi.fn(), update: vi.fn(), revisions: vi.fn(), restore: vi.fn(), importFile: vi.fn().mockResolvedValue({ id: "i1", state: "processing", error_code: null }), importStatus: vi.fn().mockResolvedValue({ id: "i1", state: "failed", error_code: "invalid_text" }) };
  render(<Knowledge api={api} />);
  await screen.findAllByRole("option", { name: "Общее" });
  fireEvent.change(screen.getByLabelText("TXT-файл"), { target: { files: [new File(["text"], "help.txt", { type: "text/plain" })] } });
  fireEvent.change(screen.getByLabelText("Раздел импорта"), { target: { value: "s1" } });
  vi.useFakeTimers();
  await act(async () => { fireEvent.submit(screen.getByRole("button", { name: "Загрузить TXT" }).closest("form")!); });
  expect(screen.getByText(/обрабатывается/)).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.getByText(/ошибка.*invalid_text/)).toBeTruthy();
});
