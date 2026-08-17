// Timestamps are stored UTC; render them in the shop's timezone.

const TZ = process.env.NEXT_PUBLIC_TIMEZONE || 'America/New_York';

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: TZ,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    timeZone: TZ,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '—';
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m) return `${m}m ${String(sec).padStart(2, '0')}s`;
  return `${sec}s`;
}

/** "just now" / "3m ago" — for bridge heartbeats. */
export function formatRelative(iso: string | null): string {
  if (!iso) return 'never';
  const deltaMs = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(deltaMs / 1000);
  if (sec < 10) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

export function formatMinutes(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m === 1 ? '1 min' : `${m} min`;
}
