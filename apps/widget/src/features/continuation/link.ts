export type LinkAction = "confirm" | "exchange";
export async function consumeLink<T>(href: string, replace: (safeHref: string) => void, exchange: (token: string, action: LinkAction) => Promise<T>): Promise<T> {
  const url = new URL(href); const fragment = new URLSearchParams(url.hash.slice(1));
  const token = fragment.get("token"); const action = fragment.get("action");
  url.hash = ""; replace(url.href);
  if (!token || (action !== "confirm" && action !== "exchange")) throw new Error("Некорректная ссылка");
  return exchange(token, action);
}
