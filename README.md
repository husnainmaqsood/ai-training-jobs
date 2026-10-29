# Orexia AI

> A curated directory of remote AI-training and LLM jobs, managed entirely from Excel uploads in Django Admin.

A Django app that lists AI-training jobs (Mercor, Micro1, Turing, DataAnnotation, AfterQuery, Ethos AI…) with
rates and direct/referral links, and shows a few honest directory statistics. Visitors need no account:
navigation is **Home · Explore Jobs · Platforms**. The site owner manages everything at **`/admin/`**.

**Current release: directory only.** Public sign-up/login, profiles, the CV builder, billing, saved jobs and the
application tracker are switched off with `USER_ACCOUNTS_ENABLED=0` (the default): their URLs return 404 and the
UI hides them. The code and its tests remain, for a later phase.

| Area | What's implemented |
|---|---|
| Directory | Search, company filter chips, rate display, verified/closed labels, outbound Apply links with disclosed referrals |
| Django Admin (`/admin/`) | **Import from Excel** on Jobs: flexible columns, dry-run preview, add & update or full-snapshot mode, duplicate-safe matching, one-transaction apply, import history with row errors, template download ([docs/IMPORTING.md](docs/IMPORTING.md)); job search/filters/status badges/bulk actions; live open-job counts per platform and category |
| CV Builder (`/cv/builder/`, off in this release) | Saved profile + work experience → "Create a CV" or "Create CV from job description" (OpenAI), preview, edit, PDF/DOCX; nothing invented, nothing stored |
| Credits | One-time Stripe Checkout packs, webhook-only crediting, automatic refund on failure, clearly labelled test mode |
| Job board | Postgres full-text search, filters (country eligibility, category, skill, platform, type, level, verified pay, remote, worldwide), newest/relevance/pay sorting, HTMX filtering, pagination, SEO landing pages, JSON-LD, sitemap |
| Listings ingestion | Greenhouse & Lever public APIs, authorized JSON feeds, admin CSV import, duplicate detection, expiry (dates + missed refreshes), broken-link checks, import logs, moderation actions |
| CV tailoring | PDF/DOCX/paste input, safe parsing, AI structuring, job-fit analysis with verified evidence, tailored CV rebuilt from original facts, deterministic fact check, section regeneration, editing, PDF/DOCX export |
| AI layer | Central service, structured outputs + Pydantic validation, retries/timeouts, usage & cost tracking, per-user and global limits, PII redaction |
| Billing | Provider interface + Stripe Checkout/Billing Portal, signed webhooks, idempotent & out-of-order-safe processing, grace periods, refunds, invoices, reconciliation, server-side quotas with row locking |
| Referrals | Approved-program-only referral links, first-party click tracking without PII, clicks vs reported/approved/paid conversions vs payouts |
| Accounts | Email login, verification, password reset, profile, usage ledger, account deletion |
| Tracker | 9 statuses, notes, dates, linked CV version, timeline |
| Admin | Django admin with audit log and import history |

## Quick start (local)

Requirements: Python 3.12+, PostgreSQL 14+, Node 18+ (only to rebuild CSS). Redis is optional.

```bash
git clone <repo> aitrainjobs && cd aitrainjobs
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
createdb aitrainjobs

cat > .env <<'ENV'
DEBUG=1
AI_PROVIDER=mock            # deterministic dev stand-in; set openai + OPENAI_API_KEY for real AI
DATABASE_URL=postgres:///aitrainjobs
SITE_URL=http://localhost:8000
ENV

python manage.py migrate
python manage.py seed_initial          # categories, plans, platform records (safe in production)
python manage.py seed_demo_jobs        # DEBUG only — clearly labelled demo listings
python manage.py createsuperuser        # the site's one and only admin
python manage.py runserver
```

The site has exactly **one admin** (enforced in the database). To hand the role to another registered account run
`python manage.py set_admin <email>`; the previous admin becomes a regular user. Demo listings from `seed_demo_jobs`
only appear with `SHOW_DEMO_JOBS=1`.

Open http://localhost:8000/admin/ and log in with the superuser. **Jobs → Import from Excel** uploads your sheet
(see [docs/IMPORTING.md](docs/IMPORTING.md)); referral links are under **Referral programs**.
Emails (verification, password reset) print to the console in DEBUG. With `DEBUG=1` and no Stripe keys the CV
Builder runs in **test mode** ("Add test credits", no payment) — see [docs/BILLING.md](docs/BILLING.md).

### Credentials needed for production

| Integration | Settings | Without it |
|---|---|---|
| OpenAI (CV Builder) | `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` | Generate button disabled with a notice |
| Stripe (credit packs) | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (+ optional `CREDIT_PACKS`) | Buying disabled ("payments aren't set up"), unless `BILLING_TEST_MODE=1` |
| Email (sign-up verification, password reset) | `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Emails can't be delivered |

Rebuild CSS after changing templates: `npm install && npm run build:css` (the built `static/css/app.css` is committed).
The Geist and Geist Mono fonts are self-hosted from `static/fonts/` (SIL OFL), so no third-party font requests are made.

## Tests

```bash
pytest                       # 180+ tests, mocked AI and Stripe — no external calls, no cost
ruff check . && ruff format --check .
RUN_AI_INTEGRATION=1 OPENAI_API_KEY=sk-... pytest -m integration   # optional live AI test (small cost)
```

Tests need a local PostgreSQL (pytest-django creates a throwaway `test_aitrainjobs` database).

## Documentation

* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — production deployment, Docker, scheduled jobs, backups, admin setup, troubleshooting
* [docs/AI.md](docs/AI.md) — AI provider setup, tailoring pipeline, factual-preservation design, match score, cost controls
* [docs/BILLING.md](docs/BILLING.md) — Stripe setup, webhooks, plans, quota policy, alternative providers
* [docs/IMPORTING.md](docs/IMPORTING.md) — Excel/CSV import in Django Admin: columns, matching, modes, safeguards
* [docs/JOB_SOURCES.md](docs/JOB_SOURCES.md) — listing sources, CSV/feed formats, compliance rules, referral programs
* [docs/SECURITY.md](docs/SECURITY.md) — security & privacy controls, data retention
* [docs/STATUS.md](docs/STATUS.md) — what is complete, what needs credentials, known limitations

## Project layout

```
config/      settings, URLs, Celery app (beat schedule)
core/        pages, newsletter, SEO, audit/error logs, rate limiting, feature gate
accounts/    custom email user, profile, verification, deletion
jobs/        platforms, categories, skills, listings, sources/importers, reports, saved jobs
referrals/   referral programs, click events, partner conversions & payouts
ai/          provider abstraction, usage/cost records, dev mock
cvs/         master CVs, parsing, prompts, tailoring pipeline, fact check, exports
billing/     plans, subscriptions, invoices, webhooks, entitlements & quotas, reconciliation
tracker/     applications and status timeline
templates/   Django templates (Tailwind + HTMX)
tests/       pytest suite + fictional CV fixtures
```
