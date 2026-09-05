'use client';

import { useCallback, useState } from 'react';
import {
  type AgentSessionState,
  type AuditEvent,
  type CheckoutResult,
  CommerceApiError,
  commerceApi,
} from '@/lib/commerce-api';
import { cn } from '@/lib/shadcn/utils';

type Step = 'identify' | 'search' | 'reviewing' | 'confirmed' | 'paying' | 'paid' | 'failed';

interface RazorpayCheckoutResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

interface RazorpayConstructorOptions {
  key: string;
  amount?: number;
  currency?: string;
  order_id?: string;
  name?: string;
  description?: string;
  handler: (response: RazorpayCheckoutResponse) => void | Promise<void>;
  modal?: { ondismiss?: () => void };
}

interface RazorpayCheckoutInstance {
  open: () => void;
}

declare global {
  interface Window {
    Razorpay: new (options: RazorpayConstructorOptions) => RazorpayCheckoutInstance;
  }
}

function loadRazorpayScript(): Promise<boolean> {
  return new Promise((resolve) => {
    if (window.Razorpay) return resolve(true);
    const script = document.createElement('script');
    script.src = 'https://checkout.razorpay.com/v1/checkout.js';
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
}

export default function BuyerPage() {
  const [step, setStep] = useState<Step>('identify');
  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [city, setCity] = useState('');
  const [session, setSession] = useState<AgentSessionState | null>(null);
  const [query, setQuery] = useState('wireless mouse under ₹1000 that is in stock');
  const [explanation, setExplanation] = useState<string | null>(null);
  const [checkout, setCheckout] = useState<CheckoutResult | null>(null);
  const [auditTrail, setAuditTrail] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [payMessage, setPayMessage] = useState<string | null>(null);

  const reset = () => {
    setStep('identify');
    setSession(null);
    setExplanation(null);
    setCheckout(null);
    setAuditTrail([]);
    setError(null);
    setPayMessage(null);
  };

  const handleIdentify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const buyer = await commerceApi.identifyBuyer(phone.trim(), name.trim() || undefined);
      const newSession = await commerceApi.createSession(buyer.id, undefined, 'web');
      setSession(newSession);
      setStep('search');
    } catch (err) {
      setError(err instanceof CommerceApiError ? err.message : 'Could not start a session.');
    } finally {
      setBusy(false);
    }
  };

  const handleFindAndBuy = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!session) return;
    setError(null);
    setBusy(true);
    try {
      const result = await commerceApi.buyBestAvailable(
        session.session_id,
        query.trim(),
        city.trim() || undefined
      );
      if (!result.ok || !result.checkout?.ok) {
        setError(result.message || result.checkout?.message || 'No match found for that request.');
        setBusy(false);
        return;
      }
      setExplanation(result.explanation || null);
      setCheckout(result.checkout);
      setStep('reviewing');
    } catch (err) {
      setError(
        err instanceof CommerceApiError ? err.message : 'Something went wrong while searching.'
      );
    } finally {
      setBusy(false);
    }
  };

  const handleConfirm = async () => {
    if (!session) return;
    setError(null);
    setBusy(true);
    try {
      const result = await commerceApi.confirm(session.session_id);
      if (result.ok) {
        setStep('confirmed');
      } else {
        setError(result.message || 'This order could not be confirmed.');
      }
    } catch (err) {
      setError(err instanceof CommerceApiError ? err.message : 'Could not confirm the order.');
    } finally {
      setBusy(false);
    }
  };

  const handlePay = async () => {
    if (!session) return;
    setError(null);
    setBusy(true);
    setStep('paying');
    try {
      const payResult = await commerceApi.pay(session.session_id);
      if (!payResult.ok) {
        setError(payResult.message || 'Could not start payment.');
        setStep('confirmed');
        setBusy(false);
        return;
      }

      if (!payResult.razorpay_key_id || payResult.razorpay_key_id.includes('placeholder')) {
        // No real Razorpay test-mode keys configured in this environment.
        // See backend/.env.example — this is expected out of the box.
        setPayMessage(
          `Test payment session created (Razorpay order ${payResult.razorpay_order_id}). ` +
            `Real Razorpay Checkout will open automatically once RAZORPAY_KEY_ID/SECRET are set in backend/.env.local.`
        );
        setBusy(false);
        return;
      }
      const razorpayKeyId = payResult.razorpay_key_id;

      const scriptLoaded = await loadRazorpayScript();
      if (!scriptLoaded) {
        setError('Could not load Razorpay Checkout — check your network connection.');
        setBusy(false);
        return;
      }

      const rzp = new window.Razorpay({
        key: razorpayKeyId,
        amount: payResult.amount_paise,
        currency: payResult.currency,
        order_id: payResult.razorpay_order_id,
        name: 'Bazaar Mitra',
        description: checkout?.public_order_id,
        handler: async (response: RazorpayCheckoutResponse) => {
          try {
            await commerceApi.verifyPayment(
              payResult.payment_id!,
              response.razorpay_payment_id,
              response.razorpay_signature
            );
            setStep('paid');
            if (checkout?.order_id) {
              const trail = await commerceApi.getAuditTrail(checkout.order_id);
              setAuditTrail(trail);
            }
          } catch {
            setError('Payment signature could not be verified.');
            setStep('failed');
          }
        },
        modal: {
          ondismiss: () => setBusy(false),
        },
      });
      rzp.open();
      setBusy(false);
    } catch (err) {
      setError(err instanceof CommerceApiError ? err.message : 'Could not start payment.');
      setStep('confirmed');
      setBusy(false);
    }
  };

  const loadAuditTrail = useCallback(async () => {
    if (!checkout?.order_id) return;
    const trail = await commerceApi.getAuditTrail(checkout.order_id);
    setAuditTrail(trail);
  }, [checkout?.order_id]);

  return (
    <main className="mx-auto min-h-screen max-w-2xl px-6 pt-24 pb-16">
      <header className="mb-8">
        <h1 className="text-foreground text-2xl font-semibold tracking-tight">AI Buyer</h1>
        <p className="text-muted-foreground mt-1.5 text-sm">
          Tell it what you want to buy. It searches every local merchant, picks the best available
          option, explains why, and always asks before it pays.
        </p>
      </header>

      {error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive mb-6 rounded-lg border px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {step === 'identify' && (
        <form
          onSubmit={handleIdentify}
          className="border-border bg-card space-y-4 rounded-xl border p-6"
        >
          <div>
            <label className="text-foreground mb-1.5 block text-sm font-medium">Phone number</label>
            <input
              type="tel"
              required
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="9876543210"
              className="border-border bg-background focus:ring-primary/40 w-full rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2"
            />
          </div>
          <div>
            <label className="text-foreground mb-1.5 block text-sm font-medium">
              Name (optional)
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Anita Verma"
              className="border-border bg-background focus:ring-primary/40 w-full rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="bg-primary text-primary-foreground w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {busy ? 'Starting…' : 'Start shopping'}
          </button>
        </form>
      )}

      {step === 'search' && (
        <form
          onSubmit={handleFindAndBuy}
          className="border-border bg-card space-y-4 rounded-xl border p-6"
        >
          <div>
            <label className="text-foreground mb-1.5 block text-sm font-medium">
              What do you want to buy?
            </label>
            <textarea
              required
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              rows={2}
              className="border-border bg-background focus:ring-primary/40 w-full resize-none rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2"
            />
          </div>
          <div>
            <label className="text-foreground mb-1.5 block text-sm font-medium">
              City (optional)
            </label>
            <input
              type="text"
              value={city}
              onChange={(e) => setCity(e.target.value)}
              placeholder="e.g. Bengaluru"
              className="border-border bg-background focus:ring-primary/40 w-full rounded-lg border px-3 py-2 text-sm outline-none focus:ring-2"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="bg-primary text-primary-foreground w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
          >
            {busy ? 'Searching every merchant…' : 'Find the best option'}
          </button>
        </form>
      )}

      {(step === 'reviewing' ||
        step === 'confirmed' ||
        step === 'paying' ||
        step === 'paid' ||
        step === 'failed') &&
        checkout && (
          <div className="space-y-4">
            {explanation && (
              <div className="border-border bg-card rounded-xl border p-5">
                <div className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
                  What I found
                </div>
                <p className="text-foreground text-sm leading-relaxed">{explanation}</p>
              </div>
            )}

            <div className="border-border bg-card rounded-xl border p-5">
              <div className="flex items-baseline justify-between">
                <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
                  Order {checkout.public_order_id}
                </span>
                <StatusBadge
                  ok={!!checkout.policy?.allowed}
                  label={checkout.policy?.allowed ? 'Policy: PASS' : 'Policy: FAIL'}
                />
              </div>
              <div className="text-foreground mt-2 text-2xl font-semibold">
                ₹{checkout.total?.toLocaleString('en-IN')}
              </div>
              {checkout.policy && !checkout.policy.allowed && (
                <ul className="text-destructive mt-2 space-y-0.5 text-xs">
                  {checkout.policy.reasons.map((r, i) => (
                    <li key={i}>• {r}</li>
                  ))}
                </ul>
              )}
              {checkout.policy && checkout.policy.allowed && (
                <p className="text-muted-foreground mt-1.5 text-xs">
                  Within the ₹{checkout.policy.max_transaction_amount.toLocaleString('en-IN')}{' '}
                  transaction limit.
                </p>
              )}
            </div>

            {step === 'reviewing' && checkout.policy?.allowed && (
              <button
                onClick={handleConfirm}
                disabled={busy}
                className="bg-primary text-primary-foreground w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
              >
                {busy ? 'Confirming…' : `Confirm — pay ₹${checkout.total?.toLocaleString('en-IN')}`}
              </button>
            )}

            {(step === 'confirmed' || step === 'paying') && (
              <button
                onClick={handlePay}
                disabled={busy}
                className="bg-primary text-primary-foreground w-full rounded-lg py-2.5 text-sm font-medium disabled:opacity-50"
              >
                {busy ? 'Opening payment…' : 'Pay now'}
              </button>
            )}

            {payMessage && (
              <div className="border-border bg-muted/50 text-muted-foreground rounded-lg border px-4 py-3 text-sm">
                {payMessage}
              </div>
            )}

            {step === 'paid' && (
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-700 dark:text-emerald-400">
                Payment verified — order {checkout.public_order_id} is paid.
              </div>
            )}

            <button
              onClick={loadAuditTrail}
              className="text-muted-foreground hover:text-foreground text-xs underline underline-offset-2"
            >
              Show audit trail for this order
            </button>

            {auditTrail.length > 0 && (
              <div className="border-border bg-card divide-border divide-y rounded-xl border">
                {auditTrail.map((event) => (
                  <div key={event.id} className="px-4 py-3">
                    <div className="flex items-center justify-between">
                      <span className="text-foreground text-xs font-medium">{event.action}</span>
                      <span
                        className={cn(
                          'text-xs',
                          event.success
                            ? 'text-emerald-600 dark:text-emerald-400'
                            : 'text-destructive'
                        )}
                      >
                        {event.success ? 'OK' : 'BLOCKED'}
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-0.5 text-xs">{event.explanation}</p>
                  </div>
                ))}
              </div>
            )}

            <button
              onClick={reset}
              className="text-muted-foreground hover:text-foreground w-full text-center text-xs underline underline-offset-2"
            >
              Start a new session
            </button>
          </div>
        )}
    </main>
  );
}

function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-[11px] font-medium',
        ok
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
          : 'bg-destructive/15 text-destructive'
      )}
    >
      {label}
    </span>
  );
}
