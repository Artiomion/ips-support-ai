# Запуск и проверка стенда

Выполняйте команды из корня репозитория. Стенд привязан к loopback и
предназначен для разработки и синтетических демонстрационных данных.

```sh
docker compose -p ips-demo -f infra/compose.yaml up -d --build --wait
curl -fsS http://localhost:8000/health/ready
```

При занятых портах задайте `IPS_API_PORT`, `IPS_WEB_PORT`,
`IPS_WIDGET_PORT`, `IPS_POSTGRES_PORT`, `IPS_MAIL_SMTP_PORT` и
`IPS_MAIL_UI_PORT` для нужных сервисов. Сохраните выбранные значения для
всех команд Compose. Для своего браузерного адреса задайте также
`API_ORIGIN`, `WEB_ORIGIN` и `WIDGET_ORIGIN`, затем пересоберите приложения.
Если используете файл настроек, передавайте его через `--env-file` перед
`-f`. В нём можно хранить параметры локальной демонстрации. Оставляйте
файл вне Git.

Если вы используете `.env.example` как основу для Compose, задайте
`SMTP_HOST=mailpit` для локальной почты и `AI_RUNNER_ENABLED=true` для
демонстрационного ИИ. В контейнере адрес `localhost` указывает на сам API.

## Две тестовые организации

На свежем стенде задайте локальный пароль для двух учебных администраторов.

```sh
export IPS_DEMO_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
docker compose -p ips-demo -f infra/compose.yaml run --rm --no-deps \
  -e IPS_DEMO_PASSWORD api python tests/integration/demo_seed.py
```

Команда выводит логины и публичные ключи виджетов. «Альфа» и «Бета»
получают по одному виджету и по две статьи, публичную и внутреннюю.
При повторном запуске используйте тот же пароль. В панели
`http://localhost:5173` проверьте обе организации отдельными входами.
Для подключения используйте сниппет из настроек виджета и добавьте
точный origin учебного сайта в разрешённые адреса. Для демонстрационного
ответа подойдёт вопрос «возврат».

## Почта и ИИ

Локальные письма видны в Mailpit на `http://localhost:8025`. После
действия, которое ставит письмо в очередь, обработайте одно задание.

```sh
docker compose -p ips-demo -f infra/compose.yaml exec api python -m ips.platform.notifications
```

После слияния B07 эта же команда обрабатывает подтверждения адреса и
уведомления о публичном ответе специалиста. Собранная страница перехода
из письма появится после A07.

Для внешнего SMTP передайте серверу `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`,
`SMTP_TLS` (`starttls` или `implicit`), `SMTP_USERNAME`, `SMTP_PASSWORD`
и при необходимости `SMTP_TIMEOUT_SECONDS`. После изменения параметров
пересоздайте контейнер API. Проверьте доставку на согласованный адрес и
открытие ссылки в отдельном контексте браузера.

Стенд передаёт `AI_PROVIDER`, `AI_API_KEY` и `AI_MODEL` только в API.
Фоновый обработчик текущего сервера принимает `AI_PROVIDER=fake`.
Для реальной демонстрации потребуется подключить выбранный адаптер
к обработчику и пройти проверку C08. Настройка ключей и критерии общей
приёмки описаны в [сценарии M4](demo-live-services.md).

## Резервная копия и восстановление

Сначала заполните стенд данными из предыдущего раздела. Сохраните архив
в закрытый локальный каталог. Восстановите его в отдельную пустую базу
`ips_restore_smoke`, чтобы проверить копию рядом с рабочей базой.

```sh
umask 077
docker compose -p ips-demo -f infra/compose.yaml exec -T postgres \
  pg_dump -U ips -d ips --format=custom --no-owner > "${TMPDIR:-/tmp}/ips-demo.dump"
docker compose -p ips-demo -f infra/compose.yaml exec -T postgres createdb -U ips ips_restore_smoke
docker compose -p ips-demo -f infra/compose.yaml exec -T postgres \
  pg_restore -U ips -d ips_restore_smoke --no-owner --exit-on-error --single-transaction \
  < "${TMPDIR:-/tmp}/ips-demo.dump"
docker compose -p ips-demo -f infra/compose.yaml exec -T postgres \
  psql -U ips -d ips_restore_smoke -v ON_ERROR_STOP=1 \
  -c "SELECT name FROM organizations WHERE name IN ('Альфа','Бета') ORDER BY name"
```

Ожидаются две строки. Поведенческий тест входит в
[`backend/tests/integration/test_core.py`](../backend/tests/integration/test_core.py).
Он создаёт отдельные базы, применяет миграции с пустой схемы и с ревизии
M3, проверяет откат handoff и доступ к заявке после перезапуска API,
восстанавливает архив и проверяет изоляцию данных через API.
При локальном запуске тестов нужны `pg_dump` и `pg_restore` совместимой
с сервером версии. Для PostgreSQL в Docker можно передать имя контейнера
в `IPS_POSTGRES_CONTAINER` и запустить из каталога `backend`
`uv run --frozen pytest -q tests/integration`.

## Остановка и сброс

`docker compose -p ips-demo -f infra/compose.yaml stop` сохраняет базу.
Полный сброс следующей командой удаляет базу проекта `ips-demo`, включая
учётные записи, обращения и восстановленную копию в этом volume.

```sh
docker compose -p ips-demo -f infra/compose.yaml down --volumes
```
