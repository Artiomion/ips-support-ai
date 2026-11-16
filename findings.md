# Findings: подготовка ИПС

- Исходное ТЗ сохранено без изменений в docs/product-spec.md.
- Пользователь явно выбрал Python backend; сроки не задал; поручил создать GitHub.
- Исходная папка: пустой Git, без commits и remote.
- Выбран план FastAPI/SQLAlchemy/PostgreSQL + React/TypeScript; единый модульный
  backend. Это исходное решение автора плана, не обязательное требование ТЗ.
- 30 карточек: по 10/40 баллов на роль, из них 32 ядро и 8 расширение.
  Нагрузка выравнивается по фактическому времени после каждого этапа.
- GitHub и Context7 доступны в текущем клиенте; это не установка у коллег.
- Официальные навыки OpenAI закреплены commit/hash в .ai/tooling.json;
  default только playwright и security-best-practices. Установка проверена
  во временной папке, глобальные настройки не менялись.
- Независимый builder check выявил 7 групп пробелов: ранний AI runner,
  public ticket/session reads, platform session, порты, циклы зависимостей,
  M5 DTO/endpoints. Исправления повторно проверены reviewer: блокеров не осталось.
- Готовность к production не заявляется; отсрочки исходного ТЗ явно указаны
  в docs/requirements.md и docs/risks.md.

## Проверенные источники

- https://github.com/openai/skills — навыки и точный upstream commit.
- https://fastapi.tiangolo.com/advanced/testing-dependencies/ — test overrides;
  также выполнен запрос Context7 по FastAPI.
- https://docs.sqlalchemy.org/en/20/orm/session_basics.html — Session/transaction.
- https://alembic.sqlalchemy.org/en/latest/branches.html — merge migration heads.
- https://docs.astral.sh/uv/guides/projects/ — project/lock workflow.
- https://nodejs.org/en/about/previous-releases — поддерживаемая ветка Node 24.
- https://github.com/upstash/context7/tree/master/docs/clients — setup по клиентам.
