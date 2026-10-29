# Project status

## Definition of done — where each item stands

| Requirement | Status | Evidence |
|---|---|---|
| Visitor can find and inspect a job | ✅ | `tests/test_jobs.py`; verified in browser |
| User can register and securely upload a CV | ✅ | `tests/test_auth.py`, `tests/test_cv_parsing.py` |
| AI tailors the CV to a job description | ✅ with mock provider · ⚠️ needs `OPENAI_API_KEY` for real AI | `tests/test_tailoring.py`; live test `tests/test_integration_ai.py` (skipped without key) |
| Generated document can be reviewed and downloaded | ✅ | preview/edit/regenerate views; `tests/test_exports.py` (PDF text extraction, DOCX validity) |
| Unsupported factual claims prevented as far as practical | ✅ | structural (ids only) + deterministic fact check; `tests/test_tailoring.py` |
| Free-tier limits enforced on the server | ✅ | `tests/test_quota.py` incl. 10-thread race test |
| Purchase a subscription through a configured provider | ✅ code · ⚠️ needs Stripe keys/prices | `tests/test_billing.py` (Stripe client mocked) |
| Verified billing events control entitlements | ✅ | signature, duplicate, out-of-order, grace, cancel, refund tests |
| Users can manage or cancel subscriptions | ✅ code · ⚠️ needs Stripe portal configured | cancel/resume/portal views + tests |
| Referral links tracked and disclosed | ✅ | `tests/test_referrals.py` |
| Admins manage listings, plans, sources, users | ✅ | Django admin + audit log; `tests/test_isolation_and_admin.py` |
| Job directory with search, company filter, rates, outbound links | ✅ | `tests/test_jobs.py`, `tests/test_spreadsheet.py`; checked in headless Chrome |
| Admin spreadsheet import (xlsx/csv) with preview, duplicate detection, summary, template | ✅ | `tests/test_spreadsheet.py` (parsing, duplicates, skip/update, end-to-end via views) |
| Admin add / edit / verify / close / reopen / delete listings without the database | ✅ | Django Admin → Jobs; `tests/test_spreadsheet.py` |
| Basic directory statistics from real rows, worded as "listings on this site" | ✅ | `jobs/stats.py`; `tests/test_spreadsheet.py::test_directory_stats_come_from_real_rows` |
| Profile with contact details and work experience, saved for CV generation | ✅ | `tests/test_builder.py` (incl. cross-user isolation) |
| CV Builder: Create a CV / Create CV from job description, preview, edit, PDF + DOCX | ✅ with mock · ⚠️ needs `OPENAI_API_KEY` | `tests/test_builder.py` (facts preserved, invented numbers/skills dropped, downloads) |
| Credit-based payment enforced on the server; test mode clearly labelled | ✅ code · ⚠️ needs Stripe keys for real payments | `tests/test_credits.py` (idempotent webhook grants, underpayment rejected, refunds) |
| Company referral links switchable by the admin, disclosed, clicks tracked | ✅ | `tests/test_spreadsheet.py::test_company_referral_switch_controls_apply_redirect`, `tests/test_referrals.py` |
| Automated tests cover critical workflows | ✅ | `pytest`: 181 passed, 1 skipped (live AI) at time of writing |
| Setup and deployment instructions | ✅ | README + `docs/` |
| No secrets exposed | ✅ | env-only config, `.gitignore`, `.env.example` |
| No fake data/placeholder buttons in production | ✅ | demo listings DEBUG-only & labelled; payment/AI buttons disabled with explanation when unconfigured |

## Requires credentials or operator action

| Item | What to do |
|---|---|
| Real AI tailoring & CV Builder | Set `AI_PROVIDER=openai`, `OPENAI_API_KEY`, choose `OPENAI_MODEL`; add a Model price row. Run `RUN_AI_INTEGRATION=1 pytest -m integration`. |
| CV credit payments | `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET`, and enable `checkout.session.completed` and `checkout.session.async_payment_succeeded` on the webhook. No price IDs needed. Set prices with `CREDIT_PACKS`. Keep `BILLING_TEST_MODE=0` in production. |
| Listings | Import your spreadsheet at `/admin/jobs/job/import/` (see docs/IMPORTING.md). |
| Referral links | Admin → Companies & referrals: add your link per company and switch referrals on. |
| Payments | Stripe account, products/prices, portal settings, webhook endpoint & secret ([BILLING.md](BILLING.md)). If Stripe isn't available to your entity, implement a merchant-of-record provider behind `BillingProvider`. |
| Email | SMTP credentials (`EMAIL_*`). In DEBUG, emails print to the console. |
| Real listings | None are seeded (by design). Add sources/listings via the admin; confirm source terms before enabling automated refreshes. |
| Referral revenue | All seeded referral programs are *Not yet verified* with no URLs. Join programs, verify terms, then activate. |
| Legal pages | Privacy policy and terms are templates reflecting actual behaviour; complete bracketed sections with counsel. |

## Verified vs not verified in the build environment

* **Verified:** migrations; full test suite against PostgreSQL; ruff lint/format; `check --deploy`;
  production mode (`DEBUG=0`, hashed static files, gunicorn) serving pages with security headers; end-to-end
  upload → tailor (mock) → preview → PDF download; visual review of key pages in headless Chrome.
* **Not executed:** Docker image build (no Docker daemon available), real OpenAI calls (no key), real Stripe
  API/webhook calls (no account; signatures were verified with Stripe's own signing algorithm in tests),
  SMTP delivery, Celery with a real Redis broker (tasks ran inline).

## Known limitations / next steps

* CV Builder results live in the server-side session only; if the user's session ends before they download, the
  CV is gone (the credit is not refunded). This is deliberate — no generated CVs are stored by default.
* CV generation runs inside the web request (no background worker), so a very slow AI response can hit the
  120 s gunicorn timeout; the credit is then refunded only if the provider call raised an error.
* Spreadsheet import reads the first sheet only and doesn't fetch or check the job links (use
  `check_job_links` for that).
* The older upload-your-CV tailoring workspace (`/cv/`), subscription plans (`/pricing/`) and application tracker
  still work and are tested, but are no longer in the main navigation. They use subscription quotas, not credits.

* Scanned/image-only PDFs aren't supported (no OCR); users are asked to upload DOCX or paste text.
* The fact check is lexical: it blocks new numbers, technologies, skills and moved claims, but cannot judge
  subtle overstatement in wording. Users are told to review every CV.
* The pre-AI "detected structure" preview uses a heuristic parser; the AI re-structures the CV on first
  tailoring.
* Without Redis, rate limits are per-process and AI tasks run inside the web request (gunicorn timeout is
  120 s). Use Redis + a Celery worker in production.
* Job alerts are newsletter-based (email + country/category); there's no per-account saved-search alert UI yet.
* English-only UI; no browser-automation (Playwright) test suite — views are covered with the Django test client.
* Plan price changes in the admin change what is displayed; the charged amount is the Stripe price ID, which must
  be updated alongside.
