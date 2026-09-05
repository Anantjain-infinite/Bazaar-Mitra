const BASE_URL =
  process.env.NEXT_PUBLIC_COMMERCE_API_URL?.replace(/\/$/, '') || 'http://localhost:8000';

export class CommerceApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    const detailObj = detail as { detail?: unknown } | null;
    super(
      typeof detail === 'string'
        ? detail
        : detailObj?.detail
          ? JSON.stringify(detailObj.detail)
          : `Request failed with status ${status}`
    );
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  options: RequestInit & { params?: Record<string, string | number | boolean | undefined> } = {}
): Promise<T> {
  const { params, ...init } = options;
  const url = new URL(`${BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
  }
  const res = await fetch(url.toString(), {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new CommerceApiError(res.status, data);
  return data as T;
}

// --- Types (mirroring the backend's Pydantic response shapes) -------------

export interface Merchant {
  id: string;
  business_name: string;
  owner_name: string;
  city: string;
  state: string;
  currency: string;
  active: boolean;
}

export interface Buyer {
  id: string;
  name: string;
  phone: string | null;
  is_ai_agent: boolean;
}

export interface AgentSessionState {
  session_id: string;
  current_agent: string;
  previous_agent: string | null;
  merchant_id: string | null;
  buyer_id: string | null;
  cart_id: string | null;
  order_id: string | null;
  payment_id: string | null;
  language: string;
}

export interface PolicyResult {
  allowed: boolean;
  reasons: string[];
  requires_confirmation: boolean;
  amount: number;
  currency: string;
  max_transaction_amount: number;
  max_daily_amount: number;
  daily_spend_before: number;
  daily_spend_after: number;
}

export interface ComparisonProduct {
  product_id: string;
  merchant_id: string;
  merchant_name: string;
  price: number;
  stock: number;
  available: boolean;
}

export interface DiscoverAndCompareResult {
  ok: boolean;
  as_of?: string;
  candidates_considered?: number;
  available_count?: number;
  comparison?: ComparisonProduct[];
  selected?: {
    product_id: string;
    merchant_id: string;
    merchant_name: string;
    price: number;
    currency: string;
  } | null;
  explanation?: string;
  error?: string;
  message?: string;
}

export interface CheckoutResult {
  ok: boolean;
  order_id?: string;
  public_order_id?: string;
  total?: number;
  currency?: string;
  policy?: PolicyResult;
  requires_explicit_confirmation?: boolean;
  error?: string;
  message?: string;
  issues?: unknown[];
}

export interface ConfirmResult {
  ok: boolean;
  order_id?: string;
  public_order_id?: string;
  status?: string;
  confirmed_at?: string | null;
  error?: string;
  message?: string;
  policy?: PolicyResult;
}

export interface PayResult {
  ok: boolean;
  payment_id?: string;
  razorpay_order_id?: string;
  razorpay_key_id?: string;
  amount_paise?: number;
  currency?: string;
  error?: string;
  message?: string;
}

export interface AuditEvent {
  id: string;
  timestamp: string;
  actor_type: string;
  agent_name: string | null;
  event_type: string;
  action: string;
  amount: number | null;
  currency: string | null;
  explanation: string;
  policy_result: string | null;
  confirmation_state: string | null;
  success: boolean;
  failure_reason: string | null;
}

export interface RevenueMetrics {
  window_days: number;
  revenue: number;
  orders: number;
  average_order_value: number;
  payment_attempts: number;
  payment_success_rate: number;
  ai_assisted_orders: number;
  ai_assisted_revenue: number;
  upsell_conversions: number;
}

export interface ProductMetrics {
  window_days: number;
  top_products: { product_id: string; name: string; revenue: number; units_sold: number }[];
  slow_products: { product_id: string; name: string; stock_quantity: number }[];
}

export interface RecommendationOpportunity {
  source_product_id: string;
  source_product_name: string;
  recommended_product_id: string;
  recommended_product_name: string;
  recommended_product_price: number;
  times_shown: number;
  times_accepted: number;
  times_converted: number;
  conversion_rate: number;
  revenue_generated: number;
}

export interface OrderSummary {
  id: string;
  public_order_id: string;
  status: string;
  payment_status: string;
  total: number;
  currency: string;
  created_at?: string;
  payments: { attempt_number: number; status: string; failure_reason: string | null }[];
}

export interface Campaign {
  campaign_id: string;
  campaign_type: string;
  status: string;
  offer: Record<string, unknown>;
  message: string;
  created_at: string;
}

export interface SearchResult {
  as_of: string;
  interpreted_as: Record<string, unknown>;
  count: number;
  products: unknown[];
}

export interface CartView {
  ok: boolean;
  empty: boolean;
  items: { product_id: string; quantity: number; unit_price: number; line_total: number }[];
  subtotal?: number;
  total: number;
  currency: string;
}

export interface AddToCartResult {
  ok: boolean;
  cart_id?: string;
  added?: { product_id: string; quantity: number; unit_price: number };
  cart_total?: number;
  currency?: string;
  accepted_recommendation_id?: string | null;
  error?: string;
}

export interface VerifyPaymentResult {
  payment: { id: string; status: string };
  order: { id: string; status: string };
  verified: boolean;
}

export interface RecommendationMetrics {
  window_days: number;
  recommendations_shown: number;
  accepted: number;
  rejected: number;
  converted: number;
  acceptance_rate: number;
  conversion_rate: number;
  ai_assisted_revenue: number;
}

// --- API functions ----------------------------------------------------

export const commerceApi = {
  listMerchants: () => request<Merchant[]>('/api/v1/merchants'),

  identifyBuyer: (phone: string, name?: string) =>
    request<Buyer>('/api/v1/buyers/identify', {
      method: 'POST',
      body: JSON.stringify({ phone, name }),
    }),

  createSession: (buyerId: string, merchantId?: string, channel = 'web') =>
    request<AgentSessionState>('/api/v1/agent/sessions', {
      method: 'POST',
      body: JSON.stringify({ buyer_id: buyerId, merchant_id: merchantId, channel }),
    }),

  getSession: (sessionId: string) =>
    request<AgentSessionState>(`/api/v1/agent/sessions/${sessionId}`),

  buyBestAvailable: (sessionId: string, query: string, city?: string) =>
    request<{
      ok: boolean;
      explanation?: string;
      checkout?: CheckoutResult;
      error?: string;
      message?: string;
    }>('/api/v1/agent/buy-best-available', {
      method: 'POST',
      params: { session_id: sessionId },
      body: JSON.stringify({ query, city }),
    }),

  search: (sessionId: string, query: string) =>
    request<SearchResult>(`/api/v1/agent/sessions/${sessionId}/search`, {
      method: 'POST',
      body: JSON.stringify({ query }),
    }),

  viewCart: (sessionId: string) =>
    request<CartView>('/api/v1/agent/cart', { params: { session_id: sessionId } }),

  addToCart: (sessionId: string, productId: string, quantity = 1) =>
    request<AddToCartResult>('/api/v1/agent/cart', {
      method: 'POST',
      params: { session_id: sessionId },
      body: JSON.stringify({ product_id: productId, quantity }),
    }),

  checkout: (sessionId: string, acknowledgePriceChange = false) =>
    request<CheckoutResult>('/api/v1/agent/checkout', {
      method: 'POST',
      params: { session_id: sessionId },
      body: JSON.stringify({ acknowledge_price_change: acknowledgePriceChange }),
    }),

  confirm: (sessionId: string) =>
    request<ConfirmResult>('/api/v1/agent/confirm', {
      method: 'POST',
      params: { session_id: sessionId },
    }),

  pay: (sessionId: string) =>
    request<PayResult>('/api/v1/agent/pay', { method: 'POST', params: { session_id: sessionId } }),

  verifyPayment: (paymentId: string, razorpayPaymentId: string, razorpaySignature: string) =>
    request<VerifyPaymentResult>('/api/v1/payments/verify', {
      method: 'POST',
      body: JSON.stringify({
        payment_id: paymentId,
        razorpay_payment_id: razorpayPaymentId,
        razorpay_signature: razorpaySignature,
      }),
    }),

  getOrder: (orderId: string) => request<OrderSummary>(`/api/v1/orders/${orderId}`),

  getAuditTrail: (resourceId: string) => request<AuditEvent[]>(`/api/v1/audit/${resourceId}`),

  // --- Merchant dashboard ---
  getRevenue: (merchantId: string, days = 30) =>
    request<RevenueMetrics>('/api/v1/merchant/revenue', {
      params: { merchant_id: merchantId, days },
    }),

  getAnalytics: (merchantId: string, days = 30) =>
    request<{ revenue: RevenueMetrics; products: ProductMetrics }>('/api/v1/merchant/analytics', {
      params: { merchant_id: merchantId, days },
    }),

  getRecommendations: (merchantId: string, days = 30) =>
    request<{ metrics: RecommendationMetrics; opportunities: RecommendationOpportunity[] }>(
      '/api/v1/merchant/recommendations',
      { params: { merchant_id: merchantId, days } }
    ),

  listOrders: (merchantId: string, limit = 20) =>
    request<OrderSummary[]>('/api/v1/orders', { params: { merchant_id: merchantId, limit } }),

  listCampaigns: (merchantId: string) =>
    request<Campaign[]>('/api/v1/growth/campaigns', { params: { merchant_id: merchantId } }),

  createCampaign: (
    merchantId: string,
    campaignType: string,
    offer: Record<string, unknown>,
    message: string,
    audienceDefinition: Record<string, unknown> = {}
  ) =>
    request<{ campaign_id: string; status: string }>('/api/v1/growth/campaigns', {
      method: 'POST',
      body: JSON.stringify({
        merchant_id: merchantId,
        campaign_type: campaignType,
        offer,
        message,
        audience_definition: audienceDefinition,
      }),
    }),

  approveCampaign: (campaignId: string, approvedBy: string) =>
    request<{ campaign_id: string; status: string }>(
      `/api/v1/growth/campaigns/${campaignId}/approve`,
      {
        method: 'POST',
        body: JSON.stringify({ approved_by: approvedBy }),
      }
    ),

  getAuditForMerchant: (merchantId: string) =>
    request<AuditEvent[]>('/api/v1/audit', { params: { merchant_id: merchantId, limit: 30 } }),
};

export { BASE_URL as COMMERCE_API_BASE_URL };
