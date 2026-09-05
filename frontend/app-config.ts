export interface AppConfig {
  pageTitle: string;
  pageDescription: string;
  companyName: string;

  supportsChatInput: boolean;
  supportsVideoInput: boolean;
  supportsScreenShare: boolean;
  isPreConnectBufferEnabled: boolean;

  logo: string;
  startButtonText: string;
  accent?: string;
  logoDark?: string;
  accentDark?: string;

  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura';
  audioVisualizerColor?: `#${string}`;
  audioVisualizerColorDark?: `#${string}`;
  audioVisualizerColorShift?: number;
  audioVisualizerBarCount?: number;
  audioVisualizerGridRowCount?: number;
  audioVisualizerGridColumnCount?: number;
  audioVisualizerRadialBarCount?: number;
  audioVisualizerRadialRadius?: number;
  audioVisualizerWaveLineWidth?: number;

  // agent dispatch configuration
  agentName?: string;

  // LiveKit Cloud Sandbox configuration
  sandboxId?: string;
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'BazaarMitra',
  pageTitle: 'BazaarMitra — Voice-Powered Local Shopping',
  pageDescription: 'Talk naturally. Discover local products and stores near you.',

  supportsChatInput: true,
  // BazaarMitra is a voice-first local-commerce assistant — no camera/screen
  // share needed for this product, so these are turned off through the
  // existing config flags (AgentControlBar already respects them) rather
  // than by deleting any working camera/screen-share code.
  supportsVideoInput: false,
  supportsScreenShare: false,
  isPreConnectBufferEnabled: true,

  // NOTE: left pointing at the existing LiveKit logo files on purpose.
  // opengraph-image.tsx has special-case logic keyed off filenames
  // containing "lk-logo" (for wordmark lookup); swap these once real
  // BazaarMitra logo assets exist in /public and opengraph-image.tsx has
  // been checked against them.
  logo: '/murf-logo.svg',
  logoDark: '/murf-logo-dark.svg',

  accent: '#2563EB',
  accentDark: '#3B82F6',
  startButtonText: 'Start conversation',

  // audio visualization configuration
  audioVisualizerType: 'bar',
  audioVisualizerBarCount: 5,

  // agent dispatch configuration
  agentName: process.env.AGENT_NAME ?? undefined,

  // LiveKit Cloud Sandbox configuration
  sandboxId: undefined,
};
