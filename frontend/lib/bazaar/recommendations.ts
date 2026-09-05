export interface BazaarRecommendation {
  id: string;
  name: string;
  category: string;
  distanceKm: number;
  rating?: number;
  emoji: string;
}

/**
 * Placeholder local-commerce listings.
 *
 * There is no local-commerce/store-search backend in this project yet —
 * only the LiveKit voice-agent connection exists. These are UI placeholders
 * so the "Nearby recommendations" layout can be built and demoed, and the
 * `BazaarRecommendationGrid` component clearly marks them with a
 * "Demo preview" badge rather than presenting them as real listings.
 *
 * Swap this out (or pass a real `items` prop to `BazaarRecommendationGrid`)
 * once real store data exists, e.g. from a tool call the agent makes and
 * publishes back over a data channel.
 */
export const DEMO_RECOMMENDATIONS: BazaarRecommendation[] = [
  {
    id: 'demo-1',
    name: 'Sharma Sports',
    category: 'Sports & footwear',
    distanceKm: 0.8,
    rating: 4.5,
    emoji: '👟',
  },
  {
    id: 'demo-2',
    name: 'Mobile Hub',
    category: 'Mobile accessories',
    distanceKm: 1.2,
    rating: 4.2,
    emoji: '📱',
  },
  {
    id: 'demo-3',
    name: 'Gift Corner',
    category: 'Gifts & novelties',
    distanceKm: 1.5,
    rating: 4.7,
    emoji: '🎁',
  },
];
