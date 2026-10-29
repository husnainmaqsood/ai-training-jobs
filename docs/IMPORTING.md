# Managing jobs with Excel uploads (Django Admin)

All job data lives in the Django database. The public site reads it on every request, so a successful import is
visible immediately on every page: home statistics, the jobs list, search and filters, sorting, platform and
category pages and counts, job detail pages, "recently added" and the sitemap. There is no page cache to clear,
nothing to rebuild, and no restart needed.

## Upload a file

1. Sign in at **`/admin/`** and open **Jobs**.
2. Click **Import from Excel** (top right).
3. Choose the file and a mode:
   * **Add & update** (default): adds new jobs and updates the ones it recognises. Never closes anything.
   * **Full snapshot**: does the same, *and* closes open listings that aren't in the file (see below).
4. Click **Upload and preview**. **Nothing is saved yet.** The preview shows, row by row, what will be
   **added**, **updated** (and which fields change), **skipped** (unchanged, or repeated in the file) and
   **rejected** (with the reason).
5. Click **Import N jobs** to apply, or **Cancel**.

You land on the **import history** entry, which lists the counts and every rejected row with its reason. All past
imports are under **Jobs → Import history** (or **Import runs** in the admin index).

Download a blank template from the import page (Excel or CSV), or use [`job-import-template.xlsx`](job-import-template.xlsx).
Its second sheet, "How to fill", explains every column.

## Columns

Only `title`, `platform` and `job_link` are required. Header names are flexible: spacing, capitals, `_` and `-`
don't matter, and common alternatives work (e.g. *Company* or *Employer* for `platform`; *URL*, *Link* or
*Application URL* for `job_link`; *Rate* or *Pay* for `hourly_rate`; *Description* for `job_description`;
*Expires* or *Deadline* for `expiry_date`). Unknown columns are ignored. Only the first sheet is read.

| Column | Format |
|---|---|
| `title` | Job title, max 200 characters. |
| `platform` | Company/platform name. Matched to existing platforms ignoring case, spaces and punctuation ("Data Annotation" = "DataAnnotation"); unknown names create a new platform. |
| `job_link` | Full `http(s)://` link to the original listing. |
| `external_id` | Optional. The platform's own job ID; if present it is used to recognise the job in later uploads. |
| `job_description` | Plain text (simple HTML is converted to text). |
| `hourly_rate` | A number (`40` → 40 per hour) or text such as `$40/hr`, `$40–60/hr`, `Up to $100/hr`, `£2,500/month`. Text that can't be read is shown as written but doesn't feed pay sorting. |
| `minimum_rate`, `maximum_rate` | Numbers. Must not be negative; minimum ≤ maximum. |
| `currency` | ISO code (`USD`, `GBP`, `EUR`…) or symbol. Defaults to USD when a rate is given (the preview warns). |
| `rate_period` | `hour`, `task`, `week`, `month`, `year` or `project`. Defaults to `hour`. |
| `category` | Existing category name. If empty or unknown, a category is suggested from the title. |
| `job_type` | `contract`, `freelance`, `hourly`, `part-time`, `full-time`, `internship`. |
| `work_arrangement` | `remote`, `hybrid`, `onsite`. Defaults to remote. |
| `location` | Free text, e.g. `Worldwide` or `Remote — US & Canada`. |
| `eligible_countries` | `Worldwide`, or country names / ISO codes separated by commas (`US, GB, Pakistan`). Unknown countries reject the row. |
| `posted_date` | Date (`YYYY-MM-DD`, `DD/MM/YYYY`, or a real Excel date). Can't be in the future. Empty → the time of import. |
| `expiry_date` | Date. The listing is hidden after this day. |
| `status` | `open` (default for new jobs), `closed`, `draft`, `expired`. |
| `last_verified_at` | Date you last checked the listing is live (or `yes` for today). |
| `referral_link` | Optional referral URL; used only while an active referral program exists for the platform. |

Limits: `.xlsx` or `.csv`, up to 10 MB and 5,000 job rows per file (split larger files). Old `.xls` files are
rejected with instructions to re-save. Nothing is ever fetched from the job links.

## How re-uploads are matched (no duplicates)

Each row is matched to an existing job in this order:

1. **`external_id` + platform**, when the row has an external ID.
2. Otherwise **platform + canonical job link**. The link is compared without `http`/`https`, `www.`, letter case
   of the domain, trailing slashes, `#fragments` and tracking parameters (`utm_*`, `ref`, `source`, `gclid`…).

Matched jobs are **updated in place**: the database ID, the public URL and any saved state stay the same.
Safeguards: a link that already belongs to a job on a *different* platform, or that matches more than one job,
is **rejected** instead of guessed. A row that repeats an earlier row of the same file is skipped.

**Empty cells never erase data.** Only filled-in cells are applied to an existing job, so a sheet without rates
won't wipe the rates you entered earlier. To close a job, set its `status` to `closed`.

## Full snapshot mode

Use it when your sheet is the complete list of currently open jobs. Open listings that aren't in the file are
**closed** (not deleted), so they leave the site but stay in the admin. Safeguards:

* The preview lists every job that would be closed, and you must tick a confirmation box.
* If the data changes between the preview and the confirmation, the import stops and asks you to review again.
* It refuses to run while any row is rejected, because the job on a rejected row would otherwise be closed.
* Only listings managed by uploads or the admin are reconciled; automated feeds (Greenhouse/Lever) and demo
  listings are never touched.

## Consistency and failure handling

* The preview is a dry run; nothing is written until you confirm.
* Confirming applies every valid row inside **one database transaction**. If anything unexpected fails, the whole
  import is rolled back, nothing changes, and the import history records the error.
* Rejected rows are reported and skipped; the valid rows of the same file are still imported (status *Partial*).
* An import can be applied only once; re-submitting the page doesn't apply it again.

## What counts as "open"

A listing is shown, and counted, only when its status is *open*, its expiry date hasn't passed, and its link
isn't flagged broken. Closed, draft, expired and archived listings are excluded from every public page and count.

## Editing in the admin

**Jobs** supports search (title, platform, category, external ID, link, description), filters (status, platform,
category, posted/expiry/verified dates and more), a coloured status column, an "On site" indicator, and bulk
actions: open, close, move to draft, mark verified, expire, archive. Bulk delete asks for confirmation first.
**Platforms** and **Categories** show their live open-job counts.

Access is limited to the site admin (Django Admin login). Anonymous visitors are sent to the admin login page.

## Company logos

Logos are managed in **`/admin/` → Platforms**. Open a platform and use the **Logo** section:

* **Upload logo** — PNG, JPG or WebP, at least 32×32 px (a square icon of 180–512 px looks best). It is trimmed,
  resized to 256 px and re-encoded automatically. SVG isn't accepted (it can contain scripts) — export a PNG.
* **Fetch logo from website** — downloads the official icon the platform's website publishes (its
  apple-touch-icon or favicon). Make sure the platform's *Website* field is its real site first.
* **Remove logo** — the site falls back to coloured initials.

To fetch several at once, tick platforms in the list and choose **Fetch logos from their websites**, or run
`python manage.py fetch_platform_logos` (only platforms without a logo; add `--replace` to refresh all).
New platforms created by a spreadsheet upload show initials until you add a logo. The **logo** filter on the
Platforms list shows which ones still need one.
