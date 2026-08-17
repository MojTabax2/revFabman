'use client';

import { useCallback, useEffect, useState } from 'react';
import { createClient } from '@/lib/supabase/client';
import { formatRelative } from '@/lib/format';
import { isBridgeOnline } from '@/lib/types';
import type { Bridge, Machine } from '@/lib/types';

/** Editable timing fields, in minutes where that reads better than seconds. */
const TUNABLES: {
  key: keyof Machine;
  label: string;
  unit: 'seconds' | 'minutes';
  hint: string;
}[] = [
  {
    key: 'session_seconds',
    label: 'Session length',
    unit: 'minutes',
    hint: 'How long a session runs before auto-stop',
  },
  {
    key: 'extend_seconds',
    label: 'Extension per re-tap',
    unit: 'minutes',
    hint: 'Time added when the active member taps again',
  },
  {
    key: 'session_max_total_seconds',
    label: 'Max total session',
    unit: 'minutes',
    hint: 'Hard ceiling including extensions',
  },
  {
    key: 'warning_remaining_seconds',
    label: 'Warning buzz at',
    unit: 'seconds',
    hint: 'Seconds remaining when the 3-beep warning fires',
  },
  {
    key: 'ready_seconds',
    label: 'Tap window',
    unit: 'seconds',
    hint: 'How long the bridge waits for a card after the button',
  },
  {
    key: 'enrollment_seconds',
    label: 'Enrollment window',
    unit: 'seconds',
    hint: 'How long an enrollment stays open for a tap',
  },
];

export default function MachinesClient() {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [bridges, setBridges] = useState<Bridge[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [newName, setNewName] = useState('');
  const [newDisplay, setNewDisplay] = useState('');

  const load = useCallback(async () => {
    const supabase = createClient();
    const [machinesRes, bridgesRes] = await Promise.all([
      supabase.from('machines').select('*').order('display_name'),
      supabase.from('bridges').select('*'),
    ]);
    const err = machinesRes.error || bridgesRes.error;
    if (err) setError(err.message);
    else {
      setError(null);
      setMachines((machinesRes.data ?? []) as Machine[]);
      setBridges((bridgesRes.data ?? []) as Bridge[]);
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addMachine(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    const display_name = newDisplay.trim() || name;
    if (!name) return;
    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase
      .from('machines')
      .insert({ name, display_name });
    if (error) {
      setError(
        error.code === '23505'
          ? `A machine named "${name}" already exists.`
          : error.message
      );
    } else {
      setError(null);
      setNotice(
        `Added "${display_name}". Set station.machine_name: "${name}" in that bridge's config.yaml.`
      );
      setNewName('');
      setNewDisplay('');
    }
    await load();
    setBusy(false);
  }

  function startEdit(machine: Machine) {
    const d: Record<string, number> = {};
    for (const t of TUNABLES) {
      const raw = machine[t.key] as number;
      d[t.key as string] = t.unit === 'minutes' ? Math.round(raw / 60) : raw;
    }
    setDraft(d);
    setEditing(machine.id);
  }

  async function saveEdit(machine: Machine) {
    setBusy(true);
    const updates: Record<string, number> = {};
    for (const t of TUNABLES) {
      const value = draft[t.key as string];
      updates[t.key as string] = t.unit === 'minutes' ? value * 60 : value;
    }
    const supabase = createClient();
    const { error } = await supabase
      .from('machines')
      .update(updates)
      .eq('id', machine.id);
    if (error) setError(error.message);
    else {
      setError(null);
      setNotice(
        `Saved. ${machine.display_name}'s bridge will pick this up on its next sync.`
      );
      setEditing(null);
    }
    await load();
    setBusy(false);
  }

  async function toggleEnabled(machine: Machine) {
    setBusy(true);
    const supabase = createClient();
    const { error } = await supabase
      .from('machines')
      .update({ enabled: !machine.enabled })
      .eq('id', machine.id);
    if (error) setError(error.message);
    await load();
    setBusy(false);
  }

  if (!loaded) return <div className="empty">Loading…</div>;

  return (
    <>
      <div className="page-header">
        <h1>Machines</h1>
      </div>

      {error && <div className="error">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      <div className="card">
        <h3 className="card-title">Add a machine</h3>
        <p className="card-hint">
          The slug must match <code className="mono">station.machine_name</code>{' '}
          in that bridge&apos;s <code className="mono">config.yaml</code>.
        </p>
        <form onSubmit={addMachine}>
          <div className="form-row">
            <div>
              <label htmlFor="slug">Slug</label>
              <input
                id="slug"
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. laser-1"
                className="mono"
                required
              />
            </div>
            <div>
              <label htmlFor="display">Display name</label>
              <input
                id="display"
                type="text"
                value={newDisplay}
                onChange={(e) => setNewDisplay(e.target.value)}
                placeholder="e.g. Laser Cutter 1"
              />
            </div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Add machine
            </button>
          </div>
        </form>
      </div>

      {machines.length === 0 ? (
        <div className="card">
          <div className="empty">No machines yet.</div>
        </div>
      ) : (
        machines.map((machine) => {
          const bridge = bridges.find((b) => b.machine_id === machine.id);
          const online = isBridgeOnline(bridge?.last_seen_at ?? null);
          return (
            <div className="card" key={machine.id}>
              <div className="row-between" style={{ marginBottom: 12 }}>
                <div>
                  <h3 style={{ fontSize: 17 }}>{machine.display_name}</h3>
                  <div className="mono" style={{ fontSize: 12 }}>
                    {machine.name}
                    {bridge ? ` · ${bridge.name}` : ' · no bridge yet'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className={`badge ${online ? 'online' : 'offline'}`}>
                    {bridge
                      ? online
                        ? 'online'
                        : `last seen ${formatRelative(bridge.last_seen_at)}`
                      : 'no bridge'}
                  </span>
                  <span
                    className={`badge ${machine.enabled ? 'ok' : 'danger'}`}
                  >
                    {machine.enabled ? 'enabled' : 'disabled'}
                  </span>
                </div>
              </div>

              {editing === machine.id ? (
                <>
                  <div
                    style={{
                      display: 'grid',
                      gap: 14,
                      gridTemplateColumns:
                        'repeat(auto-fill, minmax(220px, 1fr))',
                      marginBottom: 16,
                    }}
                  >
                    {TUNABLES.map((t) => (
                      <div key={t.key as string}>
                        <label htmlFor={`${machine.id}-${t.key}`}>
                          {t.label} ({t.unit})
                        </label>
                        <input
                          id={`${machine.id}-${t.key}`}
                          type="number"
                          min={1}
                          value={draft[t.key as string] ?? 0}
                          onChange={(e) =>
                            setDraft({
                              ...draft,
                              [t.key as string]: Number(e.target.value),
                            })
                          }
                        />
                        <div
                          className="muted"
                          style={{ fontSize: 12, marginTop: 4 }}
                        >
                          {t.hint}
                        </div>
                      </div>
                    ))}
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      className="btn btn-primary"
                      disabled={busy}
                      onClick={() => saveEdit(machine)}
                    >
                      Save settings
                    </button>
                    <button
                      className="btn"
                      disabled={busy}
                      onClick={() => setEditing(null)}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {TUNABLES.map((t) => (
                            <th key={t.key as string}>{t.label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          {TUNABLES.map((t) => {
                            const raw = machine[t.key] as number;
                            return (
                              <td key={t.key as string}>
                                {t.unit === 'minutes'
                                  ? `${Math.round(raw / 60)} min`
                                  : `${raw}s`}
                              </td>
                            );
                          })}
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                    <button
                      className="btn btn-sm btn-primary"
                      onClick={() => startEdit(machine)}
                    >
                      Edit timings
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={busy}
                      onClick={() => toggleEnabled(machine)}
                    >
                      {machine.enabled ? 'Disable' : 'Enable'}
                    </button>
                  </div>
                </>
              )}
            </div>
          );
        })
      )}
    </>
  );
}
