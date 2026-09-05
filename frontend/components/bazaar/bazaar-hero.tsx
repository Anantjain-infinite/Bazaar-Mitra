'use client';

import { Button } from '@/components/ui/button';
import { BAZAAR_STATE_COPY } from '@/components/bazaar/bazaar-state-copy';
import { BazaarSuggestions } from '@/components/bazaar/bazaar-suggestions';

interface BazaarHeroProps {
  /** Only 'ready' and 'ended' are valid here — both are single-CTA screens. */
  phase: 'ready' | 'ended';
  buttonText: string;
  onStart: () => void;
}

export function BazaarHero({ phase, buttonText, onStart }: BazaarHeroProps) {
  const copy = BAZAAR_STATE_COPY[phase];

  return (
    <div className="flex flex-col items-center gap-6 text-center">
      <div aria-live="polite">
        <h1 className="text-foreground text-xl font-semibold md:text-2xl">{copy.title}</h1>
        {copy.subtitle && <p className="text-muted-foreground mt-1 text-sm">{copy.subtitle}</p>}
      </div>

      <Button
        size="lg"
        onClick={onStart}
        aria-label={phase === 'ready' ? 'Start conversation' : 'Start again'}
        className="min-h-11 w-64 rounded-full bg-[#2563EB] font-mono text-xs font-bold tracking-wider text-white uppercase hover:bg-[#1D4ED8] focus-visible:ring-[#2563EB]/40"
      >
        {phase === 'ready' ? `🎙 ${buttonText}` : 'Start again'}
      </Button>

      {phase === 'ready' && <BazaarSuggestions />}
    </div>
  );
}
