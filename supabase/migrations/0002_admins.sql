-- Admin allowlist.
--
-- Before this migration every authenticated user had full access, so anyone who
-- could sign up became an admin. Now "has an account" and "is an admin" are
-- separate: access requires a row in `admins`.
--
-- Also turn OFF public sign-ups in the dashboard
-- (Authentication -> Providers -> Email -> "Allow new users to sign up").
-- This migration makes a stray sign-up harmless, not impossible.

create table admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  note text,
  added_at timestamptz not null default now(),
  added_by uuid references auth.users(id) on delete set null
);

-- SECURITY DEFINER + STABLE is load-bearing: it reads `admins` without
-- invoking RLS. Without it, a policy on `admins` that calls this function
-- would recurse into itself ("infinite recursion detected in policy").
create or replace function is_admin(uid uuid)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
  select exists (select 1 from admins where user_id = uid);
$$;

revoke all on function is_admin(uuid) from public;
grant execute on function is_admin(uuid) to authenticated;

-- ---------------------------------------------------------------------------
-- Swap every policy from "any authenticated user" to "must be an admin".
-- ---------------------------------------------------------------------------
drop policy if exists admin_all on machines;
drop policy if exists admin_all on members;
drop policy if exists admin_all on permissions;
drop policy if exists admin_all on bridges;
drop policy if exists admin_all on sessions;
drop policy if exists admin_all on pending_enrollments;

create policy admin_all on machines for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));
create policy admin_all on members for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));
create policy admin_all on permissions for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));
create policy admin_all on bridges for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));
create policy admin_all on sessions for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));
create policy admin_all on pending_enrollments for all to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));

-- The allowlist itself: admins may read and manage it.
alter table admins enable row level security;

create policy admins_select on admins for select to authenticated
  using (is_admin(auth.uid()));
create policy admins_insert on admins for insert to authenticated
  with check (is_admin(auth.uid()));
create policy admins_delete on admins for delete to authenticated
  using (is_admin(auth.uid()));
create policy admins_update on admins for update to authenticated
  using (is_admin(auth.uid())) with check (is_admin(auth.uid()));

-- ---------------------------------------------------------------------------
-- Never let the allowlist reach zero -- that would lock everyone out of the
-- admin app with no in-app way back in.
-- ---------------------------------------------------------------------------
-- SECURITY DEFINER so the count is the true row count rather than whatever the
-- caller's RLS policies happen to expose.
create or replace function prevent_last_admin_removal()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if (select count(*) from admins) <= 1 then
    raise exception 'Cannot remove the last admin';
  end if;
  return old;
end;
$$;

create trigger admins_prevent_last_removal
  before delete on admins
  for each row execute function prevent_last_admin_removal();

-- ---------------------------------------------------------------------------
-- grant_admin(): add an existing account to the allowlist by email.
--
-- SECURITY DEFINER so it can read auth.users, which clients cannot. That makes
-- the internal is_admin() check mandatory -- without it, any signed-in user
-- could promote themselves. Because this exists, the app does NOT need the
-- service_role key for day-to-day admin management.
-- ---------------------------------------------------------------------------
create or replace function grant_admin(target_email text, target_note text default null)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  target_id uuid;
begin
  if not is_admin(auth.uid()) then
    raise exception 'Only admins can grant admin access';
  end if;

  select id into target_id
  from auth.users
  where lower(email) = lower(trim(target_email));

  if target_id is null then
    raise exception
      'No account exists for %. Create the account first, then grant access.',
      target_email;
  end if;

  insert into admins (user_id, email, note, added_by)
  values (target_id, lower(trim(target_email)), nullif(trim(coalesce(target_note, '')), ''), auth.uid())
  on conflict (user_id) do update set note = excluded.note;

  return target_id;
end;
$$;

revoke all on function grant_admin(text, text) from public;
grant execute on function grant_admin(text, text) to authenticated;

-- ---------------------------------------------------------------------------
-- BOOTSTRAP -- required, run once.
--
-- Until `admins` has at least one row, the app shows "no access" to everyone,
-- including you. Chicken-and-egg by design: the first admin must be inserted
-- out of band.
--
-- 1. Create your user: Authentication -> Users -> Add user (tick auto-confirm).
-- 2. Run this in the Supabase SQL editor -- NOT from the app. The editor runs
--    as the table owner, which bypasses RLS; that's the only way the first
--    insert can succeed while the allowlist is still empty.
--
--   insert into admins (user_id, email, note)
--   select id, email, 'bootstrap admin'
--   from auth.users
--   where lower(email) = lower('you@example.com');
--
-- Verify:  select * from admins;
-- ---------------------------------------------------------------------------
