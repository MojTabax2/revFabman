// Row shapes mirroring supabase/migrations/0001_init.sql.

export type Machine = {
  id: string;
  name: string;
  display_name: string;
  enabled: boolean;
  ready_seconds: number;
  session_seconds: number;
  extend_seconds: number;
  extend_cooldown_seconds: number;
  session_max_total_seconds: number;
  warning_remaining_seconds: number;
  enrollment_seconds: number;
  denied_display_seconds: number;
  created_at: string;
};

export type Member = {
  id: string;
  name: string;
  rfid_uid: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type Permission = {
  member_id: string;
  machine_id: string;
  granted_at: string;
};

export type Admin = {
  user_id: string;
  email: string;
  note: string | null;
  added_at: string;
  added_by: string | null;
};

/**
 * An account that can sign in but isn't on the admin allowlist. Expected to
 * always be empty — every account here is meant to be an admin.
 */
export type OrphanAccount = {
  user_id: string;
  email: string;
  created_at: string;
  last_sign_in_at: string | null;
};

export type Bridge = {
  id: string;
  name: string;
  machine_id: string | null;
  last_seen_at: string | null;
  last_ip: string | null;
  version: string | null;
  created_at: string;
};

export type Session = {
  id: string;
  member_id: string | null;
  machine_id: string | null;
  bridge_id: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  extensions_seconds: number;
  auto_stopped: boolean;
  created_at: string;
};

export type PendingEnrollment = {
  id: string;
  machine_id: string;
  member_name: string;
  member_id: string | null;
  requested_at: string;
  expires_at: string;
  captured_uid: string | null;
  captured_member_id: string | null;
  status: 'pending' | 'captured' | 'expired' | 'canceled';
};

// Joined shapes used by the UI.

export type SessionWithNames = Session & {
  members: { name: string } | null;
  machines: { display_name: string } | null;
};

export type MemberWithPermissions = Member & {
  permissions: { machine_id: string }[];
};

/** A bridge is "online" if it has checked in recently. */
export const BRIDGE_ONLINE_THRESHOLD_MS = 90_000;

export function isBridgeOnline(lastSeenAt: string | null): boolean {
  if (!lastSeenAt) return false;
  return Date.now() - new Date(lastSeenAt).getTime() < BRIDGE_ONLINE_THRESHOLD_MS;
}
