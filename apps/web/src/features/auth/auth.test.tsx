import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { AuthApp, type AuthApi } from "./AuthApp";

const specialist = { scope: "tenant", user_id: "1", tenant_id: "2", membership_id: "3", role: "specialist", csrf_token: "csrf" } as const;
const admin = { ...specialist, role: "admin" } as const;
const platform = { scope: "platform", user_id: "1", role: "superadmin", csrf_token: "csrf" } as const;
const api = (session: AuthApi["session"], login: AuthApi["login"] = vi.fn()): AuthApi => ({ session, login, register: vi.fn() });
afterEach(cleanup);

describe("authentication", () => {
  it("shows login after an unauthenticated session", async () => {
    render(<AuthApp api={api(vi.fn().mockRejectedValue(new Error("401")))} />);
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeTruthy();
  });
  it("does not show a signed-in menu before the server responds", async () => {
    let resolve!: (value: typeof specialist) => void;
    const pending = new Promise<typeof specialist>((done) => { resolve = done; });
    render(<AuthApp api={api(() => pending)} />);
    expect(screen.queryByRole("navigation")).toBeNull();
    resolve(specialist);
    expect(await screen.findByRole("navigation")).toBeTruthy();
  });
  it("shows role-specific controls", async () => {
    const view = render(<AuthApp api={api(vi.fn().mockResolvedValue(specialist))} />);
    await screen.findByRole("navigation");
    expect(screen.queryByText("Настройки компании")).toBeNull();
    view.unmount();
    const next = render(<AuthApp api={api(vi.fn().mockResolvedValue(admin))} />);
    expect(await screen.findByText("Настройки компании")).toBeTruthy();
    next.unmount();
    render(<AuthApp api={api(vi.fn().mockResolvedValue(platform))} />);
    expect(await screen.findByText("Управление платформой")).toBeTruthy();
    expect(screen.queryByText("Настройки компании")).toBeNull();
  });
  it("submits the login form and keeps it visible after a rejected login", async () => {
    const login = vi.fn().mockRejectedValue(new Error("401"));
    render(<AuthApp api={api(vi.fn().mockRejectedValue(new Error("401")), login)} />);
    const email = await screen.findByLabelText("Электронная почта");
    fireEvent.change(email, { target: { value: "staff@example.com" } });
    fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "password123" } });
    fireEvent.submit(screen.getByRole("button", { name: "Войти" }).closest("form")!);
    await waitFor(() => expect(login).toHaveBeenCalledWith({ email: "staff@example.com", password: "password123" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });
  it("registers the company and waits for the server", async () => {
    const register = vi.fn().mockResolvedValue(admin);
    render(<AuthApp api={{ ...api(vi.fn().mockRejectedValue(new Error("401"))), register }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Создать компанию" }));
    fireEvent.change(screen.getByLabelText("Название компании"), { target: { value: "Компания" } });
    fireEvent.change(screen.getByLabelText("Электронная почта"), { target: { value: "admin@example.com" } });
    fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "password123" } });
    fireEvent.submit(screen.getByRole("button", { name: "Зарегистрироваться" }).closest("form")!);
    await waitFor(() => expect(register).toHaveBeenCalledWith({ organization_name: "Компания", email: "admin@example.com", password: "password123" }));
    expect(await screen.findByText("Настройки компании")).toBeTruthy();
  });
  it("supports signing in with the keyboard", async () => {
    const login = vi.fn().mockResolvedValue(specialist);
    const user = userEvent.setup();
    render(<AuthApp api={api(vi.fn().mockRejectedValue(new Error("401")), login)} />);
    await screen.findByLabelText("Электронная почта");
    await user.tab();
    await user.keyboard("staff@example.com");
    await user.tab();
    await user.keyboard("password123{Enter}");
    await waitFor(() => expect(login).toHaveBeenCalledWith({ email: "staff@example.com", password: "password123" }));
    expect(await screen.findByRole("navigation")).toBeTruthy();
  });
  it("shows the inbox only to a tenant session", async () => {
    const renderTenant = vi.fn(() => <p>Рабочее место</p>);
    const first = render(<AuthApp api={api(vi.fn().mockResolvedValue(platform))} renderTenant={renderTenant} />);
    await screen.findByText("Управление платформой");
    expect(renderTenant).not.toHaveBeenCalled();
    first.unmount();
    render(<AuthApp api={api(vi.fn().mockResolvedValue(specialist))} renderTenant={renderTenant} />);
    expect(await screen.findByText("Рабочее место")).toBeTruthy();
  });
});
