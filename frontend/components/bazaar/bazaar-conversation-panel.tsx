'use client';

import { useAgent, useSessionContext, useSessionMessages } from '@livekit/components-react';
// Reusing the existing transcript renderer (speaker labels, alignment,
// auto-scroll, thinking indicator) instead of building a second one.
import { AgentChatTranscript } from '@/components/agents-ui/agent-chat-transcript';
import { cn } from '@/lib/shadcn/utils';

interface BazaarConversationPanelProps {
  className?: string;
}

export function BazaarConversationPanel({ className }: BazaarConversationPanelProps) {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const { state: agentState } = useAgent();

  return (
    <div
      className={cn(
        'border-border bg-background flex h-full flex-col overflow-hidden rounded-2xl border',
        className
      )}
    >
      <div className="border-border shrink-0 border-b px-4 py-3">
        <h2 className="text-foreground text-sm font-semibold">Live conversation</h2>
      </div>

      {messages.length === 0 ? (
        <div className="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-1 p-6 text-center text-sm">
          <p>Nothing here yet.</p>
          <p className="text-xs">What you say — and BazaarMitra's replies — will show up here.</p>
        </div>
      ) : (
        <AgentChatTranscript
          agentState={agentState}
          messages={messages}
          className="min-h-0 flex-1 [&_.is-user>div]:rounded-[22px] [&>div>div]:px-4 [&>div>div]:py-4"
        />
      )}
    </div>
  );
}
