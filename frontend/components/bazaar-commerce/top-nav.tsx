'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/shadcn/utils';

const LINKS = [
  { href: '/', label: 'Voice' },
  { href: '/buyer', label: 'AI Buyer' },
  { href: '/merchant', label: 'Merchant' },
];

export function TopNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed top-3 left-1/2 z-40 -translate-x-1/2">
      <div className="border-border/60 bg-card/90 flex items-center gap-0.5 rounded-full border p-1 shadow-sm backdrop-blur-sm">
        {LINKS.map((link) => {
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                'rounded-full px-3 py-1.5 text-xs font-medium transition-colors',
                active
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
