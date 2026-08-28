import { useState } from 'react';
import { supabase } from './auth/supabase.ts';

/**
 * The only screen with no data behind it.
 *
 * There is no sign-up link and there will not be one. Verifying a token proves
 * someone controls a mailbox; `config.accounts` — seeded by a migration, never
 * by this application — is what turns that into a caseload. On a public demo
 * URL, making those the same act would hand a stranger a dashboard, so the
 * sentence under the form says so rather than leaving it to be discovered by a
 * 403.
 */
export function SignIn({ refused }: { refused?: string }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const { error: failed } = await supabase().auth.signInWithPassword({ email, password });
    if (failed) setError(failed.message);
    setBusy(false);
  }

  return (
    <div className="signin">
      <div className="signin__card">
        <span className="wordmark">
          metafora<span className="gradtext">.care</span>
        </span>

        {refused ? (
          // Verified, and named nowhere. The reason came from `deps.py` and is
          // rendered as it was written: it is the only refusal in this system
          // that tells the caller something useful.
          <>
            <p className="signin__err">{refused}</p>
            <p>
              Your sign-in worked; the address is not on this deployment's list of clinicians.
              Accounts are granted by a migration, not by signing up.
            </p>
            <button
              className="btn-grad"
              type="button"
              onClick={() => supabase().auth.signOut()}
            >
              Sign in as someone else
            </button>
          </>
        ) : (
          <>
            <form className="signin__f" onSubmit={submit}>
              <div>
                <label htmlFor="email">Email</label>
                <input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="password">Password</label>
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {error && <p className="signin__err">{error}</p>}
              <button className="btn-grad" type="submit" disabled={busy}>
                {busy ? 'Signing in…' : 'Sign in'}
              </button>
            </form>
            <p>
              Clinician accounts are seeded, not self-serve — signing up to the project is not the
              same act as being granted a caseload.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
