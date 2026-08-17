'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';

const NAV = [
  { href: '/', label: 'Dashboard' },
  { href: '/members', label: 'Members' },
  { href: '/machines', label: 'Machines' },
  { href: '/enroll', label: 'Enroll' },
  { href: '/sessions', label: 'Sessions' },
  { href: '/admins', label: 'Admins' },
];

export default function SiteHeader({ email }: { email?: string }) {
  const pathname = usePathname();
  const router = useRouter();

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push('/login');
    router.refresh();
  }

  return (
    <header className="site-header">
      <div className="inner">
        <Link href="/" className="brand">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/rev-logo.svg" alt="Rev Ithaca Startup Works" className="brand-logo" />
          <div className="brand-divider">
            <div className="brand-label">Fabman</div>
            <div className="brand-name">Shop access</div>
          </div>
        </Link>
        <nav className="site-nav">
          {NAV.map((item) => {
            const active =
              item.href === '/'
                ? pathname === '/'
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={active ? 'active' : undefined}
              >
                {item.label}
              </Link>
            );
          })}
          <button onClick={signOut} title={email}>
            Sign out
          </button>
        </nav>
      </div>
    </header>
  );
}
