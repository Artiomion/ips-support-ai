# Модель данных

UUID для внешних ID, UTC timestamps в PostgreSQL `timestamptz`. Все
tenant-сущности имеют `tenant_id NOT NULL`. У внешних связей составной FK
`(tenant_id, target_id)` → `UNIQUE(tenant_id,id)` цели; случайный UUID не
заменяет авторизацию. Глобальные users/organizations — исключения явно ниже.

| Таблицы | Владелец | Ключевые поля и ограничения |
|---|---|---|
| organizations | B | id, name, settings JSON, created_at; корень tenant |
| users | B | id, normalized_email unique, password_hash, is_superadmin; глобальны |
| memberships | B | tenant_id, user_id, role `admin\|specialist`, available, max_active; unique(tenant,user) |
| staff_sessions | B | token_hash unique, user_id, active_tenant_id, expires_at; проверять membership каждый запрос |
| invitations | B | tenant_id, email, role, token_hash, expires_at, accepted_at; одноразовые |
| widgets | B | tenant_id, name, color, greeting, allowed_origins[], public_key unique, enabled |
| widget_sections | B | tenant_id, widget_id, section_id; составные FK, общий справочник разделов C |
| customers | B | tenant_id, external_id nullable, email nullable, email_verified_at; partial unique(tenant,external_id) |
| customer_sessions | B | tenant_id, customer_id, widget_id, token_hash, site_origin, expires_at, conversation_scope nullable; scope одного widget |
| customer_links | B | tenant_id, customer_id, conversation_id, token_hash, expiry, consumed_at; продолжение после email verification |
| conversations | C | tenant_id, widget_id, customer_id, mode `bot\|human`, version, outcome `unconfirmed\|ai_confirmed\|human_confirmed`, last_activity_at |
| messages | C | tenant_id, conversation_id, seq, kind, author_id, text, client_message_id, sources JSON; unique(conversation,seq), unique(conversation,client_message_id) при not null |
| tickets | B | tenant_id, conversation_id, assignee_membership_id nullable, status, priority, category_id, version, resolved_at, closed_at |
| categories + specialist_categories | B | tenant-scoped категории и связи specialization |
| article_sections | C | tenant_id, name |
| articles | C | tenant_id, section_id, visibility `public\|internal`, status `draft\|published\|disabled`, current_revision_id |
| article_revisions | C | tenant_id, article_id, version, title, body, source_type, source_id, created_by, created_at; unique(article,version) |
| knowledge_chunks | C | tenant_id, article_id, revision_id, text, search_vector; embedding nullable только в M5 |
| ai_runs | C | tenant_id, conversation_id, input_message_id, state, provider, model, usage, error_code, generation_version, lease_until, attempts; уникальная работа на input_message |
| usage_reservations + tenant_usage | B | tenant_id, reservation_id, day, reserved/used, request_count; атомарная проверка до вызова |
| ratings | B | tenant_id, ticket_id, customer_id, score 1..5, comment; unique(ticket) |
| audit_events | B | tenant_id nullable для платформы, actor_type/id, action, object_id, time, redacted_payload |
| jobs | B | tenant_id, kind, payload, idempotency_key unique, status, attempts, run_after, lease_until |
| attachments + imports | C, M5 | tenant_id, owner/conversation, storage_key, MIME, size, state, error; закрытый доступ |
| knowledge_suggestions | C, M5 | tenant_id, ticket_id, target_article_id nullable, draft_text, state, pii_flags |
| assistant_messages + ai_complaints | C, M5 | tenant_id, ticket_id/conversation_id, staff_id, text, references; отдельные от client messages |

`conversation_scope` пока всегда NULL: ограниченные одним диалогом токены
появятся в B07. До их выпуска B/C добавляют tenant-scoped FK на conversations
и проверку принадлежности диалога клиенту и widget в той же транзакции.

У `tickets`: partial unique `(tenant_id, conversation_id)` WHERE
`status NOT IN ('resolved','closed')`. Повтор handoff возвращает текущий
активный ticket. Возобновление resolved ticket выполняется под блокировкой
conversation; закрытый ticket не возвращается в работу, создаётся новый
в этом же диалоге при новой эскалации. Только один может быть активным.

Простое назначение блокирует строку membership кандидата и ticket, после
блокировки пересчитывает активную нагрузку. Везде один порядок блокировок;
условный update ticket проверяет version и assignee. Ограничение нагрузки
проверяется внутри транзакции, а не только при предварительном SELECT.

Внутренние заметки хранятся как `messages.kind=internal_note`, но клиентские
запросы используют отдельную проекцию с allowlist kind. Helper chat никогда
не хранится как обычное сообщение conversation. Последовательность `seq`
может иметь пропуски в клиентской проекции — это не утечка содержимого.

## Миграции без ожидания друг друга

B01 создаёт базовый revision `b0001` с organizations/users/widgets/customers
и техническим каркасом; B02 добавляет поведение авторизации. C01 проектирует
ORM/DTO без ожидания БД. C02 начинает миграции после schema `b0001` или на
отдельной временной БД по контракту; первая миграция C зависит от b0001.

Затем B и C добавляют уникальные revision ID (`b_*`/`c_*`) от актуальной
базы. Слияние независимых heads — merge revision без переписывания
применённых файлов. Если новая миграция ссылается на таблицу соседа,
её `down_revision` включает необходимую ревизию. Перед merge B проверяет
единственный head, fresh upgrade и upgrade с прошлого M-этапа.
Механика описана в [Alembic](https://alembic.sqlalchemy.org/en/latest/branches.html).

Индексы: `(tenant_id, updated_at)`, `(tenant_id, status, priority)`,
`(tenant_id, conversation_id, seq)`, `(tenant_id, widget_id, customer_id)`;
для FTS — GIN. ANN-индексы только после измерений на M5; для учебного объёма
достаточно exact vector search после обязательных фильтров tenant/visibility.
