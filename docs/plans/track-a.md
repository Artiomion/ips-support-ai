# Дорожка A: Интерфейсы и клиентский опыт

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

## A01 — Каркас web/widget и общий UI

**Этап:** M0. **Баллы:** 3. **Владелец:** A.

**Создать/изменить:** package.json; package-lock.json; apps/web/{package.json,src/main.tsx,src/App.tsx}; apps/widget/{package.json,src/main.tsx}; .github/workflows/frontend.yml.

**Входы / зависимости:** Нет; структура и contracts/openapi.json уже доступны.

**Выход / интерфейсы:** Два запускаемых Vite приложения; npm workspaces apps/* и packages/*; scripts dev:web, dev:widget, test, typecheck, lint, build.

**Критерий приёмки:** apps/web/src/App.test.tsx::renders_app_shell: выводится оболочка; build обоих приложений успешен. Без backend и login mock в bundle production.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A02 — Типизированный клиент, MSW и контрактные фикстуры

**Этап:** M0. **Баллы:** 5. **Владелец:** A.

**Создать/изменить:** packages/api-client/{package.json,src/generated.ts,src/client.ts}; apps/web/src/mocks/; apps/widget/src/mocks/; contracts/examples/.

**Входы / зависимости:** A01; OpenAPI. Не ждёт B01/C01.

**Выход / интерфейсы:** createApiClient({baseUrl,csrfToken?,customerToken?}); get/post/put/patch обёртки с типами; npm run api:generate и api:check; сценарии bot, waiting, human, timeout.

**Критерий приёмки:** packages/api-client/src/client.test.ts: 401 не превращается в успех; CSRF только staff mutations; bearer не уходит на чужой origin; MSW public-message без internal_note; api:check выявляет drift.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A03 — Регистрация, вход, оболочки ролей

**Этап:** M1. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/web/src/features/auth/; apps/web/src/routes.tsx.

**Входы / зависимости:** A02; register/login/getStaffSession. На MSW до B02.

**Выход / интерфейсы:** Экраны регистрации/входа, сохранение CSRF в памяти; разные меню admin/specialist/superadmin.

**Критерий приёмки:** auth.test.tsx: 401 возвращает форму; specialist не видит admin controls; пользователь не считается вошедшим до ответа API; логин с keyboard.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A04 — Встраиваемый виджет и история диалогов

**Этап:** M1. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/widget/src/{loader.ts,bridge.ts,features/history/,features/chat/}; apps/widget/demo/index.html.

**Входы / зависимости:** A02; createPublicSession, listCustomerConversations, createConversation, listCustomerMessages/sendCustomerMessage.

**Выход / интерфейсы:** Loader и iframe; восстановление через getPublicSession по сохранённому bearer; при 401 новый anonymous session с очисткой старого token; новая проблема — новый conversation; polling 3 s и остановка при закрытии.

**Критерий приёмки:** widget.test.ts: чужой postMessage отвергнут; два виджета не делят token; при закрытии polling останавливается; недоступный localStorage даёт memory mode; repeated submit не дублирует сообщение.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A05 — Рабочее место специалиста

**Этап:** M2. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/web/src/features/inbox/.

**Входы / зависимости:** A03; tickets, staff messages, claim/update. На MSW до B04/C05.

**Выход / интерфейсы:** Очередь с фильтрами, диалог, note composer, версии ticket, состояния загрузки/пусто/ошибка.

**Критерий приёмки:** inbox.test.tsx: internal note заметно отличается; 409 обновляет ticket и не затирает чужое назначение; отправка ответа требует действия специалиста.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A06 — Настройки компании, виджеты, база знаний

**Этап:** M2. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/web/src/features/{admin/,knowledge/}; apps/widget/src/accessibility.test.tsx.

**Входы / зависимости:** A03; B widgets/members/categories и C articles/sections/revisions/import TXT.

**Выход / интерфейсы:** Формы сотрудников и widgets; embed snippet; CRUD статей и ревизии; TXT импорт с processing/error.

**Критерий приёмки:** admin.test.tsx: invalid origin показывает 422; unpublished article имеет видимый badge; stale revision показывает conflict; widget: Tab/Escape/focus return и aria labels работают.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A07 — Email, передача человеку, решение и оценки

**Этап:** M3. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/widget/src/features/{handoff/,continuation/,resolution/}; apps/web/src/features/inbox/.

**Входы / зависимости:** A04/A05; handoff/getCustomerTicket polling, confirm/reopen/rating, email request/confirm/exchange.

**Выход / интерфейсы:** Клиент просит специалиста без ИИ; защищённая ссылка, очистка fragment; подтверждение результата и повторное открытие.

**Критерий приёмки:** handoff.test.tsx: AI 503 не блокирует кнопку; ожидание видно; link token не остаётся в URL; score 0/6 не отправляется; решённая заявка подтверждается только явным действием.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A08 — Сквозная интеграция и демонстрация ядра

**Этап:** M4. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** tests/e2e/{core.spec.ts,tenants.spec.ts}; apps/web/src/features/analytics/; docs/demo.md.

**Входы / зависимости:** A01–A07; реальные B/C endpoint M0–M3. До них тесты пишутся на заданных fixtures.

**Выход / интерфейсы:** Обзор метрик + сценарий защиты на двух tenants; Playwright использует реальный backend и fake LLM, не MSW.

**Критерий приёмки:** core.spec.ts: bot answer→handoff→claim→staff reply→confirm; tenants.spec.ts: другой tenant не читает dialogue; fake виден; два origins и mobile viewport. npm run test:e2e проходит.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A09 — UI интеллектуального расширения

**Этап:** M5. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/web/src/features/{assistant/,knowledge-suggestions/}; apps/widget/src/features/attachments/; apps/web/src/features/knowledge/.

**Входы / зависимости:** A08; C09/C10 M5 contracts; сначала MSW.

**Выход / интерфейсы:** PDF/DOCX import, image upload, staff assistant, preview approve/reject suggestion; неподдерживаемые функции спрятаны по capabilities.

**Критерий приёмки:** assistant.test.tsx: draft не отправляется клиенту автоматически; dangerous suggestion нельзя опубликовать без confirm; image errors и import failure видимы.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## A10 — Расширенные отчёты и управление

**Этап:** M5. **Баллы:** 4. **Владелец:** A.

**Создать/изменить:** apps/web/src/features/{analytics/,platform/}; tests/e2e/extensions.spec.ts.

**Входы / зависимости:** A08; B09/B10 и C10; сначала MSW.

**Выход / интерфейсы:** SLA summary, incident candidates, tenant usage/limits, complaint review; финальный e2e расширения.

**Критерий приёмки:** extensions.spec.ts: нулевые знаменатели отображаются как отсутствие данных; superadmin видит агрегаты без переписок; все dashboard числа получены из API.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.
