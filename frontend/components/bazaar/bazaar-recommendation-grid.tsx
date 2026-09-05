import { BazaarRecommendationCard } from '@/components/bazaar/bazaar-recommendation-card';
import { DEMO_RECOMMENDATIONS, type BazaarRecommendation } from '@/lib/bazaar/recommendations';

interface BazaarRecommendationGridProps {
  /** Pass real store data here once it exists. Defaults to labeled demo data. */
  items?: BazaarRecommendation[];
  loading?: boolean;
}

function RecommendationSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="border-border bg-muted min-w-[168px] shrink-0 animate-pulse rounded-2xl border p-4 motion-reduce:animate-none"
    >
      <div className="bg-border mb-3 size-6 rounded" />
      <div className="bg-border mb-2 h-3 w-3/4 rounded" />
      <div className="bg-border h-3 w-1/2 rounded" />
    </div>
  );
}

export function BazaarRecommendationGrid({
  items = DEMO_RECOMMENDATIONS,
  loading = false,
}: BazaarRecommendationGridProps) {
  // No real store backend exists yet — hide the section entirely rather
  // than showing an empty shell, unless we're explicitly loading.
  if (!loading && items.length === 0) {
    return null;
  }

  return (
    <section aria-label="Nearby recommendations" className="mx-auto w-full max-w-2xl px-4 md:px-0">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-foreground text-sm font-semibold">Nearby recommendations</h2>
        <span className="bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-[10px] font-medium tracking-wide uppercase">
          Demo preview
        </span>
      </div>
      <div className="flex gap-3 overflow-x-auto pb-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => <RecommendationSkeleton key={i} />)
          : items.map((item) => <BazaarRecommendationCard key={item.id} item={item} />)}
      </div>
    </section>
  );
}
