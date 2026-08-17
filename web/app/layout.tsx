import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Fabman · Rev Ithaca Startup Works',
  description: 'Machine access control for the Rev shop',
  icons: { icon: '/favicon.svg' },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
