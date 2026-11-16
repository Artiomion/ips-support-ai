# Интерфейсы v1

Машиночитаемые поля/ответы: `contracts/openapi.json`. Примеры в
`contracts/examples/`. Это проект контракта, а не опубликованный сервер.
Endpoint M5 помечены `x-phase: M5`, остальные — ядро M0–M4.

## Общие правила

- Base `/api/v1`; JSON, UTF-8; snake_case; UUID; время ISO-8601 UTC.
- Коллекции `{items: [...], next_cursor: string|null}`. Limit 1..100,
  default 30; cursor непрозрачный, ограничен tenant и сортировкой.
- Ошибка `{error: {code, message, request_id}}`; HTTP 401 без сессии,
  403 при недостаточной роли, 404 для чужого/недоступного объекта,
  409 при конфликте версии/состояния, 422 для валидации, 429 для квоты,
  503 при недоступности функции. FastAPI default validation error
  приводится к этому формату, а не образует второй контракт.
- POST создания возвращает 201, update/action — 200, delete — 204.
- Публичное поле `tenant_id` не принимается. Tenant в DTO ответа допустим
  только для staff context. Поле нельзя использовать как аргумент доступа.
- PUT ticket требует `expected_version`; успешная запись увеличивает version.
- Авторизация: staff cookie `ips_session`; customer `Authorization: Bearer`.
  Staff mutations требуют `X-CSRF-Token` и проверку Origin.
- StaffSession — union по `scope`: tenant (admin/specialist с tenant и
  membership) либо platform (superadmin без tenant membership). Platform
  session проходит только platform guards. Общий staff guard не допускает
  superadmin к сообщениям компаний.
- GET capabilities возвращает фактически работающие features. Ядро публикует
  txt_import; M5 flags включаются после integration gate своей функции.

## Идентификация и встраивание виджета

JS loader работает в контексте сайта компании. Он вызывает публичный
session endpoint с браузерным Origin сайта, который сравнивается с точным
`scheme://host:port` из allowlist; wildcard по умолчанию запрещён.
`null`/отсутствующий Origin не принимается этим browser endpoint.
Стандартные порты `:80` для HTTP и `:443` для HTTPS при сохранении убираются,
чтобы совпадать с форматом [Origin браузера](https://developer.mozilla.org/en-US/docs/Web/API/URL/origin).
Session сохраняет widget/customer/tenant. CORS возвращает только разрешённый
origin; статический loader может быть доступен всем.

Сохранённый bearer проверяется GET `/public/session`, который возвращает
metadata и site_origin без нового token. При expiry (401) loader удаляет
старый token и создаёт новую anonymous session. Для восстановления старой
переписки нужен действующий bearer, подписанный identity либо email flow.

Анонимный bearer хранится loader в localStorage **сайта компании**, ключ
`ips:<widget_key>:session`; третий сайт не получает историю. Loader передаёт
его iframe через postMessage с точным targetOrigin. Iframe принимает init
только от parent с origin, совпадающим с session.site_origin; origin session
проверяется API, не доверять произвольному init payload. До проверки iframe
не показывает историю. Все последующие iframe API calls разрешают только
служебный widget origin и проверенный bearer, без third-party cookie.
При недоступном storage сессия живёт в памяти; предлагается email continuation.

Администратор получает ключ для конкретного виджета через защищённый
`POST /api/v1/widgets/{widget_id}/signing-key` с CSRF. Ответ содержит
base64url без padding в поле `key` и `algorithm: HMAC-SHA256`; сервер сайта
декодирует key в байты. Ответ не кэшируется, ключ нельзя помещать в браузер
или логи. Ключ производен от серверного `SESSION_SECRET` и widget ID; смена
мастер-секрета меняет все ключи, поэтому сайтам понадобится обновить их.

Для сайта с login его **сервер** подписывает HMAC payload:
`widget_key, external_id, issued_at, expires_at, nonce` (base64url JSON + HMAC
SHA-256). `issued_at` и `expires_at` — целые секунды Unix UTC; `nonce` —
случайная строка длиной 16–128 символов. Сначала JSON в UTF-8 кодируется в
base64url без padding, затем HMAC-SHA256 вычисляется от ASCII-байтов полученной
строки, а подпись тоже кодируется в base64url без padding. Итоговый формат:
`<payload>.<signature>`. Секрет отдельный для каждого widget, никогда не в JS. Максимальный
TTL 5 минут, nonce одноразовый. API проверяет подпись constant-time, TTL,
nonce, привязку к widget и только затем связывает customer. Email из identity
payload не считается подтверждённым. Поддельный Origin вне браузера не даёт
права на существующего customer: для него нужен bearer/подпись.

Email запрашивается внутри авторизованного customer session; письмо содержит
одноразовый случайный token в URL fragment. Страница отправляет его POST
confirm/exchange, получает conversation-scoped bearer, удаляет fragment из
истории. GET по ссылке не раскрывает переписку. TTL 30 минут; хранится hash;
повтор/expiry дают одинаковую безопасную ошибку. Не логировать token и query.

## Conversation и сообщения

`mode`: `bot | human`; это режим ответов, отдельно от ticket.status.
`Message.kind`: `customer | assistant | specialist | internal_note`.
PublicMessage DTO допускает первые три, internal_note исключён из schema.
Public sources содержат только `article_id, revision, title`, не полное
тело внутренней статьи или произвольный URL от модели.

POST сообщения принимает `client_message_id` (UUID) и `text` 1..8000 символов.
Возвращает только сохранённое сообщение с 201. ИИ работает отдельно через
persisted ai_run/job; клиент опрашивает GET messages. Повтор того же ID и
текста возвращает существующее сообщение с 200; другой текст с тем же ID — 409.
`after_seq` для инкрементального опроса; сортировка по seq ASC.
В M5 `attachment_ids` содержит не более 4 ready image IDs того же customer,
widget и conversation. Привязка проверяется сервером при записи сообщения;
изображения передаются provider только при включённой vision capability.
Public/staff message возвращает attachment_ids (в ядре пустой список).

Handoff вызывается клиентом явно или C orchestration через TicketPort.
Оба пути имеют одну серверную реализацию. Результат `TicketSummary`, режим
conversation атомарно human. Возврат к ИИ в ядре возможен только после
решения/закрытия и явного `resume_bot`; при активном ticket — 409.
GET `/public/tickets/{ticket_id}` позволяет клиенту опрашивать статус
с тем же bearer; проверяет владельца conversation и conversation_scope.

## Статусы заявки

| Из | Разрешено в | Кто |
|---|---|---|
| new | waiting_specialist | Сервис при постановке в очередь |
| waiting_specialist | in_progress | Claim/назначение, доступный специалист либо admin |
| in_progress | waiting_customer, resolved | Исполнитель или admin |
| waiting_customer | in_progress, resolved | Исполнитель/admin; ответ клиента возвращает in_progress |
| resolved | in_progress, closed | Клиент reopen; клиент confirm/worker close |
| closed | — | Терминальное состояние; новый handoff создаёт новый ticket |

`new` — короткое начальное состояние, handoff завершает транзакцию уже с
waiting_specialist/in_progress. Передача на другого исполнителя сохраняет
in_progress и увеличивает version. Освобождение возвращает waiting_specialist.
POST claim: один победитель, остальным 409; назначение тому же исполнителю
при совпавшей версии можно считать идемпотентным. Приоритеты: low, normal,
high, urgent. Конкретное время SLA в M5 задаётся настройкой организации.

## Внутренние порты B/C

Определения живут в `backend/src/ips/core/ports.py` после B01. До него
этот раздел — источник имен. Все типы — dataclass/Pydantic DTO, без ORM.

```python
TenantContext(tenant_id: UUID, user_id: UUID, membership_id: UUID, role: str)
CustomerPrincipal(tenant_id: UUID, customer_id: UUID, widget_id: UUID,
                  conversation_scope: UUID | None)
Citation(article_id: UUID, revision: int, title: str)
AnswerInput(text: str, history: list[dict], snippets: list[dict],
            images: list[ImageInput])  # images=[] до M5
AnswerResult(text: str, citations: list[Citation], needs_human: bool,
             category: str | None, priority: str, reason: str | None)

# Все методы sync; uow — request-scoped Session, commit делает orchestration.
TicketPort.escalate(uow, principal: CustomerPrincipal, conversation_id: UUID,
                   reason: str, category: str | None, priority: str) -> TicketSummary
TicketPort.on_customer_message(uow, principal: CustomerPrincipal,
                               conversation_id: UUID) -> None
TicketPort.has_active(uow, tenant_id: UUID, conversation_id: UUID) -> bool
TicketPort.record_staff_reply(uow, ctx: TenantContext,
                              conversation_id: UUID, created_at: datetime) -> None
ConversationPort.lock_for_handoff(uow, tenant_id: UUID,
                                  conversation_id: UUID) -> ConversationState
ConversationPort.mark_human(uow, state: ConversationState) -> None
ConversationPort.add_staff_message(uow, ctx: TenantContext,
                                   conversation_id: UUID, text: str,
                                   internal: bool,
                                   client_message_id: UUID) -> StaffMessage
ConversationPort.metrics(uow, ctx: TenantContext,
                          widget_id: UUID | None) -> ConversationCounts
NotificationPort.enqueue_staff_reply(uow, event: StaffReplyEvent) -> None
KnowledgeSearch.search(tenant_id: UUID, widget_id: UUID, query: str,
                       audience: Literal['customer', 'staff'], limit: int = 5
                       ) -> list[KnowledgeSnippet]
LLMProvider.answer(data: AnswerInput, timeout_seconds: float) -> AnswerResult
UsageBudget.reserve(tenant_id: UUID, run_id: UUID) -> Reservation
UsageBudget.settle(reservation: Reservation, usage: Usage, failed: bool) -> None
```

`ConversationState`: id, tenant_id, customer_id, widget_id, mode, version.
`KnowledgeSnippet`: article_id, revision, title, text, visibility.
`Reservation`: id, tenant_id, run_id. `Usage`: input_tokens, output_tokens.
`ImageInput`: attachment_id, mime, bytes (внутренний DTO, не публичный URL).
`StaffReplyEvent`: tenant_id, conversation_id, message_id, created_at.
`ConversationCounts`: conversations, ai_confirmed, unconfirmed, handoffs.
Outcome human_confirmed ставится при customer confirm ticket; B04 вызывает
`ConversationPort.confirm_human(uow, tenant_id, conversation_id) -> None`.
Event enqueue выполняется **в той же транзакции**, что public staff message;
idempotency_key = staff_reply:<message_id>. Повтор сообщения не создаёт
дубликат письма. record_staff_reply сохраняет первый ответ ticket ровно один
раз; internal note не вызывает ни один из этих эффектов.
`TicketSummary` и `StaffMessage` совпадают с OpenAPI. Порты не принимают
непроверенный tenant из HTTP. Staff search допускает internal только после
проверки staff context; клиентский сервис жёстко устанавливает customer scope.

B предоставляет fake ConversationPort для tests tickets, C — fake TicketPort
и UsageBudget для tests orchestration. Integration test заменяет их настоящими
адаптерами. Composition root B связывает интерфейсы без циклических импортов.

## Исполнение фоновых работ в ядре

B01 предоставляет минимальные `jobs.enqueue`/`drain_once` для писем с
idempotency key; B03/B07 используют их, для локального демо drain запускается
командой. B09 добавляет постоянный worker, lease/retry и расписание.
TXT import C03 ограничен 2MiB и обрабатывается в запросе с сохранением import
record; PDF/DOCX в M5 идут через jobs. C05 отдельно владеет ai_run runner:
background thread в lifespan API, новые Session, атомарный claim с lease,
подбор pending/просроченных runs после рестарта. Сеть вне транзакции;
финальная проверка mode/version обязательна. При нескольких API-экземплярах
lease должен защищать от повторного ответа; в ядре используется один API.

## Настройки расширения

SupportPolicy: IANA timezone, weekly windows (day 0=понедельник, start/end
HH:mm, start<end, без перехода через полночь; такой интервал разбивается),
first_response/resolution и urgent_first_response minutes. Проверять реальное
время 00:00..23:59, не только JSON pattern. B09 реализует GET/PUT policy.
B10 предоставляет GET/PUT platform limits и подтверждение incident candidate.
C10 предоставляет list/get/review complaints; source_snapshot содержит
ревизии, использованные в момент ответа, а не только текущую статью.

## Правило изменения контракта

Сначала небольшой PR в contracts с примером и consumer impact, затем
реализации backend и frontend. A не ждёт готового endpoint: работает с MSW.
B/C не редактируют generated TypeScript. API schema export проверяет
реализованные operationId/request/response/status против замороженного
контракта; M5 не считается реализованным до своей карточки.
