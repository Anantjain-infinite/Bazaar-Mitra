import { cn } from '@/lib/shadcn/utils';

const EXAMPLE_PROMPTS = [
  'Find shoes under ₹1500',
  'Find a grocery store nearby',
  'Where can I buy headphones?',
  'Help me find a birthday gift',
];

/**
 * Visual examples of what to say. These are static (not clickable) since
 * there's no active session yet to send them to — wiring them to
 * auto-start-and-send would mean inventing a second interaction path
 * alongside the real "Start conversation" flow.
 */
export function BazaarSuggestions() {
  return (
    <div className="w-full max-w-sm">
      <p className="text-muted-foreground mb-3 text-center text-xs font-medium tracking-wide uppercase">
        Try saying
      </p>
      <ul className="flex flex-wrap items-center justify-center gap-2">
        {EXAMPLE_PROMPTS.map((prompt, index) => (
          <li
            key={prompt}
            className={cn(
              'bg-muted text-foreground border-border rounded-full border px-3 py-1.5 text-xs',
              // Keep the mobile empty state from getting crowded (spec §18).
              index >= 2 && 'hidden sm:block'
            )}
          >
            “{prompt}”
          </li>
        ))}
      </ul>
    </div>
  );
}
