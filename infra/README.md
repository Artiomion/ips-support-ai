# Инфраструктура: B

Compose запускает PostgreSQL 17 и Mailpit. В B08 добавятся API и статические
web/widget; worker того же приложения появится в B09.
В ядре B07 persisted jobs для писем могут исполняться простой командой
одноразового drain; B09 добавляет постоянный worker с lease/retry.
Каждый этап обязан уметь показать отправленное письмо в sandbox.

Не добавлять secrets и реальные переписки. Обновление схемы — Alembic,
не create_all на старте сервера. Проверка восстановления backup в B08.

Из корня репозитория запустите `docker compose -f infra/compose.yaml up -d --wait`.
Из каталога `backend` выполните `uv sync --frozen`, `uv run alembic upgrade head`
и `uv run uvicorn ips.main:app --reload`. Проверки: `/health/live` и
`/health/ready` на `http://localhost:8000`. Для локальных данных используется
Docker volume; остановка `docker compose -f infra/compose.yaml stop` его не удаляет.

Для входа персонала `WEB_ORIGIN` должен точно совпадать с origin панели.
Переменная `SESSION_SECRET` используется для CSRF-токенов; задавайте случайное
значение отдельно в каждом окружении, не добавляя его в Git. В `production`
нужны `COOKIE_SECURE=true` и отдельный секрет длиной не менее 32 символов.

Для локальных приглашений Mailpit показывает письма на `http://localhost:8025`,
SMTP принимает сообщения на `localhost:1025`. Обработать одно задание очереди
можно из каталога `backend`: `uv run python -m ips.platform.notifications`.
Mailpit предназначен для локальной демонстрации; внешняя доставка настраивается
отдельно.
