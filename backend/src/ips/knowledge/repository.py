"""PostgreSQL persistence for tenant-scoped knowledge."""

from uuid import uuid4

from sqlalchemy import text


class KnowledgeRepository:
    def create_section(self, db, tenant_id, name):
        return (
            db.execute(
                text("""INSERT INTO article_sections (id,tenant_id,name)
            VALUES (:id,:tenant,:name) ON CONFLICT (tenant_id,name) DO NOTHING RETURNING id,name"""),
                {"id": uuid4(), "tenant": tenant_id, "name": name},
            )
            .mappings()
            .first()
        )

    def section_exists(self, db, tenant_id, section_id):
        return (
            db.execute(
                text("SELECT 1 FROM article_sections WHERE tenant_id=:tenant AND id=:id"),
                {"tenant": tenant_id, "id": section_id},
            ).first()
            is not None
        )

    def list_sections(self, db, tenant_id, after, limit):
        return (
            db.execute(
                text("""SELECT id,name FROM article_sections WHERE tenant_id=:tenant
            AND id>:after ORDER BY id LIMIT :limit"""),
                {"tenant": tenant_id, "after": after, "limit": limit},
            )
            .mappings()
            .all()
        )

    def create_article(
        self, db, tenant_id, actor_id, data, *, source_type="manual", source_id=None
    ):
        article_id, revision_id = uuid4(), uuid4()
        db.execute(
            text("""INSERT INTO articles (id,tenant_id,section_id,visibility,status)
            VALUES (:id,:tenant,:section,:visibility,:status)"""),
            {
                "id": article_id,
                "tenant": tenant_id,
                "section": data.section_id,
                "visibility": data.visibility,
                "status": data.status,
            },
        )
        db.execute(
            text("""INSERT INTO article_revisions
            (id,tenant_id,article_id,version,title,body,source_type,source_id,created_by)
            VALUES (:id,:tenant,:article,1,:title,:body,:source_type,:source_id,:actor)"""),
            {
                "id": revision_id,
                "tenant": tenant_id,
                "article": article_id,
                "title": data.title,
                "body": data.body,
                "source_type": source_type,
                "source_id": source_id,
                "actor": actor_id,
            },
        )
        db.execute(
            text(
                "UPDATE articles SET current_revision_id=:revision WHERE tenant_id=:tenant AND id=:id"
            ),
            {"revision": revision_id, "tenant": tenant_id, "id": article_id},
        )
        return self.get_article(db, tenant_id, article_id)

    def get_article(self, db, tenant_id, article_id, *, lock=False):
        query = """SELECT a.id,a.tenant_id,a.section_id,a.visibility,a.status,
            r.version AS revision,r.title,r.body
            FROM articles a JOIN article_revisions r ON r.tenant_id=a.tenant_id
              AND r.article_id=a.id AND r.id=a.current_revision_id
            WHERE a.tenant_id=:tenant AND a.id=:id"""
        if lock:
            query += " FOR UPDATE OF a"
        return db.execute(text(query), {"tenant": tenant_id, "id": article_id}).mappings().first()

    def lock_article(self, db, tenant_id, article_id):
        return self.get_article(db, tenant_id, article_id, lock=True)

    def save_revision(self, db, article, changes, actor_id, source_type):
        # The row lock acquired by the service serializes revision creation.
        values = {**dict(article), **changes}
        revision_id = uuid4()
        db.execute(
            text("""INSERT INTO article_revisions
            (id,tenant_id,article_id,version,title,body,source_type,created_by)
            VALUES (:id,:tenant,:article,:version,:title,:body,:source_type,:actor)"""),
            {
                "id": revision_id,
                "tenant": article["tenant_id"],
                "article": article["id"],
                "version": article["revision"] + 1,
                "title": values["title"],
                "body": values["body"],
                "source_type": source_type,
                "actor": actor_id,
            },
        )
        db.execute(
            text("""UPDATE articles SET current_revision_id=:revision,
            visibility=:visibility,status=:status WHERE tenant_id=:tenant AND id=:id"""),
            {
                "revision": revision_id,
                "visibility": values["visibility"],
                "status": values["status"],
                "tenant": article["tenant_id"],
                "id": article["id"],
            },
        )
        return self.get_article(db, article["tenant_id"], article["id"])

    def get_revision(self, db, tenant_id, article_id, revision):
        return (
            db.execute(
                text("""SELECT version AS revision,title,body,created_at,source_type
            FROM article_revisions WHERE tenant_id=:tenant AND article_id=:article AND version=:revision"""),
                {"tenant": tenant_id, "article": article_id, "revision": revision},
            )
            .mappings()
            .first()
        )

    def list_revisions(self, db, tenant_id, article_id, after, limit):
        return (
            db.execute(
                text("""SELECT version AS revision,title,body,created_at,source_type
            FROM article_revisions WHERE tenant_id=:tenant AND article_id=:article AND version>:after
            ORDER BY version LIMIT :limit"""),
                {
                    "tenant": tenant_id,
                    "article": article_id,
                    "after": after,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )

    def list_articles(self, db, tenant_id, after, limit, query=None):
        return (
            db.execute(
                text("""SELECT a.id,a.tenant_id,a.section_id,a.visibility,a.status,
            r.version AS revision,r.title,r.body FROM articles a
            JOIN article_revisions r ON r.tenant_id=a.tenant_id AND r.article_id=a.id
              AND r.id=a.current_revision_id WHERE a.tenant_id=:tenant AND a.id>:after
              AND (CAST(:query AS text) IS NULL OR strpos(lower(r.title || ' ' || r.body), lower(:query))>0)
            ORDER BY a.id LIMIT :limit"""),
                {
                    "tenant": tenant_id,
                    "after": after,
                    "limit": limit,
                    "query": query,
                },
            )
            .mappings()
            .all()
        )

    def public_candidates(self, db, tenant_id, section_ids, query, limit=5):
        if not section_ids:
            return []
        return (
            db.execute(
                text("""SELECT a.id AS article_id,r.version AS revision,r.title,r.body AS text
            FROM articles a JOIN article_revisions r ON r.tenant_id=a.tenant_id
              AND r.article_id=a.id AND r.id=a.current_revision_id
            WHERE a.tenant_id=:tenant AND a.section_id=ANY(:sections)
              AND a.visibility='public' AND a.status='published'
              AND strpos(lower(r.title || ' ' || r.body), lower(:query))>0
            ORDER BY a.id LIMIT :limit"""),
                {
                    "tenant": tenant_id,
                    "sections": section_ids,
                    "query": query,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )

    def save_import(self, db, tenant_id, section_id, import_id, state, error_code, article_id=None):
        return (
            db.execute(
                text("""INSERT INTO knowledge_imports
            (id,tenant_id,section_id,state,error_code,article_id)
            VALUES (:id,:tenant,:section,:state,:error,:article) RETURNING id,state,error_code"""),
                {
                    "id": import_id,
                    "tenant": tenant_id,
                    "section": section_id,
                    "state": state,
                    "error": error_code,
                    "article": article_id,
                },
            )
            .mappings()
            .one()
        )

    def get_import(self, db, tenant_id, import_id):
        return (
            db.execute(
                text("""SELECT id,state,error_code FROM knowledge_imports
            WHERE tenant_id=:tenant AND id=:id"""),
                {"tenant": tenant_id, "id": import_id},
            )
            .mappings()
            .first()
        )
