import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";

type S = components["schemas"];
export type AdminApi = {
  members: () => Promise<S["Member"][]>; widgets: () => Promise<S["Widget"][]>; categories: () => Promise<S["Category"][]>; sections: () => Promise<S["Section"][]>;
  createCategory: (name: string) => Promise<S["Category"]>;
  invite: (body: S["InviteInput"]) => Promise<S["InviteResult"]>; updateMember: (id: string, body: S["MemberUpdate"]) => Promise<S["Member"]>;
  createWidget: (body: S["WidgetInput"]) => Promise<S["Widget"]>; updateWidget: (id: string, body: S["WidgetInput"]) => Promise<S["Widget"]>;
};
const emptyWidget: S["WidgetInput"] = { name: "", color: "#2563eb", greeting: "Здравствуйте! Чем помочь?", allowed_origins: [], section_ids: [] };
const escapeAttribute = (value: string) => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
function MemberEditor({ member, categories, save }: { member: S["Member"]; categories: S["Category"][]; save: (id: string, body: S["MemberUpdate"]) => Promise<void> }) {
  const [value, setValue] = useState(member);
  useEffect(() => { setValue(member); }, [member]);
  return <li><form onSubmit={(e) => { e.preventDefault(); void save(member.id, { role: value.role, available: value.available, max_active: value.max_active, category_ids: value.category_ids }); }}><strong>{member.email}</strong>{" "}
    <label>Роль <select value={value.role} onChange={(e) => setValue({ ...value, role: e.target.value as S["Member"]["role"] })}><option value="specialist">Специалист</option><option value="admin">Администратор</option></select></label>{" "}
    <label><input type="checkbox" checked={value.available} onChange={(e) => setValue({ ...value, available: e.target.checked })} /> Доступен</label>{" "}
    <label>Максимум заявок <input type="number" min="1" value={value.max_active} onChange={(e) => setValue({ ...value, max_active: Number(e.target.value) })} /></label>
    <fieldset><legend>Специализация</legend>{categories.map((category) => <label key={category.id}><input type="checkbox" checked={value.category_ids.includes(category.id)} onChange={(e) => setValue({ ...value, category_ids: e.target.checked ? [...value.category_ids, category.id] : value.category_ids.filter((id) => id !== category.id) })} />{category.name} </label>)}</fieldset>
    <button type="submit">Сохранить сотрудника</button></form></li>;
}
export function Administration({ api, widgetOrigin, apiOrigin }: { api: AdminApi; widgetOrigin: string; apiOrigin: string }) {
  const [members, setMembers] = useState<S["Member"][]>([]); const [widgets, setWidgets] = useState<S["Widget"][]>([]);
  const [categories, setCategories] = useState<S["Category"][]>([]); const [sections, setSections] = useState<S["Section"][]>([]);
  const [loadError, setLoadError] = useState(""); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const [categoryName, setCategoryName] = useState(""); const [email, setEmail] = useState(""); const [role, setRole] = useState<S["InviteInput"]["role"]>("specialist");
  const [widget, setWidget] = useState<S["WidgetInput"]>(emptyWidget); const [origins, setOrigins] = useState(""); const [editing, setEditing] = useState<string | null>(null);
  const [scriptOrigin, setScriptOrigin] = useState(widgetOrigin); const [busy, setBusy] = useState(false);
  useEffect(() => { let active = true; Promise.allSettled([api.members(), api.widgets(), api.categories(), api.sections()]).then(([m, w, c, s]) => {
    if (!active) return; if (m.status === "fulfilled") setMembers(m.value); if (w.status === "fulfilled") setWidgets(w.value); if (c.status === "fulfilled") setCategories(c.value); if (s.status === "fulfilled") setSections(s.value);
    if ([m, w, c, s].some((item) => item.status === "rejected")) setLoadError("Часть настроек пока недоступна. Обновите страницу позже.");
  }); return () => { active = false; }; }, [api]);
  async function createCategory(event: FormEvent) { event.preventDefault(); if (busy || !categoryName.trim()) return; setBusy(true); setError(""); setNotice("");
    try { const category = await api.createCategory(categoryName.trim()); setCategories((old) => [...old, category]); setCategoryName(""); setNotice("Специализация создана."); } catch { setError("Не удалось создать специализацию."); } finally { setBusy(false); } }
  async function invite(event: FormEvent) { event.preventDefault(); if (busy) return; setBusy(true); setError(""); setNotice("");
    try { await api.invite({ email, role }); setEmail(""); setNotice("Приглашение поставлено в очередь на отправку."); } catch { setError("Не удалось создать приглашение."); } finally { setBusy(false); } }
  async function saveMember(id: string, body: S["MemberUpdate"]) { if (busy) return; setBusy(true); setError(""); setNotice("");
    try { const value = await api.updateMember(id, body); setMembers((items) => items.map((item) => item.id === id ? value : item)); setNotice("Настройки сотрудника сохранены."); } catch { setError("Не удалось сохранить сотрудника."); } finally { setBusy(false); } }
  async function saveWidget(event: FormEvent) { event.preventDefault(); if (busy) return; setBusy(true); setError(""); setNotice("");
    const body = { ...widget, allowed_origins: origins.split(/\r?\n/).map((value) => value.trim()).filter(Boolean) };
    try { const value = editing ? await api.updateWidget(editing, body) : await api.createWidget(body); setWidgets((items) => editing ? items.map((item) => item.id === editing ? value : item) : [...items, value]); setEditing(null); setWidget(emptyWidget); setOrigins(""); setNotice("Настройки виджета сохранены."); }
    catch (err) { setError(err instanceof ApiError && err.status === 422 ? "Проверьте данные, разрешённые адреса сайтов и доступность выбранных разделов. Адрес указывается полностью, например https://example.com." : "Не удалось сохранить виджет."); } finally { setBusy(false); } }
  function edit(value: S["Widget"]) { setEditing(value.id); setWidget({ name: value.name, color: value.color, greeting: value.greeting, allowed_origins: value.allowed_origins, section_ids: value.section_ids }); setOrigins(value.allowed_origins.join("\n")); setError(""); }
  function snippet(key: string) { try { const url = new URL("/loader.js", scriptOrigin); if (!["http:", "https:"].includes(url.protocol)) return ""; return `<script src="${escapeAttribute(url.href)}" data-widget-key="${escapeAttribute(key)}" data-api-origin="${escapeAttribute(apiOrigin)}"></script>`; } catch { return ""; } }
  return <section aria-label="Настройки компании"><h2>Настройки компании</h2>{loadError && <p role="status">{loadError}</p>}{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <h3>Специализации</h3><form onSubmit={(e) => { void createCategory(e); }}><label>Название специализации <input required value={categoryName} onChange={(e) => setCategoryName(e.target.value)} /></label> <button disabled={busy}>Добавить специализацию</button></form>
    <h3>Сотрудники</h3><ul>{members.map((member) => <MemberEditor key={member.id} member={member} categories={categories} save={saveMember} />)}</ul>
    <form onSubmit={(e) => { void invite(e); }}><label>Почта нового сотрудника <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>{" "}<label>Роль приглашённого <select value={role} onChange={(e) => setRole(e.target.value as S["InviteInput"]["role"])}><option value="specialist">Специалист</option><option value="admin">Администратор</option></select></label>{" "}<button disabled={busy}>Пригласить</button></form>
    <h3>Виджеты</h3><label>Адрес размещения виджета <input value={scriptOrigin} onChange={(e) => setScriptOrigin(e.target.value)} /></label>
    <ul>{widgets.map((item) => <li key={item.id}><strong>{item.name}</strong> <button type="button" onClick={() => edit(item)}>Изменить</button><p>Код для сайта:</p><textarea readOnly aria-label={`Код виджета ${item.name}`} value={snippet(item.public_key)} /></li>)}</ul>
    <h4>{editing ? "Изменить виджет" : "Новый виджет"}</h4><form onSubmit={(e) => { void saveWidget(e); }}>
      <p><label>Название виджета <input required value={widget.name} onChange={(e) => setWidget({ ...widget, name: e.target.value })} /></label></p>
      <p><label>Цвет <input type="color" value={widget.color} onChange={(e) => setWidget({ ...widget, color: e.target.value })} /></label></p>
      <p><label>Приветствие <input required value={widget.greeting} onChange={(e) => setWidget({ ...widget, greeting: e.target.value })} /></label></p>
      <p><label>Разрешённые адреса сайтов <textarea required value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder="https://example.com" /></label> По одному адресу на строку.</p>
      <fieldset><legend>Разделы базы знаний</legend>{sections.map((section) => <label key={section.id}><input type="checkbox" checked={widget.section_ids.includes(section.id)} onChange={(e) => setWidget({ ...widget, section_ids: e.target.checked ? [...widget.section_ids, section.id] : widget.section_ids.filter((id) => id !== section.id) })} />{section.name} </label>)}</fieldset>
      <button type="submit" disabled={busy}>{editing ? "Сохранить виджет" : "Создать виджет"}</button>{editing && <button type="button" onClick={() => { setEditing(null); setWidget(emptyWidget); setOrigins(""); }}>Отмена</button>}
    </form>
  </section>;
}
