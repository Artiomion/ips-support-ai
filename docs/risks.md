# Риски и дальнейшее усиление

| Риск | Проверка в учебной версии | Усиление перед реальными пользователями |
|---|---|---|
| Утечка другой компании | Tenant-scoped repositories, составные FK, 404 на чужой ID, тесты двух компаний | RLS, отдельный runtime DB role, аудит всех путей |
| Внутренняя инструкция в ответе | Разные retrieval scopes и response DTO, отрицательные тесты | Независимый security review и adversarial набор |
| Prompt injection | Документы как данные, без tools/SQL/внешней сети, output schema, provenance | Расширенный red-team; нельзя обещать полную защиту одним prompt |
| Гонка назначения/лимита | Два одновременных claim, row locks, версия, пересчёт нагрузки | Нагрузочные тесты и наблюдаемость блокировок |
| Ответ ИИ после handoff | Проверка mode/version при финальной записи | Outbox и отдельный worker при росте |
| Отказ или стоимость LLM | Timeout, квота с резервированием, fake CI, ручной handoff | Денежный budget, latency/error metrics, политика провайдеров |
| Гонки миграций B/C | Уникальные revisions, merge heads, чистый upgrade | Автоматический тест upgrade и rollback strategy |
| Ненадёжные фоновые задачи | DB jobs, lease и idempotency; SMTP sandbox | Transactional outbox, ретраи, dead-letter, мониторинг |
| Вложения и документы | Лимиты, MIME validation, случайные имена, private download | Object storage, antivirus/sandbox, limits CPU/RAM, OCR isolation |
| Потеря устройства анонима | Объяснено ограничение localStorage; email flow возвращает доступ | Session management, восстановление, rate limits против перебора |
| Публичный widget key | Это locator, не секрет; подтверждённая identity подписывается сервером сайта | Rotation, abuse detection, server-side quotas |
| Неверный ответ провайдера | Валидировать структурный результат, citations только к найденным revisions | Оценки на реальном наборе без пересылки лишних ПД |
| Деплой одного сервера | Volumes, backup/restore smoke, TLS для публичного демо | HA, PITR, alerting, нагрузка, план реагирования |

Не заявлять готовность к production по наличию Docker и тестов. Перед реальным
запуском отдельно нужны восстановление из backup, управление секретами,
наблюдаемость, ограничения ресурсов, email delivery, проверка хранения/удаления,
проверка доступности и требования выбранной страны/организации.

## Что намеренно упрощено

Опрос вместо realtime; один LLM provider; FTS вместо embeddings в ядре;
локальное хранилище в M5; один worker; ручное утверждение обучения;
простая доступность специалистов вместо сложного календаря. Места замены
заданы портами и не требуют микросервисов.
