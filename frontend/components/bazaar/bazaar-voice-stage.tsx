'use client';

import { AnimatePresence, motion } from 'motion/react';
import type { AppConfig } from '@/app-config';
// Reusing the project's existing, real audio visualizer (driven by
// useVoiceAssistant()/useTrackVolume() under the hood) rather than building
// a second, fake one.
import { AudioVisualizer } from '@/components/agents-ui/blocks/agent-session-view-01/components/audio-visualizer';
import type { BazaarPhase } from '@/hooks/bazaar/use-bazaar-phase';
import { cn } from '@/lib/shadcn/utils';

interface BazaarVoiceStageProps {
  phase: BazaarPhase;
  appConfig: AppConfig;
  audioVisualizerColor?: `#${string}`;
}

export function BazaarVoiceStage({ phase, appConfig, audioVisualizerColor }: BazaarVoiceStageProps) {
  // Before there's anything to visualize (no live agent audio yet), show the
  // brand icon instead of an empty/static visualizer.
  const showIcon = phase === 'ready' || phase === 'ended' || phase === 'error' || phase === 'connecting';

  return (
    <div
      className={cn(
        'bg-background border-border relative flex size-48 shrink-0 items-center justify-center rounded-full border shadow-sm transition-colors md:size-56',
        phase === 'listening' && 'border-[#2563EB]/40',
        phase === 'speaking' && 'border-[#2563EB]/60'
      )}
    >
      {phase === 'connecting' && (
        <span
          aria-hidden="true"
          className="absolute inset-0 animate-spin rounded-full border-2 border-transparent border-t-[#2563EB] motion-reduce:animate-none"
        />
      )}

      <AnimatePresence mode="wait">
        {showIcon ? (
          <motion.span
            key="icon"
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.85 }}
            transition={{ duration: 0.2 }}
            className="text-5xl motion-reduce:transition-none"
            role="img"
            aria-label="BazaarMitra"
          >
            🛍️
          </motion.span>
        ) : (
          <motion.div
            key="visualizer"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex size-full items-center justify-center overflow-hidden rounded-full"
          >
            <AudioVisualizer
              isChatOpen={false}
              audioVisualizerType={appConfig.audioVisualizerType ?? 'bar'}
              audioVisualizerColor={audioVisualizerColor}
              audioVisualizerColorShift={appConfig.audioVisualizerColorShift}
              audioVisualizerBarCount={appConfig.audioVisualizerBarCount ?? 5}
              audioVisualizerGridRowCount={appConfig.audioVisualizerGridRowCount}
              audioVisualizerGridColumnCount={appConfig.audioVisualizerGridColumnCount}
              audioVisualizerRadialBarCount={appConfig.audioVisualizerRadialBarCount}
              audioVisualizerRadialRadius={appConfig.audioVisualizerRadialRadius}
              audioVisualizerWaveLineWidth={appConfig.audioVisualizerWaveLineWidth ?? 3}
              className="size-32 md:size-40"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
