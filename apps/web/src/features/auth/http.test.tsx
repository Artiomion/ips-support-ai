import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw/http";
import { setupServer } from "msw/node";
import { createAuthApi } from "../../routes";
import { AuthApp } from "./AuthApp";

const origin = "https://api.example";
let received: unknown;
const server = setupServer(
  http.get(`${origin}/api/v1/auth/me`, () => HttpResponse.json({ error: { code: "unauthorized", message: "Вход", request_id: "test" } }, { status: 401 })),
  http.post(`${origin}/api/v1/auth/login`, async ({ request }) => {
    received = await request.json();
    return HttpResponse.json({ scope: "tenant", user_id: "1", tenant_id: "2", membership_id: "3", role: "specialist", csrf_token: "csrf" });
  }),
);
beforeAll(() => server.listen());
afterEach(() => { cleanup(); server.resetHandlers(); });
afterAll(() => server.close());

it("signs in through the typed HTTP client", async () => {
  const user = userEvent.setup();
  render(<AuthApp api={createAuthApi(origin, "https://site.example")} />);
  await user.type(await screen.findByLabelText("Электронная почта"), "staff@example.com");
  await user.type(screen.getByLabelText("Пароль"), "password123");
  await user.click(screen.getByRole("button", { name: "Войти" }));
  expect(await screen.findByRole("navigation")).toBeTruthy();
  expect(received).toEqual({ email: "staff@example.com", password: "password123" });
  expect(screen.queryByText("Настройки компании")).toBeNull();
});
