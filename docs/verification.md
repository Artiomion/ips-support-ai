# Проверки и демонстрация

## Уже доступно

```bash
python3 scripts/check_project.py
python3 scripts/bootstrap_ai.py --check
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Первая команда проверяет документацию, роли, баланс задач, OpenAPI refs,
примеры и manifest. Вторая показывает prerequisites и наличие локальных
навыков. Эти проверки не запускают будущий продукт.

## Команды, которые обязаны появиться в A01/B01

Backend — выполнять из `backend/` после B01; до него команды не существуют:

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run ty check src
uv run pytest
uv run alembic upgrade head
uv run python -m ips.core.export_openapi --check ../contracts/openapi.json
```

A01/A02 создают команды из корня:

```bash
npm ci
npm run lint
npm run typecheck
npm run test -- --run
npm run api:check
npm run build
```

A08 добавляет `npm run test:e2e`, B08 — команду поднятия реального Compose
стенда и seed. В CI PostgreSQL service с той же major-версией, что локально;
не подменять SQLite, поскольку locks/FK/FTS и partial indexes отличаются.

Первый export OpenAPI сравнивает только реализованные операции: точные
request/response enums, required fields и статусы; незавершённые endpoint
допустимы до своих карточек. M4 проверяет все операции ядра, M5 — расширения.
Не допускать «вечно зелёного» compare только названий путей.
На стадии B01 реализованных `/api/v1/` операций ещё нет, поэтому команда
явно сообщает `Checked 0 implemented API operations`. Для этапных проверок
добавьте `--require-phase M4` или `--require-phase M5`: команда завершится
ошибкой, если в соответствующем объёме есть отсутствующие операции.

## Матрица обязательных поведенческих проверок

| Поведение | Ответственная карточка |
|---|---|
| Tenant подмена, роли, superadmin без доступа к сообщениям | B02, C02, B10 |
| Origin allowlist, HMAC TTL/nonce, widget scope | B03, A04 |
| Одна активная заявка, version conflict, state machine | B04 |
| Конкурентный claim и max_active | B05, реальный PostgreSQL |
| Internal articles/notes не попадают в клиентский контекст | C04/C06 |
| Handoff во время генерации блокирует assistant reply | C05 |
| Квоты и provider down сохраняют ручной handoff | B06, C05, A07 |
| Email token expiry/replay и conversation scope | B07 |
| AI outcome только после подтверждения | C07, B07 |
| Keyboard, Escape, возврат фокуса | A06 |
| Upgrade с предыдущего этапа, backup/restore | B08 |
| Worker lease, retry, retention | B09 |
| Вложения/парсеры/visibility в vector search | C09 |
| Staff assistant и draft не отправляются клиенту | C10, A09 |

Не вводить искусственный общий coverage 100%. Покрыть указанные границы и
инварианты; UI snapshot не доказывает права доступа. Fake LLM нужен для
детерминированных тестов, реальные ключи не нужны GitHub Actions.

## Сценарий защиты M4, около 10 минут

1. Поднять Compose с seed компаний «Альфа» и «Бета», публичными и внутренними
   статьями. Показать выбранный AI provider и его режим.
2. На demo-сайте Альфы открыть JS widget; спросить о возврате, увидеть
   ответ и citation опубликованной статьи. Подтвердить решение ИИ.
3. Создать новый dialogue, задать неизвестный вопрос и нажать передачу.
   Показать новую заявку и ожидающего клиента в той же переписке.
4. Двум специалистам попробовать claim; победитель один. Ответить человеку;
   показать, что internal note клиент не видит и ИИ самостоятельно не отвечает.
5. Подтвердить email через sandbox, открыть ссылку в другой browser context,
   продолжить dialogue. Переписка другого клиента недоступна.
6. Resolve, reopen, затем confirm и rating. Показать изменение метрик;
   незавершённый dialogue не считается подтверждённым решением ИИ.
7. Войти в Бету: данных Альфы нет. Попробовать чужой UUID → 404.
8. Отключить/эмулировать отказ провайдера: handoff и работа человека доступны.

Для истинного AI smoke отдельно сохранить дату, model, 3–5 вопросов и
результат; ключ не коммитить. Если ключа нет, честно отметить «интеграция
провайдера проверена stub-transport, живой вызов не выполнен».
