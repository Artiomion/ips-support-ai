// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Overview } from "./Overview";

afterEach(cleanup);

it("shows the returned counts and distinguishes missing times from zero", async () => {
  const load = vi.fn().mockResolvedValue({ conversations: 8, tickets: 4, ai_confirmed: 3, unconfirmed: 2, handoffs: 1, first_response_seconds_avg: null, resolution_seconds_avg: 0 });
  render(<Overview load={load} />);
  await screen.findByText("8");
  expect(screen.getByText("Нет данных")).toBeTruthy();
  expect(screen.getByText("0 с")).toBeTruthy();
  expect(screen.getByText("3")).toBeTruthy();
});

it("offers a retry when the backend cannot return metrics", async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValue({ conversations: 0, tickets: 0, ai_confirmed: 0, unconfirmed: 0, handoffs: 0, first_response_seconds_avg: null, resolution_seconds_avg: null });
  render(<Overview load={load} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(await screen.findAllByText("Нет данных")).toHaveLength(2);
});

it("ignores an old response after switching to a new data source", async () => {
  type Metrics = Awaited<ReturnType<Parameters<typeof Overview>[0]["load"]>>;
  let resolveOld!: (data: Metrics) => void;
  const oldLoad = () => new Promise<Metrics>((resolve) => { resolveOld = resolve; });
  const newer = { conversations: 5, tickets: 4, ai_confirmed: 3, unconfirmed: 2, handoffs: 1, first_response_seconds_avg: null, resolution_seconds_avg: null };
  const { rerender } = render(<Overview load={oldLoad} />);
  rerender(<Overview load={() => Promise.resolve(newer)} />);
  await screen.findByText("5");
  await act(async () => { resolveOld({ ...newer, conversations: 99 }); });
  expect(screen.queryByText("99")).toBeNull();
  expect(screen.getByText("5")).toBeTruthy();
});
