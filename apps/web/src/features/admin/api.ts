import { createApiClient } from "../../../../../packages/api-client/src/client";
import type { AdminApi } from "../admin/Administration";
import type { KnowledgeApi } from "../knowledge/Knowledge";

type Page<T> = { items: T[]; next_cursor: string | null };
function required<T>(value: T | undefined): T { if (value === undefined) throw new Error("Пустой ответ сервера"); return value; }
async function all<T>(load: (cursor?: string) => Promise<Page<T>>): Promise<T[]> {
  const result: T[] = []; const seen = new Set<string>(); let cursor: string | undefined;
  do { const page = await load(cursor); result.push(...page.items); cursor = page.next_cursor || undefined;
    if (cursor && seen.has(cursor)) throw new Error("Повтор курсора"); if (cursor) seen.add(cursor);
  } while (cursor); return result;
}
export function createAdminApis(baseUrl: string, csrfToken: string): { admin: AdminApi; knowledge: KnowledgeApi } {
  const client = createApiClient({ baseUrl, csrfToken }); const header = { "X-CSRF-Token": csrfToken };
  const query = (cursor?: string) => ({ limit: 100, ...(cursor ? { cursor } : {}) });
  const sections = () => all(async (cursor) => required((await client.get("/api/v1/knowledge/sections", { params: { query: query(cursor) } })).data));
  return {
    admin: {
      members: () => all(async (cursor) => required((await client.get("/api/v1/members", { params: { query: query(cursor) } })).data)),
      widgets: () => all(async (cursor) => required((await client.get("/api/v1/widgets", { params: { query: query(cursor) } })).data)),
      categories: () => all(async (cursor) => required((await client.get("/api/v1/categories", { params: { query: query(cursor) } })).data)), sections,
      async createCategory(name) { return required((await client.post("/api/v1/categories", { params: { header }, body: { name } })).data); },
      async invite(body) { return required((await client.post("/api/v1/members/invitations", { params: { header }, body })).data); },
      async updateMember(id, body) { return required((await client.patch("/api/v1/members/{member_id}", { params: { header, path: { member_id: id } }, body })).data); },
      async createWidget(body) { return required((await client.post("/api/v1/widgets", { params: { header }, body })).data); },
      async updateWidget(id, body) { return required((await client.put("/api/v1/widgets/{widget_id}", { params: { header, path: { widget_id: id } }, body })).data); },
    },
    knowledge: {
      sections,
      async createSection(name) { return required((await client.post("/api/v1/knowledge/sections", { params: { header }, body: { name } })).data); },
      articles: () => all(async (cursor) => required((await client.get("/api/v1/knowledge/articles", { params: { query: query(cursor) } })).data)),
      async get(id) { return required((await client.get("/api/v1/knowledge/articles/{article_id}", { params: { path: { article_id: id } } })).data); },
      async create(body) { return required((await client.post("/api/v1/knowledge/articles", { params: { header }, body })).data); },
      async update(id, body) { return required((await client.patch("/api/v1/knowledge/articles/{article_id}", { params: { header, path: { article_id: id } }, body })).data); },
      revisions: (id) => all(async (cursor) => required((await client.get("/api/v1/knowledge/articles/{article_id}/revisions", { params: { path: { article_id: id }, query: query(cursor) } })).data)),
      async restore(id, revision) { return required((await client.post("/api/v1/knowledge/articles/{article_id}/restore/{revision_id}", { params: { header, path: { article_id: id, revision_id: revision } } })).data); },
      async importFile(file, sectionId, visibility) {
        const form = new FormData(); form.append("file", file); form.append("section_id", sectionId); form.append("visibility", visibility);
        return required((await client.post("/api/v1/knowledge/imports", { params: { header }, body: { file: file.name, section_id: sectionId, visibility }, bodySerializer: () => form })).data);
      },
      async importStatus(id) { return required((await client.get("/api/v1/knowledge/imports/{import_id}", { params: { path: { import_id: id } } })).data); },
    },
  };
}
