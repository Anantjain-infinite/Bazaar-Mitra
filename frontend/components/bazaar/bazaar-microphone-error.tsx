'use client';

import { MicrophoneSlashIcon } from '@phosphor-icons/react';
import { Button } from '@/components/ui/button';

interface BazaarMicrophoneErrorProps {
  onDismiss: () => void;
}

/**
 * Shown when `AgentControlBar`'s `onDeviceError` fires for the microphone
 * (permission denied, device unavailable, etc). The real mic toggle in the
 * control bar underneath remains the actual retry mechanism — this card
 * doesn't duplicate that logic, just explains the failure and points at it.
 */
export function BazaarMicrophoneError({ onDismiss }: BazaarMicrophoneErrorProps) {
  return (
    <div
      role="alert"
      className="border-border bg-background mx-auto flex max-w-sm flex-col items-center gap-3 rounded-2xl border p-6 text-center shadow-sm"
    >
      <span className="flex size-10 items-center justify-center rounded-full bg-[#DC2626]/10 text-[#DC2626]">
        <MicrophoneSlashIcon weight="bold" className="size-5" />
      </span>
      <div>
        <h2 className="text-foreground text-base font-semibold">Microphone access is blocked</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          BazaarMitra needs your microphone to hear you. Please allow microphone access in your
          browser settings, then use the microphone button below to try again.
        </p>
      </div>
      <Button onClick={onDismiss} variant="outline" className="min-h-11 rounded-full">
        Dismiss
      </Button>
    </div>
  );
}
