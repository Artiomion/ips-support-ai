import { useEffect, useState } from "react";
import type { components } from "../../../../../packages/api-client/src/generated";

type Metrics = components["schemas"]["Overview"];

export function Overview({ load }: { load: () => Promise<Metrics> }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setMetrics(null);
    setError(false);
    void load().then((data) => { if (current) setMetrics(data); }, () => { if (current) setError(true); });
    return () => { current = false; };
  }, [load, attempt]);

  const time = (seconds: number | null) => seconds === null ? "Нет данных" : `${Math.round(seconds)} с`;
  return <section aria-label="Обзор показателей">
    <h2>Обзор показателей</h2>
    {!metrics && !error && <p role="status">Загружаем показатели…</p>}
    {error && <div role="alert">Не удалось загрузить показатели. <button type="button" onClick={() => setAttempt((value) => value + 1)}>Повторить</button></div>}
    {metrics && <dl>
      <dt>Диалоги</dt><dd>{metrics.conversations}</dd>
      <dt>Заявки</dt><dd>{metrics.tickets}</dd>
      <dt>Решено с ИИ и подтверждено клиентом</dt><dd>{metrics.ai_confirmed}</dd>
      <dt>Результат не подтверждён</dt><dd>{metrics.unconfirmed}</dd>
      <dt>Передано специалисту</dt><dd>{metrics.handoffs}</dd>
      <dt>Среднее время первого ответа</dt><dd>{time(metrics.first_response_seconds_avg)}</dd>
      <dt>Среднее время решения</dt><dd>{time(metrics.resolution_seconds_avg)}</dd>
    </dl>}
  </section>;
}
