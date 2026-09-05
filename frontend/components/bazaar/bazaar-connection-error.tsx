'use client';

import { WarningIcon } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';

interface BazaarConnectionErrorProps {
  /** Real failure reasons from `useAgent().failureReasons`, if any. */
  reasons?: string[] | null;
  onRetry: () => void;
}

export function BazaarConnectionError({ reasons, onRetry }: BazaarConnectionErrorProps) {
  const hasReasons = Boolean(reasons && reasons.length > 0);

  return (
    <div
      role="alert"
      className="border-border bg-background mx-auto flex max-w-sm flex-col items-center gap-3 rounded-2xl border p-6 text-center shadow-sm"
    >
      <span className="flex size-10 items-center justify-center rounded-full bg-[#DC2626]/10 text-[#DC2626]">
        <WarningIcon weight="bold" className="size-5" />
      </span>
      <div>
        <h2 className="text-foreground text-base font-semibold">Couldn't connect</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          We couldn't connect to BazaarMitra{hasReasons ? ':' : '.'}
        </p>
        {hasReasons && (
          <ul className="text-muted-foreground mt-1 list-inside list-disc text-left text-xs">
            {reasons!.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>
      <Button
        onClick={onRetry}
        className="min-h-11 rounded-full bg-[#2563EB] font-mono text-xs font-bold tracking-wider text-white uppercase hover:bg-[#1D4ED8]"
      >
        Try again
      </Button>
    </div>
  );
}
