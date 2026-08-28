import { supabase } from './auth/supabase.ts';

/**
 * Every read this app makes.
 *
 * Relative `/api/...` — in dev a Vite proxy, in production a Vercel rewrite —
 * so the browser only ever sees one origin and CORS never applies. The same
 * trade `frontend/call` makes, and the reason `ALLOWED_ORIGINS` never has to be
 * right for either of them.
 *
 * The token is read per request rather than captured once, so a session that
 * refreshed in the background is used on the next call instead of the one that
 * was current when a component mounted.
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
  const { data } = await supabase().auth.getSession();
  const token = data.session?.access_token;

  const response = await fetch(`/api${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // `.error`, not `.detail` — `app.py`'s `_error_body` exists so that the
    // sentence the backend wrote is the sentence a person reads. A 403 here
    // says which address is not in `config.accounts`, and throwing that away
    // would turn the one useful refusal in the system into "forbidden".
    throw new ApiError(response.status, body.error ?? `request failed (${response.status})`);
  }
  return (await response.json()) as T;
}
