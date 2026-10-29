# Billing, plans & quotas

## CV-builder credits (the main paid feature)

The CV Builder (`/cv/builder/`) is sold as **credits**: one credit = one generated CV (either "Create a CV" or
"Create CV from job description"). Editing and PDF/DOCX downloads of that CV are free.

* **Packs** come from `CREDIT_PACKS` (env JSON; defaults: 1 for $2, 5 for $8, 15 for $20) and `CREDIT_CURRENCY`.
  Each pack is a one-time Stripe Checkout payment built with inline `price_data`, so **no Stripe product or price
  IDs are needed** — only `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.
* **Credits are added only by the signed webhook** (`checkout.session.completed` or
  `checkout.session.async_payment_succeeded` with `payment_status=paid`). The number of credits comes from our own
  pack definition, and the paid `amount_total` must cover the pack price; the browser redirect grants nothing.
  The Checkout Session id is stored as the ledger reference, so duplicate or replayed events never double-grant.
* **Spending** (`billing/credits.py`): a credit is reserved under a row lock *before* the AI call and refunded
  exactly once if the AI call fails. Every change is a `CreditTransaction` row (purchase, test grant, use, refund,
  admin adjustment) with the balance after it.
* **Webhook events to enable** for credits: `checkout.session.completed`, `checkout.session.async_payment_succeeded`
  (same endpoint as below).
* **Test mode:** without Stripe keys, set `BILLING_TEST_MODE=1` (default when `DEBUG=1`) to get an
  "Add test credits" button that grants credits with no payment. The page shows a "Test mode" badge. With
  `sk_test_` keys the page shows "Stripe test mode" and real test Checkout is used. With neither, buying is
  disabled and the page says payments aren't set up. Never enable `BILLING_TEST_MODE` in production.
* **Manual adjustments:** Django admin → Credit accounts / transactions (read the ledger; to add credits use
  `python manage.py shell -c "from billing import credits; ..."` → `credits.grant(user, n, "adjust", note=...)`).

The subscription plans described below still work (they gate the older upload-and-tailor workspace at `/cv/`),
but they're no longer linked from the main navigation.

## Stripe setup

1. Create (or use) a Stripe account for the operating entity. Confirm Stripe supports your country of
   incorporation and payouts (see *Alternatives* below).
2. **Products & prices** (Dashboard → Product catalog): create "Premium" with two recurring prices —
   USD 12 / month and USD 96 / year (or your chosen values). Copy each `price_...` ID.
3. **Admin → Plans**: paste the IDs into `provider_price_id` of `premium_monthly` and `premium_annual`.
   The displayed price comes from the plan row; the charged price comes from Stripe — keep them in sync.
   To change a price, create a *new* Stripe price and update the ID (existing subscribers stay on their price
   until you migrate them in Stripe).
4. **Customer portal** (Settings → Billing → Customer portal): enable cancellation (at period end), payment-method
   updates, invoice history and *plan switching* between the two prices. Upgrades/downgrades are performed in the
   portal so proration is handled by Stripe.
5. **API keys**: `STRIPE_SECRET_KEY=sk_live_...` (use `sk_test_...` first).
6. **Webhook endpoint** (Developers → Webhooks → Add endpoint):
   * URL: `https://YOUR_DOMAIN/billing/webhooks/stripe/`
   * Events: `checkout.session.completed`, `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`, `customer.subscription.paused`, `customer.subscription.resumed`,
     `invoice.paid`, `invoice.payment_succeeded`, `invoice.payment_failed`, `invoice.finalized`,
     `invoice.voided`, `invoice.marked_uncollectible`, `charge.refunded`, `refund.created`, `refund.updated`
   * Copy the signing secret into `STRIPE_WEBHOOK_SECRET=whsec_...`.
7. Local testing: `stripe listen --forward-to localhost:8000/billing/webhooks/stripe/` and use the printed
   `whsec_` secret; `stripe trigger customer.subscription.created` etc.
8. Configure Stripe Tax / Smart Retries / dunning emails in the Stripe dashboard as appropriate.

## How state flows

* **Checkout:** `POST /billing/checkout/<plan>/` creates (or reuses) a Stripe customer and a Checkout Session
  (`mode=subscription`, `client_reference_id` and metadata = user id). The success page **never grants access**.
* **Webhooks are authoritative.** `billing/webhooks.py` verifies the signature, stores the event (unique on
  provider + event id, row-locked), processes it inside a transaction and records `processed / ignored / failed`.
  * Duplicate deliveries are acknowledged without re-processing.
  * Out-of-order deliveries: each subscription stores the provider timestamp of the last applied event; older
    events are ignored (and audited).
  * Failures return HTTP 500 so Stripe retries; admins can also re-process failed events from the admin.
* **Reconciliation:** `manage.py reconcile_billing` (daily via beat) re-fetches every non-ended subscription
  from Stripe and repairs drift. Every status change is written to the billing audit log.

## Entitlement rules (`billing/entitlements.py`)

| Subscription status | Access |
|---|---|
| `active`, `trialing` | Premium until `current_period_end` (+24 h leeway for delayed renewal webhooks) |
| `active` with `cancel_at_period_end` | Premium until period end, then Stripe sends `deleted` → Free |
| `past_due` | Premium for `BILLING_PAST_DUE_GRACE_DAYS` (default 3) after the first failed payment, then Free |
| `incomplete`, `incomplete_expired`, `unpaid`, `paused`, `canceled` | Free |

Refunds are recorded on the invoice and audited; they don't revoke access by themselves. To revoke access
with a refund, cancel the subscription in Stripe (the `deleted` webhook downgrades the user).

All checks are server-side: DOCX export, detailed fit analysis, section regeneration, editing, CV/saved-job/
application limits and history visibility are enforced in views/services, not just hidden in templates.

## Quota policy

* **One generation** = one full "tailor this CV to this job" run (includes the one-time CV structuring call).
* The unit is **reserved at submission** inside a transaction that locks the user's `UsageCounter` row
  (`SELECT … FOR UPDATE`), so concurrent requests can't exceed the allowance (covered by a 10-thread test).
* **Failed for a system reason** (provider outage, timeout, rate limit, invalid output after retries, worker
  crash/stuck > 15 min) → refunded automatically, exactly once. Successful generations are never refunded.
* **Automatic retries** within a generation don't consume extra units.
* **"New version"** of an existing tailored CV is a full generation and consumes 1 unit.
* **Section regenerations** don't consume generation units; they're capped per tailored CV by the plan's
  `section_regenerations_per_cv` (and given back if the provider call fails).
* **Periods:** Free = calendar month (UTC). Monthly plans = Stripe billing period. Annual plans = monthly
  windows anchored on the subscription start date (same monthly allowance as monthly plans).
* Every reservation and refund is written to the immutable `UsageLedgerEntry` table (visible to the user on the
  Usage page and to admins).

## Plans (seeded, editable in the admin)

| | Free | Premium Monthly | Premium Annual |
|---|---|---|---|
| Price | $0 | $12 / month | $96 / year (33 % saving shown automatically) |
| Generations | 2 / month | 30 / period | 30 / month |
| Master CVs | 1 | 5 | 5 |
| History visible | latest 5 | 500 | 500 |
| PDF / DOCX | PDF | both | both |
| Detailed fit analysis | – | ✓ | ✓ |
| Section regenerations / CV | 0 | 5 | 5 |
| Saved jobs / tracked applications | 20 / 10 | 500 / 500 | 500 / 500 |

These are starting hypotheses — change them in Admin → Plans without code changes. `seed_initial --update`
resets them to the defaults above.

## Alternatives if Stripe isn't available

Stripe isn't available to businesses incorporated in some countries (e.g. Pakistan at the time of writing).
Options: incorporate where Stripe operates (e.g. Stripe Atlas), or use a **merchant of record** such as Paddle
or Lemon Squeezy, which also handles global sales tax/VAT. To add one:

1. Implement `billing.providers.BillingProvider` (checkout URL, portal URL, cancel/resume, webhook verification,
   fetch subscription) and register it in `_PROVIDERS`.
2. Add a webhook view that verifies that provider's signature and translates its events into calls to
   `sync_subscription` / `sync_invoice` (or a parallel translator) — the entitlement logic is provider-agnostic.
3. Set `BILLING_PROVIDER=<name>` and put the provider's price/plan IDs in `provider_price_id`.

We never store card numbers or CVCs; only provider customer/subscription/invoice IDs and amounts.
