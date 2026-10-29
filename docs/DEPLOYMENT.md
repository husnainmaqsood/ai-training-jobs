# Deployment & operations

## Required external services

| Service | Required? | Used for | Config |
|---|---|---|---|
| PostgreSQL 14+ | **Yes** | All data (full-text search uses Postgres features) | `DATABASE_URL` |
| SMTP email provider | **Yes** for production | Verification, password reset, newsletter, job alerts | `EMAIL_*` |
| OpenAI (or compatible) | Yes for AI features | CV structuring & tailoring | `OPENAI_API_KEY`, `OPENAI_MODEL` — see [AI.md](AI.md) |
| Stripe | Yes for paid plans | Checkout, Billing Portal, webhooks | `STRIPE_*` — see [BILLING.md](BILLING.md) |
| Redis | Recommended | Celery broker, shared cache for rate limits | `REDIS_URL` |
| Persistent disk / volume | **Yes** | Uploaded CV files (`PRIVATE_MEDIA_ROOT`) — not web-served | `PRIVATE_MEDIA_ROOT` |

Without Redis, background tasks run inline in the web request (`CELERY_TASK_ALWAYS_EAGER=1`) and rate limits
are per-process. That is fine for a single small instance; use Redis + a worker for anything larger.

## Docker

```bash
cp .env.example .env    # fill in secrets
docker compose up --build
docker compose run --rm web manage seed_initial
docker compose run --rm web manage createsuperuser
```

The image is multi-stage: Node builds Tailwind CSS, then a slim Python image runs gunicorn. The entrypoint
accepts `web` (runs migrations, then gunicorn), `worker`, `beat`, or `manage <command>`.

> The Dockerfile and compose file were written for this project but the image build was **not** executed in
> the development environment (no Docker daemon was running). The same production path — `collectstatic`
> with hashed/compressed static files and gunicorn with `DEBUG=0` — was verified locally.

## Production checklist (any PaaS / VM)

1. Provision managed PostgreSQL and (recommended) Redis.
2. Set environment variables from `.env.example`. Mandatory: `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS`,
   `CSRF_TRUSTED_ORIGINS`, `SITE_URL`, `DATABASE_URL`, `EMAIL_*`, `PRIVATE_MEDIA_ROOT` on a persistent volume.
3. Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`.
4. Release: `python manage.py migrate && python manage.py seed_initial`.
5. Run processes:
   * web: `gunicorn config.wsgi:application -c docker/gunicorn.conf.py`
   * worker: `celery -A config worker -l INFO`
   * beat: `celery -A config beat -l INFO` (exactly one instance)
6. Terminate TLS at your load balancer and forward `X-Forwarded-Proto` (the app sets HSTS, secure cookies and
   SSL redirect when `DEBUG=0`). Set `FORWARDED_ALLOW_IPS` for gunicorn if needed.
7. Run `python manage.py check --deploy`.
8. Configure Stripe webhooks and AI provider (see the dedicated docs), then place a test purchase in Stripe
   test mode before switching to live keys.

## Scheduled jobs

Celery beat (`config/celery.py`) runs:

| Task | Schedule | Management-command equivalent (for cron) |
|---|---|---|
| Refresh due automated job sources | hourly at :15 | `manage.py refresh_job_sources` |
| Expire listings past expiry/deadline | hourly | `manage.py expire_jobs` |
| Check application links (404/410 → disabled + review) | daily 03:30 UTC | `manage.py check_job_links` |
| Reconcile subscriptions with Stripe | daily 04:00 UTC | `manage.py reconcile_billing` |
| Fail & refund stuck generations (>15 min) | every 10 min | — (task only) |
| Weekly job-alert emails | Mondays 08:00 UTC | — (task only) |

If you don't run Celery, add cron entries for the management commands, e.g.

```cron
15 * * * *  cd /app && python manage.py refresh_job_sources
0  * * * *  cd /app && python manage.py expire_jobs
30 3 * * *  cd /app && python manage.py check_job_links
0  4 * * *  cd /app && python manage.py reconcile_billing
```

## Administrator setup

1. `python manage.py createsuperuser` (email + password).
2. Admin → **Plans**: review prices/limits and paste Stripe price IDs into `provider_price_id`.
3. Admin → **Platforms**: verify descriptions and websites; set `last_reviewed_at`.
4. Admin → **Referral programs**: every seeded program is `Not yet verified` with no URLs. Only after you have
   joined a program and confirmed its terms permit this use, add the approved URL and set status `Active`.
5. Admin → **Job sources**: add sources (see [JOB_SOURCES.md](JOB_SOURCES.md)). For automated sources tick
   *permission confirmed* only after checking the source's terms.
6. Admin → **Model prices**: add current per-token prices for the configured model so cost estimates are right.
7. Review queue: Admin → Jobs filtered by *needs review*; use the bulk actions to publish, reject, expire or
   disable listings. Import history is under **Import runs** in `/admin/`; unhandled errors under **System error logs**.

Staff should use separate accounts; every admin create/update/delete and bulk action is written to the
append-only **Audit log**.

## Backups & recovery

* **Database:** enable automated daily backups with point-in-time recovery on your managed Postgres
  (≥7 days retention, ideally 30). For self-managed: `pg_dump -Fc "$DATABASE_URL" > backup-$(date +%F).dump`
  nightly to encrypted off-site storage; restore with `pg_restore --clean -d "$DATABASE_URL" backup.dump`.
* **CV files:** back up the `PRIVATE_MEDIA_ROOT` volume (snapshots or `rsync`/object-storage sync) on the same
  schedule. Backups contain personal data — encrypt them and restrict access.
* **Test restores** quarterly into a staging environment.
* Deleted accounts may persist in backups until those backups expire; state this in your privacy policy.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `RuntimeError: SECRET_KEY must be set` | Set `SECRET_KEY` (required whenever `DEBUG=0`). |
| 400 Bad Request in production | Host missing from `ALLOWED_HOSTS`. CSRF failures → add `https://` origin to `CSRF_TRUSTED_ORIGINS`. |
| Redirect loop behind proxy | Proxy must send `X-Forwarded-Proto: https`; or set `SECURE_SSL_REDIRECT=0` if TLS redirect is done upstream. |
| "AI tailoring isn't configured" | `AI_PROVIDER=openai` and `OPENAI_API_KEY` set? `mock` is refused when `DEBUG=0`. |
| Generations stuck "Queued" | Celery worker not running while `CELERY_TASK_ALWAYS_EAGER=0`. Stuck jobs are failed & refunded after 15 min. |
| Paid plan shows "Coming soon" | Plan has no `provider_price_id`, or Stripe keys missing. |
| Webhooks failing (500) | See Admin → Webhook events → error. Common: price ID not mapped to a plan. Fix, then *Re-process failed events* (Stripe also retries automatically). |
| Subscription not updated after checkout | Check the webhook endpoint URL/secret in Stripe; run `manage.py reconcile_billing`. |
| Uploaded PDF rejected "not enough text" | Image-only/scanned PDF. OCR is not supported; ask the user to upload DOCX or paste text. |
| Imports failing | Admin → Import runs shows per-row and fatal errors (e.g. HTTP 429/403, missing CSV columns). |
| CSS looks unstyled | Run `npm run build:css` and `collectstatic`. |
