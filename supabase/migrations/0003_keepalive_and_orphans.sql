-- Two operational safeguards.
--
-- 1. keepalive / ping()  -- Free-plan Supabase projects pause after 7 days with
--    no database activity. Bridges sync every 30s, so a running shop keeps the
--    project awake on its own; this covers the case where every bridge is
--    powered off for a week or more (holiday shutdown, move, etc.).
--
-- 2. list_orphan_accounts()  -- Every account here is supposed to be an admin.
--    This surfaces any that aren't, so drift is visible instead of silent.

-- ---------------------------------------------------------------------------
-- 1. Keepalive
-- ---------------------------------------------------------------------------
create table keepalive (
  id int primary key default 1,
  pinged_at timestamptz not null default now(),
  constraint keepalive_single_row check (id = 1)
);

insert into keepalive (id) values (1);

alter table keepalive enable row level security;
-- No policies: nothing may touch this table directly. All access goes through
-- ping() below, which is SECURITY DEFINER.

-- Bumps a timestamp and returns it. That write is the "database activity" that
-- resets Supabase's inactivity timer.
--
-- Granted to `anon` on purpose so a scheduled job needs only the anon key --
-- which is already public in the web app, so this adds no secret to leak. The
-- function can do exactly one thing and reads nothing.
create or replace function ping()
returns timestamptz
language sql
security definer
set search_path = public
as $$
  update keepalive set pinged_at = now() where id = 1 returning pinged_at;
$$;

revoke all on function ping() from public;
grant execute on function ping() to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. Orphan account detection
-- ---------------------------------------------------------------------------
-- Accounts that can sign in but have no admins row. Expected to always be
-- empty: sign-ups are disabled and every account should be allowlisted. If
-- rows appear, something drifted -- an account added straight in the dashboard,
-- or a sign-up from before the toggle was flipped.
--
-- SECURITY DEFINER because clients cannot read auth.users. That makes the
-- internal is_admin() check mandatory, or any signed-in user could enumerate
-- every account on the project.
create or replace function list_orphan_accounts()
returns table (user_id uuid, email text, created_at timestamptz, last_sign_in_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_admin(auth.uid()) then
    raise exception 'Only admins can list accounts';
  end if;

  return query
  select u.id, u.email::text, u.created_at, u.last_sign_in_at
  from auth.users u
  left join admins a on a.user_id = u.id
  where a.user_id is null
  order by u.created_at;
end;
$$;

revoke all on function list_orphan_accounts() from public;
grant execute on function list_orphan_accounts() to authenticated;

-- ---------------------------------------------------------------------------
-- Delete an account outright. The natural fix for an orphan is usually to grant
-- it admin (grant_admin), but stale or unwanted accounts should be removable
-- without a trip to the dashboard.
--
-- Deleting from auth.users cascades to that user's identities, sessions and
-- refresh tokens via existing foreign keys.
-- ---------------------------------------------------------------------------
create or replace function delete_account(target_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not is_admin(auth.uid()) then
    raise exception 'Only admins can delete accounts';
  end if;

  if target_user_id = auth.uid() then
    raise exception 'You cannot delete your own account';
  end if;

  -- Removing the admins row first lets the last-admin trigger do its job
  -- rather than being bypassed by the cascade from auth.users.
  delete from admins where user_id = target_user_id;
  delete from auth.users where id = target_user_id;
end;
$$;

revoke all on function delete_account(uuid) from public;
grant execute on function delete_account(uuid) to authenticated;
