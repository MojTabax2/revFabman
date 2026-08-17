'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';
import { isBridgeOnline } from '@/lib/types';
import type { Bridge, Machine, Member, PendingEnrollment } from '@/lib/types';

type Phase = 'form' | 'waiting' | 'captured' | 'expired';

export default function EnrollClient() {
  const router = useRouter();
  const [machines, setMachines] = useState<Machine[]>([]);
  const [bridges, setBridges] = useState<Bridge[]>([]);
  const [cardlessMembers, setCardlessMembers] = useState<Member[]>([]);
  const [machineId, setMachineId] = useState('');
  const [name, setName] = useState('');
  const [existingMemberId, setExistingMemberId] = useState('');
  const [phase, setPhase] = useState<Phase>('form');
  const [pending, setPending] = useState<PendingEnrollment | null>(null);
  const [capturedUid, setCapturedUid] = useState<string | null>(null);
  const [remaining, setRemaining] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    (async () => {
      const supabase = createClient();
      const [machinesRes, bridgesRes, membersRes] = await Promise.all([
        supabase.from('machines').select('*').eq('enabled', true).order('display_name'),
        supabase.from('bridges').select('*'),
        supabase
          .from('members')
          .select('*')
          .is('rfid_uid', null)
          .eq('active', true)
          .order('name'),
      ]);
      const err = machinesRes.error || bridgesRes.error || membersRes.error;
      if (err) {
        setError(err.message);
        return;
      }
      const ms = (machinesRes.data ?? []) as Machine[];
      setMachines(ms);
      setBridges((bridgesRes.data ?? []) as Bridge[]);
      setCardlessMembers((membersRes.data ?? []) as Member[]);
      if (ms.length > 0) setMachineId(ms[0].id);
    })();
  }, []);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    const usingExisting = Boolean(existingMemberId);
    if (!machineId || (!trimmed && !usingExisting)) return;

    setBusy(true);
    setError(null);

    const supabase = createClient();
    const machine = machines.find((m) => m.id === machineId)!;
    const windowSeconds = machine.enrollment_seconds;
    const expiresAt = new Date(Date.now() + windowSeconds * 1000).toISOString();

    const memberName = usingExisting
      ? cardlessMembers.find((m) => m.id === existingMemberId)?.name ?? trimmed
      : trimmed;

    // Cancel any earlier pending request for this machine so the bridge only
    // ever sees one.
    await supabase
      .from('pending_enrollments')
      .update({ status: 'canceled' })
      .eq('machine_id', machineId)
      .eq('status', 'pending');

    const { data, error } = await supabase
      .from('pending_enrollments')
      .insert({
        machine_id: machineId,
        member_name: memberName,
        member_id: usingExisting ? existingMemberId : null,
        expires_at: expiresAt,
      })
      .select()
      .single();

    if (error || !data) {
      setError(error?.message ?? 'Could not start enrollment.');
      setBusy(false);
      return;
    }

    setPending(data as PendingEnrollment);
    setRemaining(windowSeconds);
    setPhase('waiting');
    setBusy(false);
    beginPolling((data as PendingEnrollment).id, windowSeconds);
  }

  function beginPolling(pendingId: string, windowSeconds: number) {
    const deadline = Date.now() + windowSeconds * 1000;
    stopPolling();
    pollRef.current = setInterval(async () => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      setRemaining(left);

      const supabase = createClient();
      const { data } = await supabase
        .from('pending_enrollments')
        .select('*')
        .eq('id', pendingId)
        .single();

      const row = data as PendingEnrollment | null;
      if (row?.status === 'captured' && row.captured_uid) {
        stopPolling();
        setCapturedUid(row.captured_uid);
        setPhase('captured');
        setTimeout(() => router.push('/members'), 1800);
        return;
      }
      if (left <= 0) {
        stopPolling();
        setPhase('expired');
        // Leave the row for the bridge to expire naturally; mark it so the UI
        // and any other admin agree it's over.
        await supabase
          .from('pending_enrollments')
          .update({ status: 'expired' })
          .eq('id', pendingId)
          .eq('status', 'pending');
      }
    }, 1000);
  }

  function reset() {
    stopPolling();
    setPending(null);
    setCapturedUid(null);
    setPhase('form');
    setName('');
    setExistingMemberId('');
  }

  const selectedMachine = machines.find((m) => m.id === machineId);
  const selectedBridge = bridges.find((b) => b.machine_id === machineId);
  const bridgeOnline = isBridgeOnline(selectedBridge?.last_seen_at ?? null);

  if (phase === 'waiting' || phase === 'captured' || phase === 'expired') {
    return (
      <div className="card" style={{ maxWidth: 520, margin: '24px auto' }}>
        <div className="enroll-wait">
          <div
            className={`pulse ${
              phase === 'captured' ? 'done' : phase === 'expired' ? 'failed' : ''
            }`}
          />
          {phase === 'waiting' && (
            <>
              <h2 style={{ fontSize: 21 }}>Tap the card on the bridge</h2>
              <p className="card-hint" style={{ margin: '8px 0 14px' }}>
                Enrolling <strong>{pending?.member_name}</strong> at{' '}
                <strong>{selectedMachine?.display_name}</strong>
              </p>
              <div className="countdown">{remaining}</div>
              <p className="muted" style={{ fontSize: 14 }}>
                Waiting for a card…
              </p>
            </>
          )}
          {phase === 'captured' && (
            <>
              <h2 style={{ fontSize: 21 }}>Card captured</h2>
              <p className="card-hint" style={{ margin: '8px 0 14px' }}>
                <strong>{pending?.member_name}</strong> →{' '}
                <span className="mono">{capturedUid}</span>
              </p>
              <p className="muted" style={{ fontSize: 14 }}>
                Taking you to Members…
              </p>
            </>
          )}
          {phase === 'expired' && (
            <>
              <h2 style={{ fontSize: 21 }}>Enrollment timed out</h2>
              <p className="card-hint" style={{ margin: '8px 0 18px' }}>
                No card was tapped at {selectedMachine?.display_name}.
              </p>
              <button className="btn btn-primary" onClick={reset}>
                Try again
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ maxWidth: 520, margin: '24px auto' }}>
      <h2 className="card-title" style={{ fontSize: 20 }}>
        Enroll a card
      </h2>
      <p className="card-hint">
        Pick the machine whose bridge the member will tap, then have them tap
        their badge. The bridge captures the card ID.
      </p>

      {error && <div className="error">{error}</div>}

      {machines.length === 0 ? (
        <div className="empty">
          No enabled machines. Add one on the Machines page first.
        </div>
      ) : (
        <form onSubmit={start}>
          <div className="field">
            <label htmlFor="machine">Bridge / machine</label>
            <select
              id="machine"
              value={machineId}
              onChange={(e) => setMachineId(e.target.value)}
            >
              {machines.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
            {selectedMachine && !bridgeOnline && (
              <div className="notice" style={{ marginTop: 10 }}>
                That machine&apos;s bridge hasn&apos;t checked in recently. It
                won&apos;t see this request until it syncs again.
              </div>
            )}
          </div>

          {cardlessMembers.length > 0 && (
            <div className="field">
              <label htmlFor="existing">Existing member without a card</label>
              <select
                id="existing"
                value={existingMemberId}
                onChange={(e) => setExistingMemberId(e.target.value)}
              >
                <option value="">— new person —</option>
                {cardlessMembers.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {!existingMemberId && (
            <div className="field">
              <label htmlFor="member-name">Member name</label>
              <input
                id="member-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Alex Smith"
                required
                autoFocus
              />
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={busy}
          >
            {busy ? 'Starting…' : 'Start enrollment'}
          </button>
        </form>
      )}
    </div>
  );
}
