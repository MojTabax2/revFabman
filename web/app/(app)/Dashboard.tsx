'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { createClient } from '@/lib/supabase/client';
import { formatDateTime, formatDuration, formatRelative } from '@/lib/format';
import { isBridgeOnline } from '@/lib/types';
import type {
  Bridge,
  Machine,
  PendingEnrollment,
  SessionWithNames,
} from '@/lib/types';

const REFRESH_MS = 5000;

type MachineStatus = {
  machine: Machine;
  bridge: Bridge | null;
  activeSession: SessionWithNames | null;
  pendingEnrollment: PendingEnrollment | null;
};

export default function Dashboard() {
  const [statuses, setStatuses] = useState<MachineStatus[]>([]);
  const [recent, setRecent] = useState<SessionWithNames[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const supabase = createClient();
    const nowIso = new Date().toISOString();

    const [machinesRes, bridgesRes, activeRes, pendingRes, recentRes] =
      await Promise.all([
        supabase.from('machines').select('*').order('display_name'),
        supabase.from('bridges').select('*'),
        supabase
          .from('sessions')
          .select('*, members(name), machines(display_name)')
          .is('ended_at', null),
        supabase
          .from('pending_enrollments')
          .select('*')
          .eq('status', 'pending')
          .gt('expires_at', nowIso),
        supabase
          .from('sessions')
          .select('*, members(name), machines(display_name)')
          .not('ended_at', 'is', null)
          .order('started_at', { ascending: false })
          .limit(10),
      ]);

    const firstError =
      machinesRes.error ||
      bridgesRes.error ||
      activeRes.error ||
      pendingRes.error ||
      recentRes.error;
    if (firstError) {
      setError(firstError.message);
      setLoaded(true);
      return;
    }
    setError(null);

    const machines = (machinesRes.data ?? []) as Machine[];
    const bridges = (bridgesRes.data ?? []) as Bridge[];
    const active = (activeRes.data ?? []) as SessionWithNames[];
    const pending = (pendingRes.data ?? []) as PendingEnrollment[];

    setStatuses(
      machines.map((machine) => ({
        machine,
        bridge: bridges.find((b) => b.machine_id === machine.id) ?? null,
        activeSession: active.find((s) => s.machine_id === machine.id) ?? null,
        pendingEnrollment:
          pending.find((p) => p.machine_id === machine.id) ?? null,
      }))
    );
    setRecent((recentRes.data ?? []) as SessionWithNames[]);
    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
    // Poll while the tab is visible; skip work when it's hidden.
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') load();
    }, REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [load]);

  if (!loaded) {
    return <div className="empty">Loading…</div>;
  }

  return (
    <>
      <div className="page-header">
        <h1>Dashboard</h1>
        <Link href="/enroll" className="btn btn-primary">
          Enroll a member
        </Link>
      </div>

      {error && <div className="error">{error}</div>}

      {statuses.length === 0 ? (
        <div className="card">
          <div className="empty">
            No machines yet. Add one on the{' '}
            <Link href="/machines">Machines</Link> page, then point a bridge at
            it.
          </div>
        </div>
      ) : (
        <div className="grid">
          {statuses.map((s) => (
            <MachineCard key={s.machine.id} status={s} />
          ))}
        </div>
      )}

      <div className="card">
        <h3 className="card-title">Recent sessions</h3>
        {recent.length === 0 ? (
          <div className="empty">No completed sessions yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Member</th>
                  <th>Machine</th>
                  <th>Duration</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {recent.slice(0, 6).map((s) => (
                  <tr key={s.id}>
                    <td>{formatDateTime(s.started_at)}</td>
                    <td>
                      <strong>{s.members?.name ?? '—'}</strong>
                    </td>
                    <td>{s.machines?.display_name ?? '—'}</td>
                    <td>{formatDuration(s.duration_seconds)}</td>
                    <td>
                      {s.auto_stopped && (
                        <span className="badge warn">auto-stop</span>
                      )}
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

function MachineCard({ status }: { status: MachineStatus }) {
  const { machine, bridge, activeSession, pendingEnrollment } = status;
  const online = isBridgeOnline(bridge?.last_seen_at ?? null);

  let kind = 'idle';
  let icon = '○';
  let title = 'Idle';
  let detail = 'Press the button on the bridge to begin.';

  if (activeSession) {
    kind = 'running';
    icon = '●';
    title = 'Running';
    detail = `${activeSession.members?.name ?? 'Unknown'} · since ${formatDateTime(
      activeSession.started_at
    )}`;
  } else if (pendingEnrollment) {
    kind = 'enrolling';
    icon = '⊕';
    title = `Enrolling ${pendingEnrollment.member_name}`;
    detail = 'Waiting for a card tap at the bridge.';
  }

  // A bridge that stopped checking in outranks whatever its last state was.
  if (!bridge || !online) {
    kind = 'offline';
    icon = '⚠';
    title = bridge ? 'Bridge offline' : 'No bridge';
    detail = bridge
      ? `Last seen ${formatRelative(bridge.last_seen_at)}. The machine keeps working from its local cache.`
      : 'No bridge has checked in for this machine yet.';
  }

  return (
    <div className="card">
      <div className="row-between" style={{ marginBottom: 12 }}>
        <h3 style={{ fontSize: 16 }}>{machine.display_name}</h3>
        <span className={`badge ${online ? 'online' : 'offline'}`}>
          {online ? 'online' : 'offline'}
        </span>
      </div>

      <div className={`status-banner ${kind}`} style={{ marginBottom: 12 }}>
        <div className="status-icon">{icon}</div>
        <div className="status-text">
          <h2>{title}</h2>
          <p>{detail}</p>
        </div>
      </div>

      <div className="mono" style={{ fontSize: 12 }}>
        {machine.name}
        {bridge && ` · ${bridge.name}`}
        {bridge?.version && ` · v${bridge.version}`}
      </div>
      {!machine.enabled && (
        <div style={{ marginTop: 10 }}>
          <span className="badge danger">disabled</span>
        </div>
      )}
    </div>
  );
}
