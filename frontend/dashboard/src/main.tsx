import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { PublicConfig } from '@metafora/contracts';
import { App } from './App.tsx';
import { SignIn } from './SignIn.tsx';
import { initSupabase, loadConfig } from './auth/supabase.ts';
import { useSession } from './auth/useSession.ts';
import '@metafora/ui/tokens.css';
import './dashboard.css';

/**
 * Boot, in one order that cannot be reshuffled:
 *
 *   ① ask the backend where sign-in lives (`GET /config`)
 *   ② build the Supabase client from the answer
 *   ③ find out whether anyone is signed in, and whether they have a caseload
 *   ④ the app
 *
 * ① is why this is a gate and not a module constant: the project and the anon
 * key are handed down at runtime so that rotating them is a secret change, and
 * nothing that touches the client can render before it exists.
 */
function Boot() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    loadConfig()
      .then((value) => {
        initSupabase(value);
        setConfig(value);
      })
      .catch((error: Error) => setFailed(error.message));
  }, []);

  if (failed) {
    return (
      <div className="signin">
        <div className="signin__card">
          <span className="wordmark">
            metafora<span className="gradtext">.care</span>
          </span>
          <p className="signin__err">{failed}</p>
          <p>
            The backend is reachable but has no Supabase project configured, so there is nobody it
            could sign in. Set <code>SUPABASE_URL</code> and <code>SUPABASE_ANON_KEY</code> on the
            deployment.
          </p>
        </div>
      </div>
    );
  }

  if (!config) return <Waiting />;
  return <Gate />;
}

function Gate() {
  const { auth, signOut } = useSession();

  if (auth.state === 'loading') return <Waiting />;
  if (auth.state === 'anonymous') return <SignIn />;
  if (auth.state === 'refused') return <SignIn refused={auth.reason} />;
  return <App account={auth.account} onSignOut={signOut} />;
}

function Waiting() {
  return (
    <div className="signin">
      <p className="note">Signing in…</p>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Boot />
  </StrictMode>,
);
