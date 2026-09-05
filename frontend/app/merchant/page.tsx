'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  type AuditEvent,
  type Campaign,
  type Merchant,
  type OrderSummary,
  type ProductMetrics,
  type RecommendationOpportunity,
  type RevenueMetrics,
  commerceApi,
} from '@/lib/commerce-api';
import { cn } from '@/lib/shadcn/utils';

type Tab = 'overview' | 'products' | 'orders' | 'growth' | 'audit';

const TABS: { id: Tab; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'products', label: 'Products' },
  { id: 'orders', label: 'Orders' },
  { id: 'growth', label: 'AI Growth' },
  { id: 'audit', label: 'Audit' },
];

export default function MerchantPage() {
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [merchantId, setMerchantId] = useState<string>('');
  const [tab, setTab] = useState<Tab>('overview');
  const [loading, setLoading] = useState(false);

  const [revenue, setRevenue] = useState<RevenueMetrics | null>(null);
  const [products, setProducts] = useState<ProductMetrics | null>(null);
  const [opportunities, setOpportunities] = useState<RecommendationOpportunity[]>([]);
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    commerceApi.listMerchants().then((list) => {
      setMerchants(list);
      if (list.length > 0) setMerchantId(list[0].id);
    });
  }, []);

  const loadAll = useCallback(async () => {
    if (!merchantId) return;
    setLoading(true);
    try {
      const [analytics, recs, orderList, campaignList, audit] = await Promise.all([
        commerceApi.getAnalytics(merchantId),
        commerceApi.getRecommendations(merchantId),
        commerceApi.listOrders(merchantId),
        commerceApi.listCampaigns(merchantId),
        commerceApi.getAuditForMerchant(merchantId),
      ]);
      setRevenue(analytics.revenue);
      setProducts(analytics.products);
      setOpportunities(recs.opportunities);
      setOrders(orderList);
      setCampaigns(campaignList);
      setAuditEvents(audit);
    } finally {
      setLoading(false);
    }
  }, [merchantId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleDraftCampaign = async (opp: RecommendationOpportunity) => {
    setActionMessage(null);
    await commerceApi.createCampaign(
      merchantId,
      'cross_sell_offer',
      {
        product_id: opp.recommended_product_id,
        discount_price: Math.round(opp.recommended_product_price * 0.75),
      },
      `Get ${opp.recommended_product_name} for a special price with your ${opp.source_product_name}!`,
      { purchased_product_id: opp.source_product_id }
    );
    setActionMessage(`Campaign drafted — awaiting your approval below.`);
    await loadAll();
  };

  const handleApprove = async (campaignId: string) => {
    await commerceApi.approveCampaign(campaignId, 'merchant_owner');
    await loadAll();
  };

  return (
    <main className="mx-auto min-h-screen max-w-4xl px-6 pt-24 pb-16">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-foreground text-2xl font-semibold tracking-tight">
            Merchant Dashboard
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Real revenue, orders, and AI-assisted growth — nothing here is mocked.
          </p>
        </div>
        <select
          value={merchantId}
          onChange={(e) => setMerchantId(e.target.value)}
          className="border-border bg-card rounded-lg border px-3 py-2 text-sm"
        >
          {merchants.map((m) => (
            <option key={m.id} value={m.id}>
              {m.business_name}
            </option>
          ))}
        </select>
      </header>

      <div className="border-border mb-6 flex gap-1 border-b">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              'border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              tab === t.id
                ? 'border-primary text-foreground'
                : 'text-muted-foreground hover:text-foreground border-transparent'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading && <p className="text-muted-foreground text-sm">Loading…</p>}

      {!loading && tab === 'overview' && revenue && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <MetricCard label="Revenue (30d)" value={`₹${revenue.revenue.toLocaleString('en-IN')}`} />
          <MetricCard label="Orders" value={String(revenue.orders)} />
          <MetricCard
            label="Avg order value"
            value={`₹${revenue.average_order_value.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`}
          />
          <MetricCard
            label="Payment success"
            value={`${Math.round(revenue.payment_success_rate * 100)}%`}
          />
          <MetricCard label="AI-assisted orders" value={String(revenue.ai_assisted_orders)} />
          <MetricCard
            label="AI-assisted revenue"
            value={`₹${revenue.ai_assisted_revenue.toLocaleString('en-IN')}`}
          />
        </div>
      )}

      {!loading && tab === 'products' && products && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="border-border bg-card rounded-xl border p-4">
            <h3 className="text-foreground mb-3 text-sm font-medium">Top products</h3>
            {products.top_products.length === 0 && (
              <p className="text-muted-foreground text-xs">No sales yet in this window.</p>
            )}
            <ul className="space-y-2">
              {products.top_products.map((p) => (
                <li key={p.product_id} className="flex items-center justify-between text-sm">
                  <span className="text-foreground">{p.name}</span>
                  <span className="text-muted-foreground text-xs">
                    ₹{p.revenue.toLocaleString('en-IN')} · {p.units_sold} sold
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="border-border bg-card rounded-xl border p-4">
            <h3 className="text-foreground mb-3 text-sm font-medium">Slow-moving products</h3>
            {products.slow_products.length === 0 && (
              <p className="text-muted-foreground text-xs">Everything active has sold recently.</p>
            )}
            <ul className="space-y-2">
              {products.slow_products.map((p) => (
                <li key={p.product_id} className="flex items-center justify-between text-sm">
                  <span className="text-foreground">{p.name}</span>
                  <span className="text-muted-foreground text-xs">{p.stock_quantity} in stock</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {!loading && tab === 'orders' && (
        <div className="border-border bg-card divide-border divide-y rounded-xl border">
          {orders.length === 0 && (
            <p className="text-muted-foreground p-4 text-sm">No orders yet.</p>
          )}
          {orders.map((o) => (
            <div key={o.id} className="p-4">
              <div className="flex items-center justify-between">
                <span className="text-foreground text-sm font-medium">{o.public_order_id}</span>
                <span className="text-foreground text-sm">₹{o.total.toLocaleString('en-IN')}</span>
              </div>
              <div className="mt-1 flex items-center gap-2">
                <StatusPill status={o.status} />
                <span className="text-muted-foreground text-xs">{o.payment_status}</span>
              </div>
              {o.payments.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {o.payments.map((p) => (
                    <div key={p.attempt_number} className="text-muted-foreground text-xs">
                      Attempt #{p.attempt_number} — {p.status}
                      {p.failure_reason ? ` (${p.failure_reason})` : ''}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && tab === 'growth' && (
        <div className="space-y-6">
          <div>
            <h3 className="text-foreground mb-3 text-sm font-medium">Cross-sell opportunities</h3>
            {opportunities.length === 0 && (
              <p className="text-muted-foreground text-sm">
                No accepted recommendations yet — opportunities appear here once buyers start
                accepting cross-sell/upsell suggestions.
              </p>
            )}
            <div className="space-y-2">
              {opportunities.map((opp) => (
                <div
                  key={`${opp.source_product_id}-${opp.recommended_product_id}`}
                  className="border-border bg-card rounded-xl border p-4"
                >
                  <p className="text-foreground text-sm">
                    Customers buying <strong>{opp.source_product_name}</strong> frequently accept{' '}
                    <strong>{opp.recommended_product_name}</strong>
                  </p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {opp.times_accepted} accepted · {opp.times_converted} converted · ₹
                    {opp.revenue_generated.toLocaleString('en-IN')} generated so far
                  </p>
                  <button
                    onClick={() => handleDraftCampaign(opp)}
                    className="text-primary mt-2 text-xs font-medium underline underline-offset-2"
                  >
                    Draft a cross-sell campaign
                  </button>
                </div>
              ))}
            </div>
          </div>

          {actionMessage && <p className="text-muted-foreground text-xs">{actionMessage}</p>}

          <div>
            <h3 className="text-foreground mb-3 text-sm font-medium">Campaigns</h3>
            {campaigns.length === 0 && (
              <p className="text-muted-foreground text-sm">No campaigns yet.</p>
            )}
            <div className="border-border bg-card divide-border divide-y rounded-xl border">
              {campaigns.map((c) => (
                <div key={c.campaign_id} className="flex items-center justify-between p-4">
                  <div>
                    <p className="text-foreground text-sm">{c.message}</p>
                    <p className="text-muted-foreground mt-0.5 text-xs">{c.campaign_type}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <CampaignStatusPill status={c.status} />
                    {c.status === 'PENDING_APPROVAL' && (
                      <button
                        onClick={() => handleApprove(c.campaign_id)}
                        className="bg-primary text-primary-foreground rounded-md px-2.5 py-1 text-xs font-medium"
                      >
                        Approve
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {!loading && tab === 'audit' && (
        <div className="border-border bg-card divide-border divide-y rounded-xl border">
          {auditEvents.length === 0 && (
            <p className="text-muted-foreground p-4 text-sm">No events yet.</p>
          )}
          {auditEvents.map((event) => (
            <div key={event.id} className="p-4">
              <div className="flex items-center justify-between">
                <span className="text-foreground text-sm font-medium">
                  {event.event_type} · {event.action}
                </span>
                <span
                  className={cn(
                    'text-xs font-medium',
                    event.success ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'
                  )}
                >
                  {event.success ? 'OK' : 'BLOCKED'}
                </span>
              </div>
              <p className="text-muted-foreground mt-1 text-xs">{event.explanation}</p>
              <p className="text-muted-foreground mt-1 text-[11px]">
                {new Date(event.timestamp).toLocaleString()}
                {event.agent_name ? ` · ${event.agent_name}` : ''}
                {event.amount ? ` · ₹${event.amount}` : ''}
              </p>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="border-border bg-card rounded-xl border p-4">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="text-foreground mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const positive = status === 'PAID' || status === 'FULFILLED' || status === 'CONFIRMED';
  const negative = status === 'PAYMENT_FAILED' || status === 'CANCELLED';
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-[11px] font-medium',
        positive && 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
        negative && 'bg-destructive/15 text-destructive',
        !positive && !negative && 'bg-muted text-muted-foreground'
      )}
    >
      {status}
    </span>
  );
}

function CampaignStatusPill({ status }: { status: string }) {
  const positive = status === 'APPROVED' || status === 'RUNNING' || status === 'COMPLETED';
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-[11px] font-medium',
        positive
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
          : 'bg-muted text-muted-foreground'
      )}
    >
      {status}
    </span>
  );
}
