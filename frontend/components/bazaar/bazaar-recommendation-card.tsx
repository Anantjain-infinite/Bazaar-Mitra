import type { BazaarRecommendation } from '@/lib/bazaar/recommendations';

interface BazaarRecommendationCardProps {
  item: BazaarRecommendation;
}

export function BazaarRecommendationCard({ item }: BazaarRecommendationCardProps) {
  return (
    <div className="border-border bg-background flex min-w-[168px] shrink-0 flex-col gap-2 rounded-2xl border p-4 text-left shadow-sm">
      <span className="text-2xl" role="img" aria-hidden="true">
        {item.emoji}
      </span>
      <div>
        <p className="text-foreground text-sm font-semibold">{item.name}</p>
        {item.rating !== undefined && (
          <p className="mt-0.5 text-xs text-[#16A34A]">★ {item.rating.toFixed(1)}</p>
        )}
      </div>
      <div className="text-muted-foreground text-xs leading-relaxed">
        <p>{item.distanceKm.toFixed(1)} km away</p>
        <p>{item.category}</p>
      </div>
    </div>
  );
}
