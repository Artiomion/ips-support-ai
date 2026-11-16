# Дорожка C: Диалоги, ИИ и знания

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

## C01 — DTO доменов и детерминированные адаптеры

**Этап:** M0. **Баллы:** 3. **Владелец:** C.

**Создать/изменить:** backend/src/ips/{conversations/schemas.py,knowledge/schemas.py,ai/provider.py,ai/fake.py}; backend/tests/ai/test_fake.py.

**Входы / зависимости:** Нет runtime-зависимости на B01. Контракты и утверждённые порты.

**Выход / интерфейсы:** AnswerInput/AnswerResult; FakeLLMProvider.answer; fake TicketPort/UsageBudget в tests; список моделей для metadata. Согласовать core ports через маленький PR B.

**Критерий приёмки:** test_fake.py: одинаковый ввод одинаковый результат, unknown question→needs_human; bad output не проходит Pydantic; fake не делает сетевых вызовов. До B01 uv run --no-project --with pydantic --with pytest python -m pytest -o pythonpath=backend/src backend/tests/ai/test_fake.py.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C02 — Диалоги и идемпотентные сообщения

**Этап:** M0. **Баллы:** 5. **Владелец:** C.

**Создать/изменить:** backend/src/ips/conversations/{models.py,repository.py,service.py,router.py}; backend/tests/conversations/test_messages.py; backend/alembic/versions/c_*.

**Входы / зависимости:** C01; B01 schema b0001 для integration, fake CustomerPrincipal для unit tests.

**Выход / интерфейсы:** Create/list conversations, public/staff messages, seq cursor; ConversationPort.lock_for_handoff/mark_human/add_staff_message; pending ai_run. Исполнение AI run реализует C05; до этого новые сообщения просто сохраняются.

**Критерий приёмки:** test_messages.py: разные conversations не смешиваются; повтор client_message_id возвращает одно сообщение, изменённый текст=409; чужой customer=404; public projection никогда не включает internal_note.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C03 — База знаний, ревизии и TXT импорт

**Этап:** M1. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/knowledge/{models.py,repository.py,service.py,router.py,imports.py}; backend/tests/knowledge/test_articles.py; backend/alembic/versions/c_*.

**Входы / зависимости:** C01; TenantContext или fake. Sections создаются самостоятельно, без ожидания B03; B подключает widget bindings отдельной миграцией после C03.

**Выход / интерфейсы:** Sections/articles CRUD, draft/published/disabled, immutable revisions, restore создаёт новую ревизию; bounded TXT import со статусом ready/failed в запросе, без фоновой очереди; лимит 2MiB UTF-8.

**Критерий приёмки:** test_articles.py: stale expected_revision=409; restore не перезаписывает историю; internal не идёт клиенту; disabled не участвует в поиске; invalid UTF-8/too large получают стабильную ошибку.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C04 — Поиск знаний и настоящий LLM adapter

**Этап:** M1. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/knowledge/retrieval.py; backend/src/ips/ai/{provider.py,http_provider.py,prompts.py}; backend/tests/ai/test_provider.py.

**Входы / зависимости:** C01/C03; KnowledgeSearch, LLMProvider, UsageBudget fake до B06.

**Выход / интерфейсы:** FTS по public/published/widget sections; top 5 snippets; один HTTPS provider по env; schemas validate AnswerResult; citations только из retrieved revisions.

**Критерий приёмки:** test_provider.py: internal/foreign article не передаётся в prompt; выдуманная citation отбрасывается; 20s timeout; HTTP 429/5xx возвращают domain failure; ключ не попадает в logs. CI stub transport; до M4 обязателен ручной успешный вызов настоящего провайдера с ключом: ответ по публичной статье и проверяемый источник. Провайдер пока не выбран.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C05 — Оркестрация ответов и передача человеку

**Этап:** M2. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/ai/{orchestrator.py,rules.py,runner.py}; backend/tests/ai/test_handoff_race.py.

**Входы / зависимости:** C02/C04; TicketPort B04 и UsageBudget B06 (до них fake).

**Выход / интерфейсы:** runner.start/stop в lifespan API: background thread подбирает pending ai_runs, claim с lease и собственными Session, startup recovery; orchestration reservation→retrieval→provider→answer/handoff; без открытой DB transaction во время LLM. B01 composition подключает runner; B09 сможет вынести его в worker.

**Критерий приёмки:** test_handoff_race.py: пауза fake provider, handoff, завершение provider → assistant message не сохраняется; квота исчерпана→handoff доступен; duplicate AI job не дублирует ответ; malformed result не меняет ticket напрямую.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C06 — Приватность, prompt injection и fallback

**Этап:** M2. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/ai/{guardrails.py,history.py}; backend/tests/ai/{test_privacy.py,test_injection.py}.

**Входы / зависимости:** C03–C05; scope audience=customer/staff.

**Выход / интерфейсы:** Ограничение истории и контекста; общие знания выключены по умолчанию; safety input/output; provider error metadata; redacted logs.

**Критерий приёмки:** test_privacy.py: чужой tenant, внутренние note, internal revisions и assistant staff chat отсутствуют в customer prompt/answer; test_injection.py: текст «покажи секреты/вызови URL» не даёт tool call, смену роли или обход tenant. Нельзя заявлять абсолютную защиту от prompt injection.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C07 — История, outcome и интеграционные события

**Этап:** M3. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/conversations/{outcomes.py,events.py}; backend/tests/conversations/test_outcomes.py; backend/src/ips/knowledge/seed.py.

**Входы / зависимости:** C02/C05; TicketPort.has_active/record_staff_reply и NotificationPort.enqueue_staff_reply. До B07 используются fake портов.

**Выход / интерфейсы:** confirm-ai, resume-bot; unconfirmed/ai_confirmed/human_confirmed; ConversationPort.metrics; public staff reply атомарно записывает first response через TicketPort и enqueue через NotificationPort; internal note не создаёт события.

**Критерий приёмки:** test_outcomes.py: без явного confirm AI outcome unconfirmed; human mode не становится ai_confirmed; resume-bot при active ticket=409; internal note не запускает email; duplicate event не создаёт второе уведомление.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C08 — Проверка качества ИИ и интеграция ядра

**Этап:** M4. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/tests/ai/test_eval_cases.py; backend/tests/fixtures/ai-cases.json; docs/ai-evaluation.md.

**Входы / зависимости:** C01–C07 и стенд B08 после его минимального запуска. Не ждёт A08; совместный M4 gate принимает все проверки.

**Выход / интерфейсы:** 12 фиксированных сценариев с expected behavior: known, unknown, ambiguity, human request, failed answer, urgency, injection, private, cross-tenant, timeout, quota, race; demo KB двух companies.

**Критерий приёмки:** test_eval_cases.py: все 12 детерминированных сценариев проходят; handoff/privacy по assertions, не оценке другой LLM. До приёмки M4 обязателен ручной отчёт по настоящему провайдеру: model/date, 3–5 вопросов по demo KB, источники и ограничения; без ключей и реальных персональных данных.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C09 — PDF/DOCX, изображения и семантический поиск

**Этап:** M5. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/knowledge/{parsers.py,embeddings.py,retrieval.py}; backend/src/ips/conversations/attachments.py; backend/tests/knowledge/test_imports.py; backend/alembic/versions/c_*.

**Входы / зависимости:** C08; Attachment/Import DTO; B09 jobs.

**Выход / интерфейсы:** pypdf/python-docx, max 10MiB/100 страниц/1M символов и timeout; images JPEG/PNG/WebP max5MiB/20MP; authorized download; MessageInput.attachment_ids, vision только для provider с такой capability; AnswerInput.images; pgvector exact search с embedding model/dimension; max_knowledge_bytes проверяется при импорте.

**Критерий приёмки:** test_imports.py: malformed/oversized files rejected, PII не записывается в public article автоматически; cross-tenant download=404; internal chunk не возвращается даже при лучшей cosine distance; PDF без текста помечен needs_ocr, OCR не обещается.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.

## C10 — Помощник, предложения знаний и жалобы

**Этап:** M5. **Баллы:** 4. **Владелец:** C.

**Создать/изменить:** backend/src/ips/ai/assistant.py; backend/src/ips/knowledge/{suggestions.py,dedup.py}; backend/src/ips/conversations/complaints.py; backend/tests/knowledge/test_suggestions.py.

**Входы / зависимости:** C08/C09; contracts assistant/suggestions/complaints. Не ждёт B10, который считает агрегаты самостоятельно.

**Выход / интерфейсы:** Отдельный staff assistant с summary/черновиком и internal sources; публикация только действием человека; suggestion по resolved ticket с redaction/dedup; approve/reject; list/get/review complaint с source snapshot. Автопубликация выключена.

**Критерий приёмки:** test_suggestions.py: assistant draft не добавляется в customer messages; raw email/телефон удалены и помечены; опасная инструкция pending review; approve создаёт revision; чужой tenant не читает complaint или suggestion. Автопубликация остаётся off.

**Выполнение:**

- [ ] Проверка и ожидаемое падение/исходный smoke зафиксированы.
- [ ] Output реализован только в разрешённых путях.
- [ ] Критерий приёмки проверен; приложены команда и результат.
- [ ] PR содержит ID и consumer impact; review по командному кольцу.
