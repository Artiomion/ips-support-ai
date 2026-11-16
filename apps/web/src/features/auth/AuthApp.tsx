import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";

type Session = components["schemas"]["StaffSession"];
type Login = components["schemas"]["LoginInput"];
type Register = components["schemas"]["RegisterInput"];
export type AuthApi = { session: () => Promise<Session>; login: (body: Login) => Promise<Session>; register: (body: Register) => Promise<Session> };

export function AuthApp({ api }: { api: AuthApi }) {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [company, setCompany] = useState("");

  useEffect(() => {
    let current = true;
    api.session().then((value) => { if (current) setSession(value); }).catch((err: unknown) => {
      if (current && (!(err instanceof ApiError) || err.status !== 401)) setError("Не удалось проверить сессию. Попробуйте войти.");
    }).finally(() => { if (current) setChecking(false); });
    return () => { current = false; };
  }, [api]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError("");
    try {
      const result = registering ? await api.register({ organization_name: company, email, password }) : await api.login({ email, password });
      setPassword("");
      setSession(result);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Неверная почта или пароль." : "Не удалось выполнить запрос. Проверьте данные и попробуйте ещё раз.");
    } finally { setPending(false); }
  }

  if (checking) return <p role="status">Проверяем сессию…</p>;
  if (session) return <><p>Вы вошли как {session.role === "admin" ? "администратор" : session.role === "specialist" ? "специалист" : "администратор платформы"}.</p><nav aria-label="Основное меню"><ul>
    {session.scope === "tenant" && <li>Обращения</li>}
    {session.scope === "tenant" && session.role === "admin" && <li>Настройки компании</li>}
    {session.scope === "platform" && <li>Управление платформой</li>}
  </ul></nav></>;
  return <section><h2>{registering ? "Регистрация компании" : "Вход"}</h2>
    {error && <p role="alert">{error}</p>}
    <form onSubmit={submit}>
      {registering && <p><label>Название компании <input required autoComplete="organization" value={company} onChange={(e) => setCompany(e.target.value)} /></label></p>}
      <p><label>Электронная почта <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label></p>
      <p><label>Пароль <input type="password" required autoComplete={registering ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} /></label></p>
      <button type="submit" disabled={pending}>{pending ? "Подождите…" : registering ? "Зарегистрироваться" : "Войти"}</button>
    </form>
    <p><button type="button" disabled={pending} onClick={() => { setRegistering(!registering); setError(""); }}>{registering ? "Уже есть аккаунт? Войти" : "Создать компанию"}</button></p>
  </section>;
}
