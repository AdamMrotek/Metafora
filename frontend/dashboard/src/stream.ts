import { supabase } from './auth/supabase.ts';

// `EventSource` cannot carry an `Authorization` header, so this reads
// `GET /interviews/stream` as a `fetch` + `ReadableStream` instead.

const MIN_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 15_000;

export function subscribeToEscalations(onNudge: () => void): () => void {
  let stopped = false;
  let controller: AbortController | null = null;

  void connect();

  async function connect() {
    let backoff = MIN_BACKOFF_MS;
    while (!stopped) {
      controller = new AbortController();
      try {
        await consume(controller.signal, onNudge);
        backoff = MIN_BACKOFF_MS;
      } catch {
        // refused, dropped, network — all reconnect the same way
      }
      if (stopped) return;
      await sleep(backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    }
  }

  return () => {
    stopped = true;
    controller?.abort();
  };
}

async function consume(signal: AbortSignal, onNudge: () => void): Promise<void> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  const response = await fetch('/api/interviews/stream', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream refused (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';
    for (const event of events) {
      if (event.startsWith('data:')) onNudge();
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
