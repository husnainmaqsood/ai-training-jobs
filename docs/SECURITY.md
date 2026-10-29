# Security & privacy controls

| Concern | Control | Where |
|---|---|---|
| Authorization | Every private object is fetched filtered by `user=request.user` (404 otherwise); UUID public ids for CVs; job management and imports are Django Admin views (`admin_site.admin_view` + add/change permission checks), and only one admin account can exist; plan features checked in views/services | `cvs/views.py`, `tracker/views.py`, `billing/entitlements.py` |
| IDOR via forms | Form querysets restricted to the user's own objects (CV selector, CV version on applications) | `cvs/forms.py`, `tracker/forms.py` |
| CSRF | Django CSRF on all forms; HTMX sends the token header; only the Stripe webhook is exempt (signature-verified) | `static/js/app.js`, `billing/views.py` |
| XSS | Django autoescaping; third-party HTML converted to text on import (scripts/styles dropped); JSON-LD escaped; strict CSP (`script-src 'self'`, no inline scripts) | `core/middleware.py`, `jobs/importers.py` |
| Clickjacking & headers | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, nosniff, referrer policy, COOP, Permissions-Policy; HSTS, secure cookies and SSL redirect when `DEBUG=0` | `config/settings.py` |
| Open redirect | Outbound `/go/` links resolve only stored URLs | `referrals/views.py` |
| Passwords | Django PBKDF2 hashing, validators (min length 10, common/numeric/similarity) | `config/settings.py` |
| Rate limiting | Login, registration, password reset, verification resend, contact, newsletter, uploads, tailoring, section regeneration, checkout, listing reports (cache-backed; use Redis in production for shared counters) | `core/ratelimit.py` |
| Uploads | 5 MB cap, magic-byte + extension check, DOCX zip-bomb/entry limits, PDF page cap & encrypted-PDF rejection, text length cap; files stored under random names outside any public URL and served only via ownership-checked downloads with `no-store` | `cvs/parsing.py`, `cvs/views.py` |
| Prompt injection | Untrusted content delimited and declared as data; deterministic fact check makes injected "add skill X" instructions ineffective | `cvs/prompts.py`, `cvs/validation.py` |
| Secrets | Only from environment; `.env` git-ignored; never sent to the browser; log filter redacts API keys, emails and phone numbers | `.env.example`, `core/logging.py` |
| Payments | No card data touches the server (Stripe Checkout); only provider ids stored; webhook signatures verified; idempotent processing | `billing/` |
| Audit | Append-only audit log for admin create/update/delete & bulk actions, billing state changes, account deletion | `core/audit.py`, `core/admin_mixins.py` |
| Error logging | Unhandled exceptions stored without request bodies; visible in Django Admin (System error logs) | `core/middleware.py` |
| Admin data minimisation | Admin screens show CV metadata only (no CV text) and no tracker notes | `cvs/admin.py`, `tracker/admin.py` |
| Analytics | First-party click counts with daily-rotating salted hashes; no third-party trackers or advertising cookies | `referrals/services.py` |

## Data retention & deletion

| Data | Retention |
|---|---|
| Account, CVs (files + text), tailored CVs, saved jobs, tracker | Until the user deletes them or the account. Account deletion removes all of it (files included) and cancels live subscriptions. |
| Billing records (invoices, subscriptions) | Deleted with the account locally; Stripe retains its own records as required by law. Export what your accountant needs before deletion if required in your jurisdiction. |
| AI usage records | Kept (no content); user link set to null on account deletion. |
| Click events | `RETENTION_CLICKS_DAYS` (default 400) |
| Error logs | `RETENTION_ERRORS_DAYS` (default 90) |
| Webhook payloads | Payload cleared after `RETENTION_WEBHOOK_PAYLOAD_DAYS` (default 400); event id/status kept for idempotency |
| Unconfirmed newsletter sign-ups | 30 days |
| Job descriptions of failed generations | 30 days |

`manage.py purge_expired_data` enforces this (scheduled daily via Celery beat).

## CV data and AI providers

CV content is sent to the AI provider only after explicit consent and only when the user requests tailoring.
Emails and phone numbers are removed first; tailoring requests omit name and contact details. CVs are never
used for training. Before launch, review your provider's data-processing terms (DPA, retention, region) and
complete the bracketed sections of the privacy policy template (`templates/core/privacy.html`) with counsel.

## Reporting vulnerabilities

Publish a security contact (e.g. `security@yourdomain`) and add it to `CONTACT_EMAIL` or a `security.txt`.
