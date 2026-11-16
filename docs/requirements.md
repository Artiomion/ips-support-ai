# Соответствие исходному ТЗ

Источник — неизменённый `docs/product-spec.md`. Ядро M0–M4 и расширение M5
не покрывают целиком будущую SaaS-версию; отсрочки перечислены явно.

| Раздел ТЗ / требование | Карточки | Объём и проверка |
|---|---|---|
| 1: клиент → ИИ → человек → решение | A04/A05/A07/A08, B04/B05/B08, C05/C07 | Сквозной M4 |
| 2: организации, роли, самостоятельная регистрация | A03/A06, B02/B03 | Ядро, negative tenant/RBAC tests |
| 2: приглашения по email | A06, B03 | Ядро, SMTP sandbox; реальный delivery требует SMTP |
| 2: суперадмин без свободного доступа к перепискам | B02 + B10/A10 | Guard в ядре, полноценный aggregate UI в M5 |
| 3: несколько widgets, branding, origins, разделы знаний | A04/A06, B03, C03/C04 | Ядро, exact origin и scope tests |
| 3: CMS integrations | — | Развитие после M5 |
| 4: signed user, anonymous, verified email | A04/A07, B03/B07, C02 | Ядро; device persistence при доступном storage |
| 4: письмо и защищённая ссылка, без inbound email | A07, B07 | Ядро, fragment exchange/replay tests |
| 5: отдельные диалоги, одна активная заявка, статусы | A04/A05, B04, C02 | Ядро, DB unique + transitions |
| 5: confirm/reopen | A07, B04, C07 | Ядро |
| 5: автоматическое закрытие по настройке | B09 | M5, injected clock |
| 6: текст, уточнения, known/unknown, citations, handoff | C04/C05/C06, A04/A07 | Ядро; certainty не трактуется как достоверная вероятность |
| 6: категории и срочность | C04/C05, B04/B05 | Ядро: предложение ИИ валидируется сервером |
| 6: изображения и screenshots | C09, A09 | M5, при vision-capable provider |
| 6: общие знания опциональны, политика не выдумывается | C04/C06, A06 | Ядро: off по умолчанию; no evidence→уточнение/handoff |
| 6: после подключения человека ИИ молчит | C05, B04, A05 | Ядро, race test |
| 7: specialization, availability, load, claim/reassign | B03/B05, A05 | Ядро; row lock и max_active tests |
| 7: полный график и SLA routing | B09/B10, A10 | M5: простой weekly calendar + SLA; отпуска/сложные смены позже |
| 7: нет специалиста → очередь и ожидание | B05, A07 | Ядро |
| 8: inbox, фильтры, переписка, notes, данные клиента | A05, B04, C02 | Ядро; только доступные данные, без enrichment |
| 8: AI summary/drafts/отдельный staff assistant | C10, A09 | M5; в ядре история, summary не обещается |
| 9: ручные статьи и TXT | C03, A06 | Ядро |
| 9: PDF/DOCX | C09, A09 | M5; скан PDF без текста требует будущий OCR |
| 9: public/internal, source/history/restore/disable | C03/C04/C06, A06 | Ядро |
| 9: обучение из resolved, dedup, удаление ПД | C10, A09 | M5: redaction + review; не обучение весов |
| 9: выбор auto publish/review | C10 | В M5 только review; автоматический режим отложен отдельно |
| 9: сомнительные инструкции всегда review | C10, A09 | M5; auto publication пока вообще выключена |
| 9: crawling сайтов | — | Не требуется по ТЗ, не реализовывать |
| 10: counts, AI outcomes, handoffs, время, widgets, rating | B07, C07, A08 | Ядро, определения в contracts/verification |
| 10: SLA, нагрузка, категории, эффективность KB | B10/C10, A10 | M5; KB usage/citations, не выдуманная causal effectiveness |
| 10: массовые инциденты | B10/A10 | M5: candidates по эвристике, полноценный ML позже |
| 10: complaint и проверка использованных знаний | C10/A10 | M5, snapshot revisions сохраняется |
| 11: стиль/язык, источники, fallback | B06, C06, A06 | Ядро, минимум настроек |
| 11: права, tenant, private assets, origins, spam | B02/B03/B06, C06/C09 | Ядро; attachments M5 |
| 11: retention, расписания, SLA | B09 | M5; до этого удалить demo data можно reset только тестового стенда |
| 11: audit, AI quotas, отказ провайдера | B06/C05/A07 | Ядро |
| 11: keyboard/a11y | A06/A08 | Ядро, фокус и клавиатура |
| 12: staff/widget/AI limits | B06 | Ядро |
| 12: тарифы/knowledge volume limits | B10/C09 | M5, без списаний |
| 12: оплата/подписки | — | Не входят в первоначальную версию |
| 13–14: этапы, проверки, масштабирование | Все M-этапы, risks/verification | План; не обещание production readiness |

При изменении объёма менять эту таблицу, карточку, контракт и UI-capabilities
одним согласованным набором PR. Недоступная расширенная функция не должна
отображаться пользователю как работающая.
