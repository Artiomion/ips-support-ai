# ИПС — клиентская поддержка с ИИ

Учебный проект для трёх разработчиков: виджет клиента, рабочее место
специалиста, управление компанией и ответы ИИ по базе знаний.

[Репозиторий проекта](https://github.com/Artiomion/ips-support-ai) ·
[30 задач в GitHub](docs/github-issues.md).

**Backend и часть платформенных функций уже реализованы; работа продолжается.**
Актуальное состояние каждой функции — в GitHub Issues. Python выбран для backend.
Репозиторий рассчитан на работу
через небольшие GitHub PR и обычные кодовые модели со средним рассуждением.

Начать: [инструкция участника](docs/start-here.md),
[план на троих](docs/roadmap.md), [настройка ИИ](docs/ai-setup.md).

| Участник | Ответственность | План | Баллы: ядро + расширение |
|---|---|---|---|
| A — [bachaa7](https://github.com/bachaa7) | Панель компании, специалист, виджет, API-клиент | [A](docs/plans/track-a.md) | 32 + 8 |
| B — [Artiomion](https://github.com/Artiomion) | Платформа, доступы, заявки, маршрутизация, инфраструктура | [B](docs/plans/track-b.md) | 32 + 8 |
| C — [Gwynblai](https://github.com/Gwynblai) | Диалоги, сообщения, ИИ, база знаний, документы | [C](docs/plans/track-c.md) | 32 + 8 |

Баллы — исходная относительная оценка, не обещание равных часов.
Правило выравнивания после каждого этапа: [командный процесс](docs/team-workflow.md).

## Навигация

- [Исходное ТЗ без изменений](docs/product-spec.md).
- [Архитектура и решения](docs/architecture.md), [модель данных](docs/data-model.md).
- [HTTP и внутренние интерфейсы](docs/contracts.md), [OpenAPI](contracts/openapi.json).
- [Что входит в защиту и что отложено](docs/scope.md), [проверки](docs/verification.md).
- [Подключение настоящих ИИ и почты к демонстрации](docs/demo-live-services.md).
- [Покрытие требований](docs/requirements.md), [риски и путь к production](docs/risks.md).
- [GitHub и правила совместной работы](docs/team-workflow.md).
- [Готовые запросы для моделей](docs/prompts/task.md).

## Проверка подготовленных файлов

```bash
python3 scripts/check_project.py
python3 scripts/bootstrap_ai.py --check
```

Необходимое для будущего запуска: Python 3.12, uv, Node.js 24 LTS, npm,
Docker Compose, Git. Точные версии пакетов зафиксируют A01/B01 в lock-файлах.
Установка проверенных навыков: `python3 scripts/bootstrap_ai.py --install`.
Это локальная установка в игнорируемую `.agents/skills/`; настройки аккаунта
не меняются. См. [полную инструкцию](docs/ai-setup.md).
