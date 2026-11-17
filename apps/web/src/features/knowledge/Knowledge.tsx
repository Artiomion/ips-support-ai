import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../../../../../packages/api-client/src/client";
import type { components } from "../../../../../packages/api-client/src/generated";

type S = components["schemas"];
export type KnowledgeApi = {
  sections: () => Promise<S["Section"][]>; createSection: (name: string) => Promise<S["Section"]>;
  articles: () => Promise<S["Article"][]>; get: (id: string) => Promise<S["Article"]>; create: (body: S["ArticleInput"]) => Promise<S["Article"]>;
  update: (id: string, body: S["ArticleUpdate"]) => Promise<S["Article"]>; revisions: (id: string) => Promise<S["ArticleRevision"][]>; restore: (id: string, revision: number) => Promise<S["Article"]>;
  importFile: (file: File, section: string, visibility: S["ArticleInput"]["visibility"]) => Promise<S["ImportResult"]>; importStatus: (id: string) => Promise<S["ImportResult"]>;
};
const blank: S["ArticleInput"] = { section_id: "", title: "", body: "", visibility: "public", status: "draft" };
const labels: Record<S["Article"]["status"], string> = { draft: "Черновик", published: "Опубликована", disabled: "Отключена" };
function toForm(article: S["Article"]): S["ArticleInput"] { return { section_id: article.section_id, title: article.title, body: article.body, visibility: article.visibility, status: article.status }; }
export function Knowledge({ api }: { api: KnowledgeApi }) {
  const [sections, setSections] = useState<S["Section"][]>([]); const [articles, setArticles] = useState<S["Article"][]>([]);
  const [sectionName, setSectionName] = useState(""); const [current, setCurrent] = useState<S["Article"] | null>(null);
  const [form, setForm] = useState<S["ArticleInput"]>(blank); const [revisions, setRevisions] = useState<S["ArticleRevision"][]>([]);
  const [error, setError] = useState(""); const [notice, setNotice] = useState(""); const [busy, setBusy] = useState(false); const [conflict, setConflict] = useState(false);
  const [file, setFile] = useState<File | null>(null); const [importSection, setImportSection] = useState(""); const [importVisibility, setImportVisibility] = useState<S["ArticleInput"]["visibility"]>("public");
  const [importResult, setImportResult] = useState<S["ImportResult"] | null>(null); const [importError, setImportError] = useState("");
  useEffect(() => { let active = true; Promise.all([api.sections(), api.articles()]).then(([s, a]) => { if (active) { setSections(s); setArticles(a); } }).catch(() => { if (active) setError("База знаний пока недоступна."); }); return () => { active = false; }; }, [api]);
  useEffect(() => {
    if (!importResult || !["queued", "processing"].includes(importResult.state) || importError) return;
    let active = true; const timer = setInterval(() => { void api.importStatus(importResult.id).then((value) => { if (active) setImportResult(value); }).catch(() => { if (active) setImportError("Не удалось узнать состояние импорта."); }); }, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [api, importResult, importError]);
  useEffect(() => {
    if (importResult?.state !== "ready") return;
    let active = true; api.articles().then((items) => { if (active) setArticles(items); }).catch(() => { if (active) setImportError("Импорт завершён, но не удалось обновить список статей."); });
    return () => { active = false; };
  }, [api, importResult?.id, importResult?.state]);
  async function choose(article: S["Article"]) { if (busy) return; setBusy(true); setCurrent(article); setForm(toForm(article)); setConflict(false); setError(""); setRevisions([]); try { setRevisions(await api.revisions(article.id)); } catch { setError("Не удалось загрузить историю версий."); } finally { setBusy(false); } }
  async function sectionSubmit(event: FormEvent) { event.preventDefault(); if (busy || !sectionName.trim()) return; setBusy(true); setError(""); setNotice("");
    try { const section = await api.createSection(sectionName.trim()); setSections((old) => [...old, section]); setSectionName(""); setNotice("Раздел создан."); } catch { setError("Не удалось создать раздел."); } finally { setBusy(false); } }
  async function save(event: FormEvent) { event.preventDefault(); if (busy) return; setBusy(true); setError(""); setNotice("");
    try { const result = current ? await api.update(current.id, { expected_revision: current.revision, title: form.title, body: form.body, visibility: form.visibility, status: form.status }) : await api.create(form);
      setArticles((old) => current ? old.map((item) => item.id === result.id ? result : item) : [...old, result]); setCurrent(result); setForm(toForm(result)); setConflict(false); setNotice("Статья сохранена.");
    } catch (err) { if (err instanceof ApiError && err.status === 409) { setConflict(true); setError("Статья изменилась у другого пользователя. Ваши правки сохранены в форме; загрузите актуальную версию перед повторным сохранением."); } else setError("Не удалось сохранить статью."); }
    finally { setBusy(false); } }
  async function reload() { if (!current) return; setBusy(true); setError(""); try { const latest = await api.get(current.id); setCurrent(latest); setForm(toForm(latest)); setArticles((old) => old.map((item) => item.id === latest.id ? latest : item)); setConflict(false); } catch { setError("Не удалось получить актуальную версию."); } finally { setBusy(false); } }
  async function restore(revision: number) { if (!current || busy) return; setBusy(true); setError(""); setNotice(""); try { const result = await api.restore(current.id, revision); setCurrent(result); setForm(toForm(result)); setArticles((old) => old.map((item) => item.id === result.id ? result : item)); setNotice("Версия восстановлена."); try { setRevisions(await api.revisions(result.id)); } catch { setError("Версия восстановлена, но историю обновить не удалось."); } } catch { setError("Не удалось восстановить версию."); } finally { setBusy(false); } }
  async function upload(event: FormEvent) { event.preventDefault(); if (!file || !importSection || busy) return; setImportError(""); setImportResult(null);
    if (!file.name.toLowerCase().endsWith(".txt") || file.size > 2 * 1024 * 1024) { setImportError("Выберите TXT-файл размером до 2 МиБ."); return; }
    setBusy(true); try { setImportResult(await api.importFile(file, importSection, importVisibility)); } catch { setImportError("Не удалось загрузить файл."); } finally { setBusy(false); } }
  return <section aria-label="База знаний"><h2>База знаний</h2>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <h3>Разделы</h3><ul>{sections.map((section) => <li key={section.id}>{section.name}</li>)}</ul><form onSubmit={(e) => { void sectionSubmit(e); }}><label>Новый раздел <input required value={sectionName} onChange={(e) => setSectionName(e.target.value)} /></label><button disabled={busy}>Добавить раздел</button></form>
    <h3>Статьи</h3><button type="button" disabled={busy} onClick={() => { setCurrent(null); setForm(blank); setRevisions([]); setConflict(false); setError(""); }}>Новая статья</button>
    <ul>{articles.map((article) => <li key={article.id}><button type="button" disabled={busy} onClick={() => { void choose(article); }}>{article.title}</button> <strong>{labels[article.status]}</strong> · {article.visibility === "internal" ? "Внутренняя" : "Публичная"}</li>)}</ul>
    <form onSubmit={(e) => { void save(e); }}><h4>{current ? `Редактирование, версия ${current.revision}` : "Новая статья"}</h4>
      <p><label>Раздел статьи <select required disabled={!!current} value={form.section_id} onChange={(e) => setForm({ ...form, section_id: e.target.value })}><option value="">Выберите раздел</option>{sections.map((section) => <option key={section.id} value={section.id}>{section.name}</option>)}</select></label></p>
      <p><label>Название статьи <input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label></p>
      <p><label>Текст статьи <textarea required value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></label></p>
      <label>Доступ <select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value as S["ArticleInput"]["visibility"] })}><option value="public">Публичная</option><option value="internal">Внутренняя</option></select></label>{" "}
      <label>Состояние статьи <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value as S["ArticleInput"]["status"] })}><option value="draft">Черновик</option><option value="published">Опубликована</option><option value="disabled">Отключена</option></select></label>{" "}
      <button disabled={busy || conflict}>Сохранить статью</button>{conflict && <button type="button" disabled={busy} onClick={() => { void reload(); }}>Заменить мои правки актуальной версией</button>}
    </form>
    {current && <section><h4>История версий</h4>{revisions.length === 0 && <p>Нет загруженных версий.</p>}<ul>{revisions.map((revision) => <li key={revision.revision}>Версия {revision.revision}: {revision.title} <button type="button" disabled={busy} onClick={() => { void restore(revision.revision); }}>Восстановить версию {revision.revision}</button></li>)}</ul></section>}
    <h3>Импорт TXT</h3>{importError && <p role="alert">{importError} {importResult && ["queued", "processing"].includes(importResult.state) && <button type="button" onClick={() => setImportError("")}>Повторить проверку</button>}</p>}{importResult && <p role="status">Импорт: {{ queued: "в очереди", processing: "обрабатывается", ready: "готово", failed: "ошибка" }[importResult.state]}{importResult.error_code ? ` (${importResult.error_code})` : ""}</p>}
    <form onSubmit={(e) => { void upload(e); }}><label>TXT-файл <input type="file" accept=".txt,text/plain" required onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>{" "}<label>Раздел импорта <select required value={importSection} onChange={(e) => setImportSection(e.target.value)}><option value="">Выберите раздел</option>{sections.map((section) => <option key={section.id} value={section.id}>{section.name}</option>)}</select></label>{" "}<label>Доступ импорта <select value={importVisibility} onChange={(e) => setImportVisibility(e.target.value as S["ArticleInput"]["visibility"])}><option value="public">Публичная</option><option value="internal">Внутренняя</option></select></label>{" "}<button disabled={busy || importResult?.state === "queued" || importResult?.state === "processing"}>Загрузить TXT</button></form>
  </section>;
}
