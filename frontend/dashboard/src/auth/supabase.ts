import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import type { PublicConfig } from '@metafora/contracts';

/**
 * The Supabase client, created from what the backend hands down.
 *
 * There is no `VITE_SUPABASE_URL` in this app and there deliberately is not
 * one: `GET /config` carries the project and the anon key, so this bundle holds
 * exactly one piece of configuration — where `/api` goes, which is a Vercel
 * rewrite and not code. Rotating the anon key is then `fly secrets set` rather
 * than a rebuild of a static site.
 *
 * Which is why the client cannot be a module constant: it does not exist until
 * a fetch has come back. `main.tsx` boots through `loadConfig` before rendering
 * anything that could reach for it.
 */

let client: SupabaseClient | null = null;

/** The backend's answer when it has no project configured. */
export class NoSignIn extends Error {}

export async function loadConfig(): Promise<PublicConfig> {
  const response = await fetch('/api/config');
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // `app.py`'s error body, which every refusal in this product shares.
    throw new NoSignIn(body.error ?? 'this server has no sign-in configured');
  }
  return body as PublicConfig;
}

export function initSupabase(config: PublicConfig): SupabaseClient {
  client = createClient(config.supabaseUrl, config.supabaseAnonKey, {
    auth: {
      // The two settings this dependency was chosen for. A hand-rolled client
      // signs in fine and then 401s an hour later, which is the bug you find
      // in front of someone.
      persistSession: true,
      autoRefreshToken: true,
    },
  });
  return client;
}

export function supabase(): SupabaseClient {
  if (!client) throw new Error('supabase client used before loadConfig');
  return client;
}
