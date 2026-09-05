'use client';

import { useSessionContext } from '@livekit/components-react';
import { cn } from '@/lib/shadcn/utils';

/**
 * BazaarMitra brand header. The "Online" indicator reflects the real
 * `session.isConnected` value from LiveKit — it is never faked.
 */
export function BazaarHeader() {
  const { isConnected } = useSessionContext();

  return (
    <header className="mx-auto flex w-full max-w-2xl items-center justify-between px-4 pt-6 md:px-0">
      <div className="flex items-center gap-2">
        <span className="text-xl" role="img" aria-label="BazaarMitra">
          🛍️
        </span>
        <div>
          <p className="text-foreground text-sm leading-tight font-bold tracking-tight">
            BazaarMitra
          </p>
          <p className="text-muted-foreground hidden text-xs leading-tight md:block">
            Your voice-powered local shopping companion
          </p>
        </div>
      </div>

      <div className="text-muted-foreground hidden items-center gap-1.5 text-xs font-medium md:flex">
        <span
          className={cn('size-1.5 rounded-full', isConnected ? 'bg-[#16A34A]' : 'bg-border')}
          aria-hidden="true"
        />
        {isConnected ? 'Online' : 'Offline'}
      </div>
    </header>
  );
}
