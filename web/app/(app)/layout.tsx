import { redirect } from 'next/navigation';
import SiteHeader from '@/app/components/SiteHeader';
import NoAccess from '@/app/components/NoAccess';
import { createClient } from '@/lib/supabase/server';

/** Shell for every authenticated page. Requires an allowlisted admin. */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) redirect('/login');

  // Signed in is not the same as authorized: access requires a row in `admins`.
  const { data: isAdmin } = await supabase.rpc('is_admin', { uid: user.id });

  if (!isAdmin) {
    return <NoAccess email={user.email ?? undefined} />;
  }

  return (
    <>
      <SiteHeader email={user.email ?? undefined} />
      <main>{children}</main>
    </>
  );
}
