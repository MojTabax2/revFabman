'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createClient } from '@/lib/supabase/client';
import { formatDateTime, formatDuration } from '@/lib/format';
import type { Machine, SessionWithNames } from '@/lib/types';

const PAGE_SIZE = 100;

export default function SessionsClient() {
  const [sessions, setSessions] = useState<SessionWithNames[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [machineFilter, setMachineFilter] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    const supabase = createClient();
    let query = supabase
      .from('sessions')
      .select('*, members(name), machines(display_name)')
      .order('started_at', { ascending: false })
      .limit(PAGE_SIZE);
    if (machineFilter) query = query.eq('machine_id', machineFilter);

    const [sessionsRes, machinesRes] = await Promise.all([
      query,
      supabase.from('machines').select('*').order('display_name'),
    ]);
    const err = sessionsRes.error || machinesRes.error;
    if (err) setError(err.message);
    else {
      setError(null);
      setSessions((sessionsRes.data ?? []) as SessionWithNames[]);
      setMachines((machinesRes.data ?? []) as Machine[]);
    }
    setLoaded(true);
  }, [machineFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const totals = useMemo(() => {
    const completed = sessions.filter((s) => s.ended_at);
    const seconds = completed.reduce(
      (sum, s) => sum + (s.duration_seconds ?? 0),
      0
    );
    return { count: completed.length, seconds };
  }, [sessions]);

  function exportCsv() {
    const header = [
      'session_id',
      'started_at',
      'ended_at',
      'duration_seconds',
      'extensions_seconds',
      'auto_stopped',
      'member',
      'machine',
    ];
    const rows = sessions.map((s) => [
      s.id,
      s.started_at,
      s.ended_at ?? '',
      s.duration_seconds ?? '',
      s.extensions_seconds,
      s.auto_stopped ? 'true' : 'false',
      s.members?.name ?? '',
      s.machines?.display_name ?? '',
    ]);
    const csv = [header, ...rows]
      .map((r) =>
        r
          .map((cell) => {
            const v = String(cell);
            return v.includes(',') || v.includes('"')
              ? `"${v.replace(/"/g, '""')}"`
              : v;
          })
          .join(',')
      )
      .join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `fabman-sessions-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!loaded) return <div className="empty">Loading…</div>;

  return (
    <>
      <div className="page-header">
        <h1>Sessions</h1>
        <button
          className="btn"
          onClick={exportCsv}
          disabled={sessions.length === 0}
        >
          Export CSV
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <div className="row-between" style={{ marginBottom: 14 }}>
          <div>
            <select
              value={machineFilter}
              onChange={(e) => setMachineFilter(e.target.value)}
              style={{ minWidth: 220 }}
            >
              <option value="">All machines</option>
              {machines.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="muted" style={{ fontSize: 13 }}>
            {totals.count} completed · {formatDuration(totals.seconds)} total
            {sessions.length === PAGE_SIZE && ` · showing latest ${PAGE_SIZE}`}
          </div>
        </div>

        {sessions.length === 0 ? (
          <div className="empty">No sessions recorded yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Ended</th>
                  <th>Member</th>
                  <th>Machine</th>
                  <th>Duration</th>
                  <th>Extended</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td>{formatDateTime(s.started_at)}</td>
                    <td>
                      {s.ended_at ? (
                        formatDateTime(s.ended_at)
                      ) : (
                        <span className="badge accent">active</span>
                      )}
                    </td>
                    <td>
                      <strong>{s.members?.name ?? '—'}</strong>
                    </td>
                    <td>{s.machines?.display_name ?? '—'}</td>
                    <td>{formatDuration(s.duration_seconds)}</td>
                    <td>
                      {s.extensions_seconds > 0
                        ? `+${formatDuration(s.extensions_seconds)}`
                        : '—'}
                    </td>
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
