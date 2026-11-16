# Backend: B и C

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL.
- `src/ips/platform/` и `tickets/` — B; `conversations/`, `ai/`, `knowledge/` — C.
- Общие `core/`, `main.py`, `pyproject.toml`, `uv.lock` и конфигурация Alembic — B.
  Порты `core/ports.py` утверждаются B+C; DTO HTTP — по OpenAPI.
- SQLAlchemy sync Session на запрос; не делить Session между потоками.
  Сетевой вызов LLM выполняется вне БД-транзакции; короткая транзакция
  повторно проверяет bot/human mode перед записью ответа.
- Никаких циклических импортов доменных сервисов. Сборка зависимостей — main.py.
- Каждый владелец добавляет свою миграцию с уникальным revision ID;
  не редактировать чужую или уже применённую. B сводит heads перед merge.
- Все ID, tenant-фильтры и приватность проверяются в реальной PostgreSQL.
- До B01 backend-команд ещё нет: см. `docs/verification.md`.
