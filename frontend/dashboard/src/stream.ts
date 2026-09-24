import { useEffect, useRef } from 'react';
import { supabase } from './auth/supabase.ts';

// `EventSource` cannot carry an `Authorization` header, so this reads
// `GET /interviews/stream` as a `fetch` + `ReadableStream` instead.

const MIN_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 15_000;

// The server sends a `: keep-alive` comment every 15s (HEARTBEAT_S in
// services/core/sse.py). No byte in over twice that means the connection
// died without either side finding out — `reader.read()` would otherwise
// hang forever, and a tab left open all day would stop getting escalations
// with nothing ever throwing to trigger a reconnect.
const STALE_MS = 35_000;

// Independent of the stream's own health: if a red flag ever reaches an open
// dashboard only through this poll, that is a stream bug to fix, but a
// clinician still sees it within 30s rather than not at all for as long as
// the tab happens to stay open.
const BACKSTOP_POLL_MS = 30_000;

/** Subscribes for the life of the calling component; call `onNudge` on every
 *  push and every backstop tick. Caller decides what a nudge means.
 *
 *  Subscribes once (a fresh inline callback each render must not tear down
 *  and reopen the connection) but always calls the latest `onNudge` — a ref,
 *  not a dependency. */
export function useEscalationStream(onNudge: () => void): void {
  const nudgeRef = useRef(onNudge);
  nudgeRef.current = onNudge;

  useEffect(() => subscribeToEscalations(() => nudgeRef.current()), []);
  useEffect(() => {
    const interval = setInterval(() => nudgeRef.current(), BACKSTOP_POLL_MS);
    return () => clearInterval(interval);
  }, []);
}

function subscribeToEscalations(onNudge: () => void): () => void {
  let stopped = false;
  let controller: AbortController | null = null;

  void connect();

  async function connect() {
    let backoff = MIN_BACKOFF_MS;
    while (!stopped) {
      controller = new AbortController();
      try {
        await consume(controller, onNudge);
        backoff = MIN_BACKOFF_MS;
      } catch {
        // refused, dropped, stale, network — all reconnect the same way
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

async function consume(controller: AbortController, onNudge: () => void): Promise<void> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;
  const response = await fetch('/api/interviews/stream', {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal: controller.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`stream refused (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // Aborting (rather than just throwing client-side) actually tears down the
  // connection, so the server's `request.is_disconnected()` check notices and
  // frees the broadcaster subscription instead of leaking it.
  let watchdog = setTimeout(() => controller.abort(), STALE_MS);
  try {
    for (;;) {
      const { done, value } = await reader.read();
      clearTimeout(watchdog);
      if (done) return;
      watchdog = setTimeout(() => controller.abort(), STALE_MS);
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? '';
      for (const event of events) {
        if (event.startsWith('data:')) onNudge();
      }
    }
  } finally {
    clearTimeout(watchdog);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
