# Progress: подготовка ИПС

## Выполнено

- Прочитано ТЗ и уточнены Python/GitHub.
- Подготовлены инструкции, архитектура, модель данных, HTTP/внутренние
  контракты, scope/риски, 3 дорожки, шаблоны PR/Issues и локальные инструменты.
- Проверены установка pinned навыков и шесть сценариев безопасности файлов.
- Независимый reviewer подтвердил закрытие найденных пробелов.
- Создан приватный Artiomion/ips-support-ai, main отправлен; 30 Issues
  с метками ролей/этапов, по 10 на участника.
- GitHub Actions Planning package для первого commit завершился success:
  https://github.com/Artiomion/ips-support-ai/actions/runs/43915785608

## Фактические проверки

- scripts/check_project.py: 30 карточек; 40/32 балла на каждого; баланс всех
  этапов; OpenAPI refs/examples; переносимые пути и Markdown links.
- OpenAPI 3.1: официальный openapi-spec-validator подтвердил валидность.
- unittest installer: 6 tests OK (repeat, tamper, unmanaged, traversal,
  symlink, pin mismatch).
- Реальная установка навыков в /private/tmp: оба default навыка установлены,
  upstream SKILL.md hashes совпали.
- Python py_compile и git diff --check: пройдены на текущем этапе.

## Исправленные проблемы окружения

- gh auth внутри ограниченной сети дал ложную ошибку token; сетевой read-only
  gh api user подтвердил аккаунт без повторной аутентификации.
- Standalone Python не нашёл CA; downloader использует проверенный системный
  curl при этой конкретной ошибке. Проверка TLS не отключается.
- Для одноразового OpenAPI validator потребовался доступ uv к его cache/network.

## Не реализовывалось и не проверялось

Runtime приложения, backend/frontend dependencies, Docker стенд, реальные
LLM/SMTP вызовы и CI продукта. Они входят в последующие карточки.
Пользовательские аккаунты коллег, OAuth и приглашения не настроены:
логины двух участников неизвестны.
