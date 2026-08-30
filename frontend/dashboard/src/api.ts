import { supabase } from './auth/supabase.ts';

/**
 * Every request this app makes.
 *
 * Relative `/api/...` — in dev a Vite proxy, in production a Vercel rewrite —
 * so the browser only ever sees one origin and CORS never applies. The same
 * trade `frontend/call` makes, and the reason `ALLOWED_ORIGINS` never has to be
 * right for either of them.
 *
 * The token is read per request rather than captured once, so a session that
 * refreshed in the background is used on the next call instead of the one that
 * was current when a component mounted.
 *
 * Reads were the whole of it until Phase 5a. `post` is dispatch — queueing an
 * interview and minting its link — and it is the same shape as `get` plus a
 * body, deliberately: one place decides how a token is attached and how a
 * refusal is unwrapped, and a second helper that got either of those subtly
 * different is a bug nobody would look for.
 */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function get<T>(path: string): Promise<T> {
  return send<T>('GET', path);
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  return send<T>('POST', path, body);
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;

  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    // `.error`, not `.detail` — `app.py`'s `_error_body` exists so that the
    // sentence the backend wrote is the sentence a person reads. A 403 here
    // says which address is not in `config.accounts`, and throwing that away
    // would turn the one useful refusal in the system into "forbidden".
    throw new ApiError(response.status, payload.error ?? `request failed (${response.status})`);
  }
  return (await response.json()) as T;
}
