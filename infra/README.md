# Локальный стенд

Из корня проекта запустите API, панель, виджет, PostgreSQL 17 и Mailpit.
API применяет миграции Alembic перед запуском.

```sh
docker compose -p ips-demo -f infra/compose.yaml up -d --build --wait
```

| Сервис | Адрес |
|---|---|
| Панель | http://localhost:5173 |
| Виджет и `loader.js` | http://localhost:5174 |
| Готовность API | http://localhost:8000/health/ready |
| Почтовая песочница | http://localhost:8025 |

По умолчанию включён демонстрационный ИИ. Адреса панели и виджета должны
совпадать с настройками CORS. При открытии стенда используйте `localhost`.
Данные PostgreSQL остаются в Docker volume между запусками.

Для разработки с локальным API поднимите только сервисы данных.

```sh
docker compose -p ips-demo -f infra/compose.yaml up -d --wait postgres mailpit
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn ips.main:app --reload
```

Команды для заполнения базы, сброса, локальной отправки письма и проверки
резервной копии приведены в [инструкции для команды](../docs/runbook.md).
