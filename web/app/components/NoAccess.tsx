'use client';

import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';

/** Shown to a signed-in user who isn't on the admin allowlist. */
export default function NoAccess({ email }: { email?: string }) {
  const router = useRouter();

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push('/login');
    router.refresh();
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/rev-logo.svg"
          alt="Rev Ithaca Startup Works"
          className="login-logo"
        />
        <h1 style={{ fontSize: 20, marginBottom: 6 }}>No access</h1>
        <p className="card-hint">
          {email ? <strong>{email}</strong> : 'This account'} is signed in but
          isn&apos;t an administrator of this Fabman install. Ask an existing
          admin to grant access.
        </p>
        <button className="btn btn-block" onClick={signOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}
