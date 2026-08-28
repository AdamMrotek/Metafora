import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import type { Account } from '@metafora/contracts';
import { ApiError, get } from '../api.ts';
import { supabase } from './supabase.ts';

/**
 * Signed in, and named in `config.accounts`. Two different facts, in that
 * order, and the second is the one that decides whether this app has anything
 * to show.
 *
 * Supabase will sign anyone in — it is a public project. `GET /me` is what
 * turns a verified token into a caseload, and a 403 from it is not an error to
 * retry but a sentence to render: *your address is not on the list*. So the
 * state here is a small machine rather than a boolean.
 */

export type Auth =
  | { state: 'loading' }
  | { state: 'anonymous' }
  | { state: 'refused'; reason: string }
  | { state: 'in'; session: Session; account: Account };

export function useSession(): { auth: Auth; signOut: () => Promise<void> } {
  const [auth, setAuth] = useState<Auth>({ state: 'loading' });

  useEffect(() => {
    let live = true;

    async function settle(session: Session | null) {
      if (!session) {
        if (live) setAuth({ state: 'anonymous' });
        return;
      }
      try {
        const account = await get<Account>('/me');
        if (live) setAuth({ state: 'in', session, account });
      } catch (error) {
        if (!live) return;
        // 401 means the token did not verify after all — nothing to say to the
        // person beyond "sign in". Anything else came with a reason, and the
        // reason is the whole point of `deps.py` sending one.
        if (error instanceof ApiError && error.status === 401) {
          setAuth({ state: 'anonymous' });
        } else {
          setAuth({ state: 'refused', reason: (error as Error).message });
        }
      }
    }

    supabase()
      .auth.getSession()
      .then(({ data }) => settle(data.session));

    // Fires on sign-in, sign-out and every silent refresh, which is what keeps
    // this from being a snapshot taken once at boot.
    const { data: sub } = supabase().auth.onAuthStateChange((_event, session) => {
      settle(session);
    });

    return () => {
      live = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  return {
    auth,
    signOut: async () => {
      await supabase().auth.signOut();
    },
  };
}
