'use client';

import { useRef } from 'react';
import { ConnectionState } from 'livekit-client';
import { useAgent, useSessionContext } from '@livekit/components-react';

/**
 * The five states required by the BazaarMitra spec, plus a real `thinking`
 * state (the agent SDK genuinely reports this between listening and
 * speaking) and an `error` state for connection/agent failures.
 */
export type BazaarPhase =
  | 'ready'
  | 'connecting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'ended'
  | 'error';

export interface UseBazaarPhaseReturn {
  phase: BazaarPhase;
  /** Populated only when `phase === 'error'` and the agent reported reasons. */
  failureReasons: string[] | null;
}

/**
 * Derives a single, product-facing phase from the real LiveKit session and
 * agent state. This never invents state — `phase` is a pure function of
 * `useSessionContext()` (room-level connection, from the existing
 * `AgentSessionProvider`) and `useAgent()` (agent lifecycle).
 */
export function useBazaarPhase(): UseBazaarPhaseReturn {
  const session = useSessionContext();
  const agent = useAgent();

  // Distinguishes "ended" (a call happened, then disconnected) from "ready"
  // (never started), per the CALL ENDED vs READY states in the spec.
  // Writing a ref during render is safe here: the write is idempotent (once
  // true, always true for this session) and depends only on this render's
  // data — the pattern React itself documents for derived state.
  const hasConnectedOnce = useRef(false);
  if (session.isConnected) {
    hasConnectedOnce.current = true;
  }

  let phase: BazaarPhase;

  if (agent.state === 'failed') {
    phase = 'error';
  } else if (session.connectionState === ConnectionState.Connecting) {
    // Room is still being established (before the agent can even be present).
    phase = 'connecting';
  } else if (session.isConnected) {
    switch (agent.state) {
      case 'listening':
      case 'pre-connect-buffering':
        phase = 'listening';
        break;
      case 'thinking':
        phase = 'thinking';
        break;
      case 'speaking':
        phase = 'speaking';
        break;
      // 'connecting' | 'initializing' | 'idle' | 'disconnected': the room is
      // up but the agent participant hasn't finished joining/warming up yet.
      default:
        phase = 'connecting';
    }
  } else {
    phase = hasConnectedOnce.current ? 'ended' : 'ready';
  }

  return {
    phase,
    failureReasons: agent.state === 'failed' ? agent.failureReasons : null,
  };
}
