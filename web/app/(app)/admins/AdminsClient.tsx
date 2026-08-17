'use client';

import { useCallback, useEffect, useState } from 'react';
import { createClient } from '@/lib/supabase/client';
import { formatDateTime } from '@/lib/format';
import type { Admin, OrphanAccount } from '@/lib/types';

export default function AdminsClient() {
  const [admins, setAdmins] = useState<Admin[]>([]);
  const [orphans, setOrphans] = useState<OrphanAccount[]>([]);
  const [myUserId, setMyUserId] = useState<string | null>(null);
  const [canCreateAccounts, setCanCreateAccounts] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  // Single "add an admin" form
  const [newEmail, setNewEmail] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newNote, setNewNote] = useState('');

  // "Your account" — change own password
  const [myEmail, setMyEmail] = useState<string | null>(null);
  const [pw1, setPw1] = useState('');
  const [pw2, setPw2] = useState('');
  const [pwNotice, setPwNotice] = useState<string | null>(null);
  const [pwError, setPwError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const supabase = createClient();
    const [{ data: userData }, adminsRes, orphansRes] = await Promise.all([
      supabase.auth.getUser(),
      supabase.from('admins').select('*').order('added_at'),
      supabase.rpc('list_orphan_accounts'),
    ]);
    setMyUserId(userData.user?.id ?? null);
    setMyEmail(userData.user?.email ?? null);
    if (adminsRes.error) setError(adminsRes.error.message);
    else {
      setError(null);
      setAdmins((adminsRes.data ?? []) as Admin[]);
    }
    // Missing function just means migration 0003 hasn't been applied yet;
    // don't turn that into a page-level error.
    setOrphans(
      orphansRes.error ? [] : ((orphansRes.data ?? []) as OrphanAccount[])
    );

    try {
      const res = await fetch('/api/admins/create');
      const json = await res.json();
      setCanCreateAccounts(Boolean(json.canCreateAccounts));
    } catch {
      setCanCreateAccounts(false);
    }

    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /**
   * One action for "make this person an admin". Creates the account when the
   * server has an elevated key; otherwise falls back to the grant_admin()
   * database function, which can only allowlist accounts that already exist.
   */
  async function addAdmin(e: React.FormEvent) {
    e.preventDefault();
    const email = newEmail.trim();
    if (!email) return;

    setBusy(true);
    setError(null);
    setNotice(null);

    if (canCreateAccounts) {
      const res = await fetch('/api/admins/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password: newPassword,
          note: newNote.trim(),
        }),
      });
      const json = await res.json();

      if (!res.ok) {
        setError(json.error ?? 'Could not add the admin.');
      } else {
        setNotice(
          json.created
            ? `Created an account for ${json.email} and granted admin access. ` +
              `Give them the password directly — it isn't emailed.`
            : `${json.email} already had an account and now has admin access.`
        );
        setNewEmail('');
        setNewPassword('');
        setNewNote('');
      }
    } else {
      const supabase = createClient();
      const { error } = await supabase.rpc('grant_admin', {
        target_email: email,
        target_note: newNote.trim() || null,
      });
      if (error) setError(error.message);
      else {
        setNotice(`${email} now has admin access.`);
        setNewEmail('');
        setNewNote('');
      }
    }

    await load();
    setBusy(false);
  }

  async function revoke(admin: Admin) {
    const self = admin.user_id === myUserId;
    const message = self
      ? 'Remove your OWN admin access? You will be locked out of this app immediately.'
      : `Remove admin access for ${admin.email}?`;
    if (!confirm(message)) return;

    setBusy(true);
    setError(null);
    setNotice(null);

    const supabase = createClient();
    const { error } = await supabase
      .from('admins')
      .delete()
      .eq('user_id', admin.user_id);

    if (error) {
      // The database refuses to let the allowlist reach zero.
      setError(
        /last admin/i.test(error.message)
          ? 'Cannot remove the last admin — add another one first.'
          : error.message
      );
      setBusy(false);
      await load();
      return;
    }

    setNotice(`Revoked admin access for ${admin.email}.`);
    if (self) {
      window.location.href = '/';
      return;
    }
    await load();
    setBusy(false);
  }

  /** Change your own password. Anyone handed a starter password can rotate it. */
  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setPwError(null);
    setPwNotice(null);

    if (pw1.length < 10) {
      setPwError('Use at least 10 characters.');
      return;
    }
    if (pw1 !== pw2) {
      setPwError('The two passwords do not match.');
      return;
    }

    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase.auth.updateUser({ password: pw1 });
    if (error) setPwError(error.message);
    else {
      setPwNotice('Password updated.');
      setPw1('');
      setPw2('');
    }
    setBusy(false);
  }

  async function grantOrphan(orphan: OrphanAccount) {
    setBusy(true);
    setError(null);
    setNotice(null);
    const supabase = createClient();
    const { error } = await supabase.rpc('grant_admin', {
      target_email: orphan.email,
      target_note: null,
    });
    if (error) setError(error.message);
    else setNotice(`${orphan.email} now has admin access.`);
    await load();
    setBusy(false);
  }

  async function deleteOrphan(orphan: OrphanAccount) {
    if (
      !confirm(
        `Permanently delete the account ${orphan.email}? This cannot be undone.`
      )
    )
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const supabase = createClient();
    const { error } = await supabase.rpc('delete_account', {
      target_user_id: orphan.user_id,
    });
    if (error) setError(error.message);
    else setNotice(`Deleted the account ${orphan.email}.`);
    await load();
    setBusy(false);
  }

  if (!loaded) return <div className="empty">Loading…</div>;

  return (
    <>
      <div className="page-header">
        <h1>Admins</h1>
      </div>

      {error && <div className="error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      {orphans.length > 0 && (
        <div className="card" style={{ borderColor: 'var(--warning)' }}>
          <h3 className="card-title">
            <span className="badge warn">attention</span>{' '}
            {orphans.length} account{orphans.length === 1 ? '' : 's'} without
            admin access
          </h3>
          <p className="card-hint">
            Every account on this project is meant to be an admin. These can
            sign in but see &ldquo;no access&rdquo; — most likely added straight
            in the Supabase dashboard, or signed up before sign-ups were turned
            off. Grant them access or delete them.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Created</th>
                  <th>Last sign-in</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {orphans.map((o) => (
                  <tr key={o.user_id}>
                    <td>
                      <strong>{o.email}</strong>
                    </td>
                    <td>{formatDateTime(o.created_at)}</td>
                    <td>
                      {o.last_sign_in_at ? (
                        formatDateTime(o.last_sign_in_at)
                      ) : (
                        <span className="muted">never</span>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        <button
                          className="btn btn-sm btn-primary"
                          disabled={busy}
                          onClick={() => grantOrphan(o)}
                        >
                          Grant admin
                        </button>
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={busy}
                          onClick={() => deleteOrphan(o)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-title">
          {admins.length} admin{admins.length === 1 ? '' : 's'}
          {orphans.length === 0 && (
            <>
              {' '}
              <span className="badge ok">all accounts allowlisted</span>
            </>
          )}
        </h3>
        <p className="card-hint">
          Only these accounts can use this app. Everyone else who signs in sees
          &ldquo;no access&rdquo;.
        </p>
        {admins.length === 0 ? (
          <div className="empty">No admins found.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Note</th>
                  <th>Added</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {admins.map((a) => (
                  <tr key={a.user_id}>
                    <td>
                      <strong>{a.email}</strong>
                      {a.user_id === myUserId && (
                        <>
                          {' '}
                          <span className="badge accent">you</span>
                        </>
                      )}
                    </td>
                    <td className="muted">{a.note || '—'}</td>
                    <td>{formatDateTime(a.added_at)}</td>
                    <td>
                      <div className="actions">
                        <button
                          className="btn btn-sm btn-danger"
                          disabled={busy || admins.length <= 1}
                          onClick={() => revoke(a)}
                          title={
                            admins.length <= 1
                              ? 'Cannot remove the last admin'
                              : 'Revoke admin access'
                          }
                        >
                          Revoke
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <h3 className="card-title">Add an admin</h3>
        {canCreateAccounts ? (
          <p className="card-hint">
            Enter their email. If they don&apos;t have an account yet one is
            created with the starter password you set — hand it over directly,
            it is <strong>not emailed</strong>, and they can change it once
            signed in. If they already have an account it&apos;s simply granted
            access.
          </p>
        ) : (
          <p className="card-hint">
            This deployment can only grant access to accounts that already
            exist. To create accounts from here, set{' '}
            <code className="mono">SUPABASE_SERVICE_ROLE_KEY</code> in Vercel.
            Otherwise add the person under Authentication → Users in the Supabase
            dashboard first, then enter their email below.
          </p>
        )}
        <form onSubmit={addAdmin}>
          <div className="field">
            <label htmlFor="new-email">Email</label>
            <input
              id="new-email"
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="person@example.com"
              required
            />
          </div>
          {canCreateAccounts && (
            <div className="field">
              <label htmlFor="new-password">
                Starter password (10+ characters, only needed for a new account)
              </label>
              <input
                id="new-password"
                type="text"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="mono"
                placeholder="e.g. shop-laser-2026"
              />
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                They can change it themselves under &ldquo;Your account&rdquo;
                after signing in.
              </div>
            </div>
          )}
          <div className="field">
            <label htmlFor="new-note">Note (optional)</label>
            <input
              id="new-note"
              type="text"
              value={newNote}
              onChange={(e) => setNewNote(e.target.value)}
              placeholder="e.g. shop manager"
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Add admin
          </button>
        </form>
      </div>

      <div className="card">
        <h3 className="card-title">Your account</h3>
        <p className="card-hint">
          Signed in as <strong>{myEmail ?? '—'}</strong>. Change your password
          here — worth doing right away if someone handed you a starter one.
        </p>

        {pwError && <div className="error">{pwError}</div>}
        {pwNotice && <div className="notice">{pwNotice}</div>}

        <form onSubmit={changePassword}>
          <div className="form-row">
            <div>
              <label htmlFor="pw1">New password (10+ characters)</label>
              <input
                id="pw1"
                type="password"
                value={pw1}
                onChange={(e) => setPw1(e.target.value)}
                autoComplete="new-password"
                required
              />
            </div>
            <div>
              <label htmlFor="pw2">Confirm</label>
              <input
                id="pw2"
                type="password"
                value={pw2}
                onChange={(e) => setPw2(e.target.value)}
                autoComplete="new-password"
                required
              />
            </div>
            <button type="submit" className="btn" disabled={busy}>
              Change password
            </button>
          </div>
        </form>
      </div>
    </>
  );
}
