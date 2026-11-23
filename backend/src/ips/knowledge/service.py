"""Knowledge behavior; the caller controls the transaction."""

from fastapi import HTTPException


class KnowledgeService:
    def __init__(self, repository):
        self.repository = repository

    def _article(self, uow, tenant_id, article_id):
        article = self.repository.lock_article(uow, tenant_id, article_id)
        if article is None:
            raise HTTPException(404, "Article unavailable")
        return article

    def update(self, uow, tenant_id, actor_id, article_id, expected_revision, changes):
        article = self._article(uow, tenant_id, article_id)
        if article["revision"] != expected_revision:
            raise HTTPException(409, "Article changed")
        if not changes or set(changes) - {"title", "body", "visibility", "status"}:
            raise HTTPException(422, "Invalid update")
        return self.repository.save_revision(uow, article, changes, actor_id, "manual")

    def restore(self, uow, tenant_id, actor_id, article_id, revision):
        article = self._article(uow, tenant_id, article_id)
        previous = self.repository.get_revision(uow, tenant_id, article_id, revision)
        if previous is None:
            raise HTTPException(404, "Revision unavailable")
        return self.repository.save_revision(
            uow,
            article,
            {"title": previous["title"], "body": previous["body"], "status": "draft"},
            actor_id,
            "restore",
        )
