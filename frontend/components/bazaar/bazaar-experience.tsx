'use client';

import { useCallback, useState } from 'react';
import { useTheme } from 'next-themes';
import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { BazaarConnectionError } from '@/components/bazaar/bazaar-connection-error';
import { BazaarConversationPanel } from '@/components/bazaar/bazaar-conversation-panel';
import { BazaarHeader } from '@/components/bazaar/bazaar-header';
import { BazaarHero } from '@/components/bazaar/bazaar-hero';
import { BazaarMicrophoneError } from '@/components/bazaar/bazaar-microphone-error';
import { BazaarRecommendationGrid } from '@/components/bazaar/bazaar-recommendation-grid';
import { BAZAAR_STATE_COPY } from '@/components/bazaar/bazaar-state-copy';
import { BazaarVoiceStage } from '@/components/bazaar/bazaar-voice-stage';
import { useBazaarPhase } from '@/hooks/bazaar/use-bazaar-phase';

const FADE_PROPS = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
  transition: { duration: 0.2 },
};

interface BazaarExperienceProps {
  appConfig: AppConfig;
}

/**
 * BazaarMitra's top-level view. Drop-in replacement for `ViewController`
 * inside `components/app/app.tsx` — it consumes the exact same
 * `useSessionContext()` from `AgentSessionProvider` and never creates a
 * second connection path. Real agent-state work (start/end, mic/camera
 * toggles, chat send, transcript, mic-device errors) is delegated to the
 * existing `AgentControlBar` / `AgentChatTranscript` / `useSessionMessages`.
 */
export function BazaarExperience({ appConfig }: BazaarExperienceProps) {
  const session = useSessionContext();
  const { phase, failureReasons } = useBazaarPhase();
  const { resolvedTheme } = useTheme();

  const [startError, setStartError] = useState<string | null>(null);
  const [micError, setMicError] = useState<Error | null>(null);

  const handleStart = useCallback(async () => {
    setStartError(null);
    try {
      await session.start();
    } catch (error) {
      console.error('BazaarMitra: failed to start session', error);
      setStartError("We couldn't connect to BazaarMitra. Please try again.");
    }
  }, [session]);

  const showHero = phase === 'ready' || phase === 'ended';
  const showError = phase === 'error' || Boolean(startError);
  // Only mount controls/transcript once the room is actually connected —
  // matches the original architecture (AgentSessionView_01 does the same),
  // so device toggles never run against a room that isn't connected yet.
  // Also excluded on `error`, since useAgentErrors() (in AppSetup) ends the
  // session asynchronously on agent failure — this avoids a one-frame flash
  // of controls alongside the error card while that teardown is in flight.
  const showLiveControls = session.isConnected && !showError;

  const audioVisualizerColor =
    resolvedTheme === 'dark' ? appConfig.audioVisualizerColorDark : appConfig.audioVisualizerColor;

  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    chat: appConfig.supportsChatInput,
    camera: appConfig.supportsVideoInput,
    screenShare: appConfig.supportsScreenShare,
  };

  return (
    <div className="bg-background flex min-h-svh w-full flex-col gap-8 pt-2 pb-8 md:gap-10 md:pt-4">
      <BazaarHeader />

      <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-8 px-4 md:flex-row md:items-start md:px-0">
        {/* Voice area */}
        <div className="flex w-full flex-col items-center gap-6 md:w-[360px] md:shrink-0">
          <AnimatePresence mode="wait">
            {showError ? (
              <motion.div key="error" {...FADE_PROPS}>
                <BazaarConnectionError
                  reasons={failureReasons}
                  onRetry={() => {
                    setStartError(null);
                    handleStart();
                  }}
                />
              </motion.div>
            ) : showHero ? (
              <motion.div key="hero" {...FADE_PROPS} className="flex flex-col items-center gap-6">
                <BazaarVoiceStage
                  phase={phase}
                  appConfig={appConfig}
                  audioVisualizerColor={audioVisualizerColor}
                />
                <BazaarHero
                  phase={phase === 'ended' ? 'ended' : 'ready'}
                  buttonText={appConfig.startButtonText}
                  onStart={handleStart}
                />
              </motion.div>
            ) : (
              // eslint-disable-next-line prettier/prettier
              <motion.div key="live" {...FADE_PROPS} className="flex w-full flex-col items-center gap-6">
                <BazaarVoiceStage
                  phase={phase}
                  appConfig={appConfig}
                  audioVisualizerColor={audioVisualizerColor}
                />

                <div aria-live="polite" className="text-center">
                  <p className="text-foreground text-base font-semibold">
                    {BAZAAR_STATE_COPY[phase].title}
                  </p>
                  {BAZAAR_STATE_COPY[phase].subtitle && (
                    <p className="text-muted-foreground mt-1 text-sm">
                      {BAZAAR_STATE_COPY[phase].subtitle}
                    </p>
                  )}
                </div>

                {micError && <BazaarMicrophoneError onDismiss={() => setMicError(null)} />}

                {showLiveControls && (
                  <AgentControlBar
                    variant="livekit"
                    controls={controls}
                    isConnected={session.isConnected}
                    onDisconnect={session.end}
                    onDeviceError={({ error }) => setMicError(error)}
                    className="w-full max-w-sm"
                  />
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Live transcript */}
        {showLiveControls && (
          <div className="min-h-[320px] w-full flex-1 md:min-h-[420px]">
            <BazaarConversationPanel className="h-full" />
          </div>
        )}
      </div>

      <BazaarRecommendationGrid />
    </div>
  );
}
