'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/client';
import type { Machine, Member, Permission } from '@/lib/types';

export default function MembersClient() {
  const [members, setMembers] = useState<Member[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [showInactive, setShowInactive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState('');
  const [newUid, setNewUid] = useState('');

  const load = useCallback(async () => {
    const supabase = createClient();
    const [membersRes, machinesRes, permsRes] = await Promise.all([
      supabase.from('members').select('*').order('name'),
      supabase.from('machines').select('*').order('display_name'),
      supabase.from('permissions').select('*'),
    ]);
    const err = membersRes.error || machinesRes.error || permsRes.error;
    if (err) {
      setError(err.message);
    } else {
      setError(null);
      setMembers((membersRes.data ?? []) as Member[]);
      setMachines((machinesRes.data ?? []) as Machine[]);
      setPermissions((permsRes.data ?? []) as Permission[]);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function hasPermission(memberId: string, machineId: string) {
    return permissions.some(
      (p) => p.member_id === memberId && p.machine_id === machineId
    );
  }

  async function togglePermission(memberId: string, machineId: string) {
    const supabase = createClient();
    setBusy(true);
    const granted = hasPermission(memberId, machineId);
    const { error } = granted
      ? await supabase
          .from('permissions')
          .delete()
          .eq('member_id', memberId)
          .eq('machine_id', machineId)
      : await supabase
          .from('permissions')
          .insert({ member_id: memberId, machine_id: machineId });
    if (error) setError(error.message);
    await load();
    setBusy(false);
  }

  async function addMember(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    const uid = newUid.trim();
    if (!name) return;
    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase
      .from('members')
      .insert({ name, rfid_uid: uid || null });
    if (error) {
      setError(
        error.code === '23505'
          ? `That card (${uid}) is already assigned to another member.`
          : error.message
      );
    } else {
      setError(null);
      setNewName('');
      setNewUid('');
    }
    await load();
    setBusy(false);
  }

  /** Soft delete: keeps session history and lets a re-enroll restore them. */
  async function deactivate(member: Member) {
    if (!confirm(`Deactivate ${member.name}? Their session history is kept.`))
      return;
    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase
      .from('members')
      .update({ active: false })
      .eq('id', member.id);
    if (error) setError(error.message);
    await load();
    setBusy(false);
  }

  async function reactivate(member: Member) {
    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase
      .from('members')
      .update({ active: true })
      .eq('id', member.id);
    if (error) setError(error.message);
    await load();
    setBusy(false);
  }

  if (!loaded) return <div className="empty">Loading…</div>;

  const visible = members.filter((m) => showInactive || m.active);

  return (
    <>
      <div className="page-header">
        <h1>Members</h1>
        <Link href="/enroll" className="btn btn-primary">
          Enroll by tapping a card
        </Link>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <h3 className="card-title">Add a member manually</h3>
        <p className="card-hint">
          Leave the card blank to add someone now and assign their badge later
          via <Link href="/enroll">Enroll</Link>.
        </p>
        <form onSubmit={addMember}>
          <div className="form-row">
            <div>
              <label htmlFor="name">Name</label>
              <input
                id="name"
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Alex Smith"
                required
              />
            </div>
            <div>
              <label htmlFor="uid">Card ID (optional)</label>
              <input
                id="uid"
                type="text"
                value={newUid}
                onChange={(e) => setNewUid(e.target.value)}
                placeholder="e.g. 043f6afba2"
                className="mono"
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Add member
            </button>
          </div>
        </form>
      </div>

      <div className="card">
        <div className="row-between" style={{ marginBottom: 14 }}>
          <h3 style={{ fontSize: 16 }}>
            {visible.length} member{visible.length === 1 ? '' : 's'}
          </h3>
          <label
            style={{
              textTransform: 'none',
              fontSize: 13,
              fontWeight: 500,
              margin: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              style={{ width: 'auto' }}
            />
            Show deactivated
          </label>
        </div>

        {visible.length === 0 ? (
          <div className="empty">No members yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Card ID</th>
                  {machines.map((m) => (
                    <th key={m.id}>{m.display_name}</th>
                  ))}
                  <th />
                </tr>
              </thead>
              <tbody>
                {visible.map((member) => (
                  <tr key={member.id}>
                    <td>
                      <strong>{member.name}</strong>
                      {!member.active && (
                        <>
                          {' '}
                          <span className="badge muted">deactivated</span>
                        </>
                      )}
                    </td>
                    <td className="mono">
                      {member.rfid_uid ?? (
                        <span className="badge warn">no card</span>
                      )}
                    </td>
                    {machines.map((machine) => {
                      const granted = hasPermission(member.id, machine.id);
                      return (
                        <td key={machine.id}>
                          <button
                            className={`btn btn-sm ${granted ? '' : 'btn-primary'}`}
                            disabled={busy || !member.active}
                            onClick={() =>
                              togglePermission(member.id, machine.id)
                            }
                            title={
                              member.active
                                ? granted
                                  ? 'Revoke access'
                                  : 'Grant access'
                                : 'Reactivate the member first'
                            }
                          >
                            {granted ? 'Revoke' : 'Grant'}
                          </button>
                        </td>
                      );
                    })}
                    <td>
                      <div className="actions">
                        {member.active ? (
                          <button
                            className="btn btn-sm btn-danger"
                            disabled={busy}
                            onClick={() => deactivate(member)}
                          >
                            Deactivate
                          </button>
                        ) : (
                          <button
                            className="btn btn-sm"
                            disabled={busy}
                            onClick={() => reactivate(member)}
                          >
                            Reactivate
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
