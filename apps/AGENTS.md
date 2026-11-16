# Интерфейсы: A

- `web/` — React-панель; `widget/` — React iframe и JS loader; оба принадлежат A.
- TypeScript strict, Vite, React Router, TanStack Query; без SSR.
- API-типы генерировать из `contracts/openapi.json`; не писать альтернативные DTO.
- Сначала режим MSW по тем же DTO, затем реальный API без переписывания экранов.
- Session сотрудника в HttpOnly cookie; customer bearer только для своего клиента.
  Не помещать служебные секреты в bundle, localStorage или postMessage.
- Ответы ИИ отображать безопасным Markdown без raw HTML.
- Widget loader проверяет origin postMessage. Размер iframe не перекрывает сайт
  после закрытия; фокус, Escape, tab navigation проверяются в A06.
- UI не обещает доставку письма, ИИ или загрузку файла, пока API их не подтвердил.
- До A01 npm-команд ещё нет: см. `docs/verification.md`.
