-- Fabman v2 initial schema
-- Postgres / Supabase. All timestamps are timestamptz (stored UTC).
--
-- Clients:
--   * Admins  -> Vercel app via Supabase Auth (authenticated role). Full access via RLS.
--   * Bridges -> Raspberry Pis using the service_role key (bypasses RLS). Keep that key
--                secret on the Pi. Hardening to a scoped bridge key is a later task.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- updated_at helper
-- ---------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- machines: one row per controlled machine/station. Per-machine tunables live
-- here so admins can adjust them centrally; the Pi pulls them during sync.
-- ---------------------------------------------------------------------------
create table machines (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,                 -- slug the Pi config references, e.g. 'laser-1'
  display_name text not null,                -- shown on the OLED + app
  enabled boolean not null default true,
  ready_seconds int not null default 30,
  session_seconds int not null default 900,
  extend_seconds int not null default 900,
  extend_cooldown_seconds int not null default 10,
  session_max_total_seconds int not null default 18000,
  warning_remaining_seconds int not null default 60,
  enrollment_seconds int not null default 60,
  denied_display_seconds int not null default 2,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- members: people, one RFID card each.
-- rfid_uid is exactly what the reader emits (OMNIKEY HID Prox in keyboard-wedge
-- mode gives a 10-hex-char string, e.g. '043f6afba2').
-- Nullable so a member can be created before their badge is enrolled; unique
-- when set. Soft-deleted via active=false so session history is preserved.
-- ---------------------------------------------------------------------------
create table members (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  rfid_uid text unique,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index members_uid_idx on members(rfid_uid);

create trigger members_set_updated_at
  before update on members
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- permissions: which member may use which machine.
-- ---------------------------------------------------------------------------
create table permissions (
  member_id uuid not null references members(id) on delete cascade,
  machine_id uuid not null references machines(id) on delete cascade,
  granted_at timestamptz not null default now(),
  primary key (member_id, machine_id)
);

create index permissions_machine_idx on permissions(machine_id);

-- ---------------------------------------------------------------------------
-- bridges: the Pi devices. Heartbeat + health.
-- ---------------------------------------------------------------------------
create table bridges (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,                 -- e.g. 'bridge-laser-1'
  machine_id uuid references machines(id) on delete set null,
  last_seen_at timestamptz,
  last_ip text,
  version text,
  created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- sessions: usage records. The Pi generates the id locally and upserts, so
-- retried pushes after an outage are idempotent (no duplicate rows).
-- ---------------------------------------------------------------------------
create table sessions (
  id uuid primary key,                       -- provided by the bridge (client-generated)
  member_id uuid references members(id) on delete set null,
  machine_id uuid references machines(id) on delete set null,
  bridge_id uuid references bridges(id) on delete set null,
  started_at timestamptz not null,
  ended_at timestamptz,
  duration_seconds int,
  extensions_seconds int not null default 0,
  auto_stopped boolean not null default false,
  created_at timestamptz not null default now()
);

create index sessions_machine_idx on sessions(machine_id);
create index sessions_member_idx on sessions(member_id);
create index sessions_started_idx on sessions(started_at desc);

-- ---------------------------------------------------------------------------
-- pending_enrollments: enrollment coordination between the app and a bridge.
-- Scoped to a machine so the right bridge picks it up.
-- ---------------------------------------------------------------------------
create table pending_enrollments (
  id uuid primary key default gen_random_uuid(),
  machine_id uuid not null references machines(id) on delete cascade,
  member_name text not null,
  member_id uuid references members(id) on delete cascade,  -- set when re-badging an existing member
  requested_at timestamptz not null default now(),
  expires_at timestamptz not null,
  captured_uid text,
  captured_member_id uuid references members(id) on delete set null,
  status text not null default 'pending'      -- pending | captured | expired | canceled
);

create index pending_machine_idx on pending_enrollments(machine_id, status);

-- ---------------------------------------------------------------------------
-- Convenience view for bridge sync: active members with a badge who are
-- granted on each machine. The Pi pulls "where machine_id = <me>" and caches
-- uid -> member locally for offline authorization.
-- ---------------------------------------------------------------------------
create view v_machine_access as
select
  p.machine_id,
  m.rfid_uid as uid,
  m.id       as member_id,
  m.name     as member_name
from permissions p
join members m on m.id = p.member_id
where m.active = true
  and m.rfid_uid is not null;

-- ---------------------------------------------------------------------------
-- Row Level Security
-- Any authenticated user (admin, via the Vercel app) gets full access.
-- Refine to an explicit admins allowlist later if needed. The service_role
-- key used by bridges bypasses RLS entirely.
-- ---------------------------------------------------------------------------
alter table machines            enable row level security;
alter table members             enable row level security;
alter table permissions         enable row level security;
alter table bridges             enable row level security;
alter table sessions            enable row level security;
alter table pending_enrollments enable row level security;

create policy admin_all on machines            for all to authenticated using (true) with check (true);
create policy admin_all on members             for all to authenticated using (true) with check (true);
create policy admin_all on permissions         for all to authenticated using (true) with check (true);
create policy admin_all on bridges             for all to authenticated using (true) with check (true);
create policy admin_all on sessions            for all to authenticated using (true) with check (true);
create policy admin_all on pending_enrollments for all to authenticated using (true) with check (true);
