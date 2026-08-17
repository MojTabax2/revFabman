import { createClient as createSupabaseClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { createClient } from '@/lib/supabase/server';

/**
 * Add an admin by email.
 *
 * Creates the account if it doesn't exist, then allowlists it. Existing
 * accounts are simply allowlisted (and reactivated/renamed), so the caller
 * doesn't have to know or care which case they're in.
 *
 * Needs SUPABASE_SERVICE_ROLE_KEY, because creating an auth user is privileged.
 * Without it this returns 501 and the UI falls back to the grant_admin()
 * database function, which can allowlist existing accounts with no elevated key.
 *
 * The service key is server-only (no NEXT_PUBLIC_ prefix) and never reaches the
 * browser. The caller-is-an-admin check below is what keeps this from being an
 * open door to elevated access -- do not remove it.
 */
export async function POST(request: Request) {
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;

  if (!serviceKey || !url) {
    return NextResponse.json(
      { error: 'not_configured' },
      { status: 501 }
    );
  }

  // 1. Who is calling?
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: 'Not signed in.' }, { status: 401 });
  }

  // 2. Are they already an admin? Without this, any signed-in user could mint
  //    themselves an admin account.
  const { data: isAdmin, error: checkError } = await supabase.rpc('is_admin', {
    uid: user.id,
  });
  if (checkError) {
    return NextResponse.json({ error: checkError.message }, { status: 500 });
  }
  if (!isAdmin) {
    return NextResponse.json({ error: 'Admins only.' }, { status: 403 });
  }

  // 3. Validate input.
  let body: { email?: string; password?: string; note?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid request body.' }, { status: 400 });
  }

  const email = (body.email ?? '').trim().toLowerCase();
  const password = body.password ?? '';
  const note = (body.note ?? '').trim() || null;

  if (!email) {
    return NextResponse.json({ error: 'Email is required.' }, { status: 400 });
  }

  const admin = createSupabaseClient(url, serviceKey, {
    auth: { autoRefreshToken: false, persistSession: false },
  });

  // 4. Does an account already exist for this email?
  //    listUsers is paginated; 1000 is far beyond the handful of shop admins
  //    this install will ever have.
  const { data: existingList, error: listError } =
    await admin.auth.admin.listUsers({ page: 1, perPage: 1000 });
  if (listError) {
    return NextResponse.json({ error: listError.message }, { status: 500 });
  }
  const existing = existingList.users.find(
    (u) => (u.email ?? '').toLowerCase() === email
  );

  // 5a. Existing account -> just allowlist it.
  if (existing) {
    const { error: allowlistError } = await admin
      .from('admins')
      .upsert(
        { user_id: existing.id, email, note, added_by: user.id },
        { onConflict: 'user_id' }
      );
    if (allowlistError) {
      return NextResponse.json(
        { error: allowlistError.message },
        { status: 500 }
      );
    }
    return NextResponse.json({ ok: true, email, created: false });
  }

  // 5b. No account -> create one, then allowlist it.
  if (password.length < 10) {
    return NextResponse.json(
      {
        error:
          `No account exists for ${email}, so a temporary password is required ` +
          `(at least 10 characters).`,
      },
      { status: 400 }
    );
  }

  const { data: created, error: createError } =
    await admin.auth.admin.createUser({
      email,
      password,
      // Confirm immediately: Supabase's built-in mailer is rate-limited and
      // often unconfigured, so waiting on a confirmation email would strand
      // the new admin.
      email_confirm: true,
    });

  if (createError || !created?.user) {
    return NextResponse.json(
      { error: createError?.message ?? 'Could not create the account.' },
      { status: 500 }
    );
  }

  const { error: allowlistError } = await admin.from('admins').insert({
    user_id: created.user.id,
    email,
    note,
    added_by: user.id,
  });

  if (allowlistError) {
    // Don't leave a usable account lying around that isn't an admin.
    await admin.auth.admin.deleteUser(created.user.id);
    return NextResponse.json({ error: allowlistError.message }, { status: 500 });
  }

  return NextResponse.json({ ok: true, email, created: true });
}

/** Tells the UI which backend it should use for adding admins. */
export async function GET() {
  return NextResponse.json({
    canCreateAccounts: Boolean(
      process.env.SUPABASE_SERVICE_ROLE_KEY && process.env.NEXT_PUBLIC_SUPABASE_URL
    ),
  });
}
