export function createPoller(update: () => void) {
  let timer: ReturnType<typeof setInterval> | undefined;
  return { start() { if (!timer) timer = setInterval(update, 3000); }, stop() { if (timer) clearInterval(timer); timer = undefined; } };
}

export function createSender(send: (text: string, messageId: string) => Promise<void>) {
  let busy = false;
  let retry: { text: string; id: string } | undefined;
  return { async submit(text: string) {
    if (busy || !text.trim()) return;
    busy = true;
    const item = retry?.text === text ? retry : { text, id: crypto.randomUUID() };
    try { await send(item.text, item.id); retry = undefined; }
    catch (error) { retry = item; throw error; }
    finally { busy = false; }
  } };
}
