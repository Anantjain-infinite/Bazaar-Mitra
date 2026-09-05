import type { BazaarPhase } from '@/hooks/bazaar/use-bazaar-phase';

export interface BazaarStateCopy {
  title: string;
  subtitle?: string;
}

export const BAZAAR_STATE_COPY: Record<BazaarPhase, BazaarStateCopy> = {
  ready: {
    title: 'Ready to shop?',
    subtitle: 'Talk to BazaarMitra',
  },
  connecting: {
    title: 'Connecting to BazaarMitra...',
    subtitle: 'Just a moment while we connect you.',
  },
  listening: {
    title: 'Listening to you',
  },
  thinking: {
    title: 'BazaarMitra is thinking',
    subtitle: 'Finding the best matches nearby...',
  },
  speaking: {
    title: 'BazaarMitra is speaking',
  },
  ended: {
    title: 'Conversation ended',
    subtitle: 'Ready to discover something nearby?',
  },
  error: {
    title: "Couldn't connect",
    subtitle: "We couldn't connect to BazaarMitra.",
  },
};
