# Дорожка B: Платформа, заявки и инфраструктура

> Для исполнителя: выполняй по одной карточке обычной кодовой моделью.
> При подключённом Superpowers можно использовать executing-plans;
> подагенты не требуются. Исходный дизайн: `docs/architecture.md`.

**Цель:** независимый поток с работающими проверяемыми результатами.
**Архитектура:** один backend с портами B/C и отдельные web/widget через HTTP.
**Стек:** Python/FastAPI/PostgreSQL; React/TypeScript/Vite.
**Общие ограничения:** `AGENTS.md`, `docs/contracts.md`, `docs/scope.md`.

**Review focus:** другой tenant; внутренние знания; повтор запроса;
гонки назначения/ИИ; сбой провайдера. Проверки распределены по карточкам.

**Нагрузка:** 32 балла ядра + 8 расширения = 40. M0/M1/M2/M3–M4/M5
дают каждому по 8 баллов. Баллы включают тесты, review и интеграцию.

## Общий цикл каждой карточки

- [ ] Открой только входы карточки; зафиксируй разрешённые файлы и потребителей.
- [ ] Напиши указанную поведенческую проверку. Для scaffold — smoke/build.
- [ ] Для логики убедись, что проверка падает по ожидаемой причине.
- [ ] Реализуй указанное поведение и output без изменения чужого контракта.
- [ ] Выполни указанную проверку и команды своей части из verification.
- [ ] Проверь diff, открой маленький PR с ID, фактами проверок и ограничениями.

Если карточка велика, её PR можно разбить на несколько частей. У каждой части
собственный результат; все части вместе сохраняют исходный балл карточки.
Моки снимают ожидание реализации соседа, но сквозной M4 требует настоящих портов.

## B01 — Backend, инфраструктура, базовая схема и порты

**Этап:** M0. **Баллы:** 3. **Владелец:** B.

**Создать/изменить:** backend/{pyproject.toml,uv.lock,.python-version,src/ips/main.py,src/ips/core/,alembic/}; infra/compose.yaml; .github/workflows/backend.yml.

**Входы / зависимости:** Нет. Контракты в репозитории. C01 делает свои файлы независимо.

**Выход / интерфейсы:** create_app(settings, ports); Session dependency; базовая миграция b0001; /health/live, /health/ready; config, errors, ports; core.jobs enqueue/drain_once с idempotency key для ранних писем; export/compare OpenAPI. Документировать команды в verification.

**Критерий приёмки:** tests/core/test_bootstrap.py: health live=200, ready при DB down=503; alembic upgrade head на пустой PostgreSQL; безопасная ошибка без stacktrace. CI uv sync --frozen и PostgreSQL, а не SQLite.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B02 — Компании, пользователи, роли и staff sessions

**Этап:** M0. **Баллы:** 5. **Владелец:** B.

**Создать/изменить:** backend/src/ips/platform/{models.py,auth.py,schemas.py,router.py}; backend/tests/platform/test_auth.py; backend/alembic/versions/b_*.

**Входы / зависимости:** B01; StaffSession/OpenAPI. C02 может использовать fake contexts до merge.

**Выход / интерфейсы:** Register/login/logout/me; Argon2; token hashes; TenantContext guard; csrf+Origin; StaffSession union по scope tenant/platform, superadmin без tenant membership и без доступа к данным компаний.

**Критерий приёмки:** test_auth.py: пароль не plain; неверная роль=403; tenant подмена=404; logout отзывает token; login/register origin validation; superadmin не проходит staff tenant guard; два tenants.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B03 — Сотрудники, виджеты и customer identity

**Этап:** M1. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/platform/{members.py,widgets.py,customers.py,identity.py}; backend/tests/platform/test_identity.py; backend/alembic/versions/b_*.

**Входы / зависимости:** B02; createPublicSession/getPublicSession, Member/Widget DTO. Сначала widgets без section bindings; после C03 отдельная миграция связей widget_sections, без блокировки остальных операций.

**Выход / интерфейсы:** Invites и SMTP adapter через jobs.enqueue/drain_once B01; members/categories; widgets/domains; anon bearer; HMAC confirmed identity; GET session metadata. Для section_ids до C03 используется пустой список; общий gate M2 требует реальные связи.

**Критерий приёмки:** test_identity.py: HMAC TTL 5min и nonce replay; spoofed external_id не принимается; host suffix attack отвергнут; разные widgets/tenants изолированы; public key не позволяет получить старую историю.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B04 — Заявки, handoff и статусы

**Этап:** M1. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/tickets/{models.py,schemas.py,service.py,router.py}; backend/tests/tickets/test_transitions.py; backend/alembic/versions/b_*.

**Входы / зависимости:** B02; TicketPort, fake ConversationPort; настоящая C реализация подключается позже.

**Выход / интерфейсы:** TicketPort.escalate/on_customer_message/has_active/record_staff_reply; list/get/update/confirm/reopen и getCustomerTicket; статусы по contracts; version checks; mode=human в общей транзакции.

**Критерий приёмки:** test_transitions.py: повтор handoff один active ticket; illegal transition=409; чужой conversation=404; reopen resolved не создаёт второй active; closed handoff создаёт новый ticket; internal note не идёт в public DTO.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B05 — Атомарное назначение и простая маршрутизация

**Этап:** M2. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/tickets/{assignment.py,routing.py}; backend/tests/tickets/test_concurrency.py.

**Входы / зависимости:** B03/B04; availability, categories, max_active.

**Выход / интерфейсы:** Claim, reassignment admin/assignee; выбор доступного по специализации и минимальной нагрузке; без кандидата очередь.

**Критерий приёмки:** test_concurrency.py на двух независимых PostgreSQL connections: два claim — один успех/один 409; два tickets при max_active=1 не назначаются одному; недоступного не выбирает; порядок locks стабилен.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B06 — Операционные настройки, audit и квоты

**Этап:** M2. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/core/{usage.py,rate_limit.py}; backend/src/ips/platform/{settings.py,audit.py}; backend/tests/platform/test_limits.py.

**Входы / зависимости:** B02; UsageBudget port C04, OrganizationSettings.

**Выход / интерфейсы:** Серверные лимиты staff/widgets/AI; atomic reserve/settle; audit важных действий без текста/private tokens; HTTP spam limiter; getStaffCapabilities/getCustomerCapabilities из реально подключённых функций, не из роли UI.

**Критерий приёмки:** test_limits.py: два reserve на последний слот — один проходит; failure освобождает резерв по правилу; повтор settle не удваивает; expired reservation подбирается; audit не содержит ключ/пароль/письмо.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B07 — Email continuation, ratings и базовая аналитика

**Этап:** M3. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/platform/{email_links.py,notifications.py}; backend/src/ips/tickets/{ratings.py,analytics.py}; backend/tests/platform/test_links.py.

**Входы / зависимости:** B03/B04; ConversationPort scope и metrics; StaffReplyEvent/NotificationPort; C07 outcome counters; SMTP sandbox.

**Выход / интерфейсы:** Request/confirm/exchange одноразовых ссылок; NotificationPort.enqueue_staff_reply в транзакции сообщения и idempotent SMTP job; rating; widget-filtered overview. В ядре jobs можно исполнять drain_once, постоянный worker в B09.

**Критерий приёмки:** test_links.py: token hash+30min TTL, второй exchange отклонён, link scope не даёт другой conversation; score 1..5; metrics считают ai_confirmed только по подтверждению; нулевой знаменатель безопасен.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B08 — Интеграция backend и воспроизводимый запуск

**Этап:** M4. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** infra/{compose.yaml,Dockerfile.api,README.md}; backend/tests/integration/; docs/runbook.md; .github/workflows/backend.yml.

**Входы / зависимости:** B01–B07 и интерфейсы C02–C07. Не ждёт C08 или A08: стенд нужен для их тестов; приёмка всех трёх вместе — gate M4.

**Выход / интерфейсы:** Composition root связывает порты; один Alembic head; seed двух companies; команды поднять/сбросить тестовый стенд; backup/restore smoke.

**Критерий приёмки:** tests/integration/test_core.py: реальный handoff меняет mode и ticket атомарно; API restart сохраняет данные; fresh migration+upgrade с M3; backup восстанавливает обе компании. Все backend quality gates проходят.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B09 — SLA, jobs, авто-закрытие и retention

**Этап:** M5. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/core/{worker.py,jobs.py}; backend/src/ips/tickets/{sla.py,auto_close.py}; backend/src/ips/platform/retention.py; backend/tests/jobs/.

**Входы / зависимости:** B08; persisted jobs из B07; injected clock, UTC.

**Выход / интерфейсы:** Постоянный DB-backed worker поверх enqueue/drain_once B01 с lease/retry/idempotency; get/updateSupportPolicy с IANA timezone и weekly windows; SLA; авто-close resolved; retention.

**Критерий приёмки:** tests/jobs/test_worker.py: два workers не выполняют одну lease; crash/retry не дублирует email; auto-close только после срока; retention удаляет attachments/knowledge links безопасно и tenant-scoped; DST проверяется через timezone conversion.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## B10 — Платформенные лимиты, SLA analytics, инциденты

**Этап:** M5. **Баллы:** 4. **Владелец:** B.

**Создать/изменить:** backend/src/ips/platform/{superadmin.py,plans.py}; backend/src/ips/tickets/{incidents.py,analytics.py}; backend/tests/platform/test_superadmin.py.

**Входы / зависимости:** B08/B09; platform/limits/SLA/incident contracts. Не зависит от C10; complaint UI читает C endpoint независимо.

**Выход / интерфейсы:** Агрегаты платформы; лимиты тарифов без оплаты; SLA overview; candidate incidents по повторяющейся категории/временному окну (heuristic, без обещания ML).

**Критерий приёмки:** test_superadmin.py: superadmin видит counts и не messages; admin своей компании не управляет другой; max_staff/widgets нельзя обойти параллельными create; incidents scoped to tenant.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.
