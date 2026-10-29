# Job sources, listing verification & referral programs

## Spreadsheet uploads (simplest way to manage listings)

Upload Excel/CSV files in Django Admin: **Jobs → Import from Excel**. Columns, matching rules, the two import
modes and their safeguards are documented in [IMPORTING.md](IMPORTING.md).

**"Verified"** means an admin checked the original listing is live. The statistics count a listing as
*verified open* when it was verified within the last 30 days. Set `last_verified_at` in the sheet, or use the
**Mark as verified now** bulk action on Jobs.

## Rules

* Only official or authorized channels: employers' public job-board APIs, partner feeds you have permission to
  use, admin CSV uploads and manual entry. **No HTML scraping**, no CAPTCHA/anti-bot circumvention, no use that
  violates a site's terms.
* Automated sources do not run until an admin ticks **permission confirmed** after reading the source's terms.
* Never invent listings, pay, company details or referral links. Compensation is shown only when
  `compensation_verified` is set (Lever salary ranges published by the employer are treated as verified).
* "Remote" never implies "worldwide". Eligibility is `worldwide` only when the source says so explicitly,
  `restricted` when countries are named, otherwise `unknown` (shown as "Location eligibility not verified").

## Source kinds

| Kind | Config (`JobSource.config`) | Endpoint |
|---|---|---|
| Greenhouse | `{"board_token": "company"}` | `https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` |
| Lever | `{"site": "company", "eu": false}` | `https://api.lever.co/v0/postings/<site>?mode=json` |
| JSON feed | `{"url": "https://...", "headers": {"Authorization": "Bearer ..."}}` | list, or `{"jobs": [...]}` in the format below |
| CSV | — (upload in Admin → Job sources → *Import CSV*, or `manage.py import_jobs_csv <source_id> file.csv`) | |
| Manual | — | Admin → Jobs → Add |

Optional config keys for any kind: `"publish_immediately": true` (default: imported listings are drafts
flagged for review) and `"default_category": "<category-slug>"`.

Many AI-training platforms (Mercor, micro1, Alignerr, Outlier, DataAnnotation, Turing, Mindrift, OneForma, RWS,
TELUS Digital…) publish roles on their own sites or through ATS providers. Check whether a platform uses
Greenhouse/Lever publicly and whether its terms permit aggregation; otherwise add listings manually with a link
to the official page, or ask the platform for a feed.

## CSV / JSON feed fields

| Field | Required | Notes |
|---|---|---|
| `title` | ✓ | ≤ 200 chars |
| `description` | ✓ | plain text or HTML (converted to text; scripts/styles removed) |
| `application_url` | ✓ | http(s) only |
| `external_id` | recommended | stable id in the source; used for updates & expiry |
| `source_url`, `company_name`, `category` (slug or name) | | |
| `skills`, `tags` | | comma-separated (CSV) or list (JSON) |
| `employment_type` | | contract, freelance, hourly, part_time, full_time, internship |
| `work_arrangement` | | remote (default), hybrid, onsite |
| `eligibility_scope` | | worldwide, restricted, unknown (default: restricted if countries given, else unknown) |
| `eligible_countries`, `excluded_countries` | | ISO alpha-2 codes, e.g. `US, GB, PK` (UK → GB) |
| `experience_level` | | any, entry, mid, senior, expert |
| `compensation_min`, `compensation_max`, `compensation_currency`, `compensation_period`, `compensation_verified` | | period: hour, task, week, month, year, project |
| `published_at`, `expires_at`, `application_deadline` | | ISO dates/datetimes |
| `preferred_qualifications` | | |

Invalid rows are skipped and logged with row number and reasons in the Import run.

## Duplicates, refresh & expiry

* Within a source, `external_id` identifies a listing (unique constraint) — re-imports update it.
* Across sources, a fingerprint of platform + normalized title + application URL (query string stripped)
  detects duplicates; duplicates are counted and not re-created.
* Each successful automated refresh is treated as complete: a listing missing from
  `expire_after_missed_runs` consecutive refreshes (default 2) is expired. Listings that reappear are restored.
* `expire_jobs` expires listings past `expires_at` or `application_deadline`.
* `check_job_links` marks listings whose application URL returns 404/410 as `link_broken` (hidden) and
  `needs_review`. Network errors don't flag listings.
* Users can report listings; three open reports flag a listing for review. Admin actions: publish, mark
  verified, expire, reject, archive, disable (broken link), clear review flag — all audited.
* Demo listings (`seed_demo_jobs`, DEBUG only) are flagged `is_demo`, labelled in the UI, excluded from sitemaps
  and JSON-LD, and hidden whenever `DEBUG=0`.

## Referral programs

Each platform can have referral program records with status, program URL, approved referral URL or a per-job
tracking template (`{url}?ref=CODE`), terms, restrictions, eligible regions, attribution method, commission
rules (+ verified flag) and last verification date.

* A referral link is used **only** when the platform has an `Active` program; otherwise users go to the direct
  application URL. In the Django admin a job-level `referral_url` can't be saved without an active program; the
  simpler admin area and spreadsheet import store it, but it stays unused until referrals are switched on.
* **Admin → Companies & referrals** is the easy way to manage this: per company, set your referral/sign-up link
  and/or a per-job format (`{url}?ref=CODE`), and tick *Use my referral links for this company* (this makes the
  program `Active`; unticking pauses it). Priority for a job's Apply button: the job's own referral link → the
  per-job format applied to the job link → the plain job link. The company's "Visit" button uses the sign-up link.
* Outbound links go through `/go/job/<id>/` and `/go/platform/<slug>/`, which redirect only to stored URLs
  (no open redirect) and record a `ClickEvent`: listing, platform, program, source, link type, page and a
  daily-rotating salted hash — no IP address, user agent or user id is stored.
* Referral links are labelled "Referral link" with a commission disclosure, and links carry `rel="sponsored"`.
* Revenue reporting keeps four separate layers: **clicks** (ours), **reported conversions**, **approved
  commissions** (partner-reported, entered/imported by admins as `ReferralConversion`) and **payouts**
  (`ReferralPayout`, money actually received). Nothing is shown as earned unless the partner reported it.
