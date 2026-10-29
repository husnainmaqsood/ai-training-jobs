"""
Listing ingestion. Only official/authorized channels are supported:

* Greenhouse public Job Board API (boards-api.greenhouse.io) — published by employers for exactly this use.
* Lever public Postings API (api.lever.co/v0/postings).
* Authorized JSON feeds supplied by a partner in our documented format.
* Admin CSV upload.

No HTML scraping, no CAPTCHA/anti-bot circumvention. Automated sources refuse to run unless an admin has
attested that the source's terms permit it (`JobSource.permission_confirmed`).
"""

import csv
import html
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.countries import COUNTRIES, normalize_list

from .models import Category, ImportRun, Job, JobSource, Skill

logger = logging.getLogger(__name__)
HTTP_TIMEOUT = 20
USER_AGENT = "OrexiaAIBot/1.0 (+listing sync; contact via site)"
MAX_ITEMS = 2000


class ImportErrorSafe(Exception):
    pass


@dataclass
class Listing:
    external_id: str
    title: str
    description: str
    application_url: str
    source_url: str = ""
    company_name: str = ""
    location_text: str = ""
    employment_type: str = ""
    work_arrangement: str = ""
    eligibility_scope: str = ""
    eligible_countries: list = field(default_factory=list)
    excluded_countries: list = field(default_factory=list)
    experience_level: str = ""
    category: str = ""
    skills: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    compensation_min: Decimal | None = None
    compensation_max: Decimal | None = None
    compensation_currency: str = ""
    compensation_period: str = ""
    compensation_verified: bool = False
    published_at: datetime | None = None
    expires_at: datetime | None = None
    application_deadline: object = None
    preferred_qualifications: str = ""


# --- Helpers ------------------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1
        if tag == "li":
            self.parts.append("\n• ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    """Convert (possibly entity-escaped) HTML into plain text. We never render third-party HTML."""
    value = html.unescape(value or "")
    parser = _TextExtractor()
    parser.feed(value)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:30000]


def safe_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url[:1000]


COUNTRY_NAME_TO_CODE = {name.lower(): code for code, name in COUNTRIES.items()}
COUNTRY_NAME_TO_CODE.update({"usa": "US", "u.s.": "US", "united states of america": "US", "uk": "GB", "england": "GB", "britain": "GB"})
WORLDWIDE_WORDS = ("worldwide", "anywhere", "global", "any location", "all countries")


def infer_location(text: str):
    """Conservative: only 'worldwide' if the source says so explicitly; countries only if named."""
    t = (text or "").lower()
    arrangement = Job.WorkArrangement.REMOTE if "remote" in t else (Job.WorkArrangement.HYBRID if "hybrid" in t else "")
    if any(w in t for w in WORLDWIDE_WORDS):
        return arrangement or Job.WorkArrangement.REMOTE, Job.Eligibility.WORLDWIDE, []
    codes = sorted({code for name, code in COUNTRY_NAME_TO_CODE.items() if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", t)})
    if codes:
        return arrangement or Job.WorkArrangement.ONSITE, Job.Eligibility.RESTRICTED, codes
    return arrangement or Job.WorkArrangement.ONSITE, Job.Eligibility.UNKNOWN, []


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _dt(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(ts, tz=UTC)
    parsed = parse_datetime(str(value))
    if parsed is None:
        d = parse_date(str(value))
        if d:
            parsed = datetime(d.year, d.month, d.day)
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return parsed


def _get_json(url, headers=None):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}, timeout=HTTP_TIMEOUT)
    if resp.status_code == 429:
        raise ImportErrorSafe("Source rate-limited the request (HTTP 429). Will retry next run.")
    if resp.status_code in (401, 403):
        raise ImportErrorSafe(f"Access denied by source (HTTP {resp.status_code}). Check credentials/permission.")
    resp.raise_for_status()
    return resp.json()


# --- Fetchers -------------------------------------------------------------------------------------


def fetch_greenhouse(source):
    token = (source.config or {}).get("board_token", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", token or ""):
        raise ImportErrorSafe("Greenhouse source needs a valid config.board_token")
    data = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    listings = []
    for j in (data.get("jobs") or [])[:MAX_ITEMS]:
        loc = (j.get("location") or {}).get("name", "")
        arrangement, scope, countries = infer_location(loc)
        listings.append(
            Listing(
                external_id=str(j.get("id")),
                title=j.get("title", ""),
                description=html_to_text(j.get("content", "")),
                application_url=j.get("absolute_url", ""),
                source_url=j.get("absolute_url", ""),
                location_text=loc,
                work_arrangement=arrangement,
                eligibility_scope=scope,
                eligible_countries=countries,
                published_at=_dt(j.get("first_published") or j.get("updated_at")),
            )
        )
    return listings


LEVER_COMMITMENT = {
    "contract": "contract",
    "contractor": "contract",
    "freelance": "freelance",
    "part-time": "part_time",
    "part time": "part_time",
    "full-time": "full_time",
    "full time": "full_time",
    "intern": "internship",
}


def fetch_lever(source):
    site = (source.config or {}).get("site", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", site or ""):
        raise ImportErrorSafe("Lever source needs a valid config.site")
    region = "api.eu.lever.co" if (source.config or {}).get("eu") else "api.lever.co"
    data = _get_json(f"https://{region}/v0/postings/{site}?mode=json")
    listings = []
    for j in (data if isinstance(data, list) else [])[:MAX_ITEMS]:
        cats = j.get("categories") or {}
        loc = " ".join(filter(None, [cats.get("location", ""), " ".join(cats.get("allLocations") or []), j.get("workplaceType", "")]))
        arrangement, scope, countries = infer_location(loc)
        commitment = (cats.get("commitment") or "").lower()
        emp = next((v for k, v in LEVER_COMMITMENT.items() if k in commitment), "")
        desc = j.get("descriptionPlain") or html_to_text(j.get("description", ""))
        for lst in j.get("lists") or []:
            desc += f"\n\n{lst.get('text', '')}\n" + html_to_text(lst.get("content", ""))
        salary = j.get("salaryRange") or {}
        listings.append(
            Listing(
                external_id=str(j.get("id")),
                title=j.get("text", ""),
                description=desc.strip(),
                application_url=j.get("applyUrl") or j.get("hostedUrl", ""),
                source_url=j.get("hostedUrl", ""),
                location_text=loc,
                work_arrangement=arrangement,
                eligibility_scope=scope,
                eligible_countries=countries,
                employment_type=emp,
                published_at=_dt(j.get("createdAt")),
                compensation_min=_decimal(salary.get("min")),
                compensation_max=_decimal(salary.get("max")),
                compensation_currency=(salary.get("currency") or "")[:3],
                compensation_period={"per-hour-wage": "hour", "per-year-salary": "year", "per-month-salary": "month"}.get(
                    salary.get("interval", ""), ""
                ),
                # The employer published this range in its own API, so it counts as verified-at-source.
                compensation_verified=bool(salary.get("min") or salary.get("max")),
            )
        )
    return listings


def listing_from_mapping(row: dict) -> Listing:
    """Shared by JSON feeds and CSV. Field names match docs/JOB_SOURCES.md."""
    get = lambda k: (row.get(k) or "").strip() if isinstance(row.get(k, ""), str) else row.get(k)  # noqa: E731
    split = lambda v: [s.strip() for s in (v.split(",") if isinstance(v, str) else (v or [])) if s and str(s).strip()]  # noqa: E731
    scope = (get("eligibility_scope") or "").lower()
    eligible = normalize_list(get("eligible_countries") or [])
    if scope not in {c.value for c in Job.Eligibility}:
        scope = Job.Eligibility.RESTRICTED if eligible else Job.Eligibility.UNKNOWN
    return Listing(
        external_id=str(get("external_id") or ""),
        title=get("title") or "",
        description=html_to_text(get("description") or ""),
        application_url=get("application_url") or "",
        source_url=get("source_url") or "",
        company_name=get("company_name") or "",
        employment_type=(get("employment_type") or "").lower(),
        work_arrangement=(get("work_arrangement") or "remote").lower(),
        eligibility_scope=scope,
        eligible_countries=eligible,
        excluded_countries=normalize_list(get("excluded_countries") or []),
        experience_level=(get("experience_level") or "").lower(),
        category=get("category") or "",
        skills=split(get("skills")),
        tags=split(get("tags")),
        compensation_min=_decimal(get("compensation_min")),
        compensation_max=_decimal(get("compensation_max")),
        compensation_currency=(get("compensation_currency") or "").upper()[:3],
        compensation_period=(get("compensation_period") or "").lower(),
        compensation_verified=str(get("compensation_verified") or "").lower() in {"1", "true", "yes"},
        published_at=_dt(get("published_at")),
        expires_at=_dt(get("expires_at")),
        application_deadline=parse_date(str(get("application_deadline"))) if get("application_deadline") else None,
        preferred_qualifications=html_to_text(get("preferred_qualifications") or ""),
    )


def fetch_json_feed(source):
    url = safe_url((source.config or {}).get("url", ""))
    if not url.startswith("https://"):
        raise ImportErrorSafe("JSON feed source needs an https config.url")
    data = _get_json(url, headers=(source.config or {}).get("headers"))
    items = data.get("jobs", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ImportErrorSafe("Feed must be a JSON list or an object with a 'jobs' list")
    return [listing_from_mapping(i) for i in items[:MAX_ITEMS] if isinstance(i, dict)]


CSV_REQUIRED = {"title", "description", "application_url"}


def parse_csv(file_obj):
    raw = file_obj.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ImportErrorSafe("CSV is larger than 5 MB")
    text = raw.decode("utf-8-sig", errors="replace") if isinstance(raw, bytes) else raw
    reader = csv.DictReader(io.StringIO(text))
    missing = CSV_REQUIRED - set(reader.fieldnames or [])
    if missing:
        raise ImportErrorSafe(f"CSV is missing required columns: {', '.join(sorted(missing))}")
    return [listing_from_mapping(row) for _, row in zip(range(MAX_ITEMS), reader, strict=False)]


FETCHERS = {
    JobSource.Kind.GREENHOUSE: fetch_greenhouse,
    JobSource.Kind.LEVER: fetch_lever,
    JobSource.Kind.JSON_FEED: fetch_json_feed,
}


# --- Ingestion ------------------------------------------------------------------------------------


def _validate(listing: Listing):
    errors = []
    if not listing.title or len(listing.title) > 200:
        errors.append("title missing or too long")
    if len(listing.description) < 30:
        errors.append("description missing or too short")
    if not safe_url(listing.application_url):
        errors.append("application_url must be an http(s) URL")
    if listing.compensation_min and listing.compensation_max and listing.compensation_min > listing.compensation_max:
        errors.append("compensation_min > compensation_max")
    return errors


def _choice(value, choices, default):
    values = {c.value for c in choices}
    return value if value in values else default


def ingest(source: JobSource, listings, run: ImportRun, *, full_refresh: bool):
    """Create/update listings. `full_refresh` means the feed is complete, so missing items count as missed."""
    now = timezone.now()
    publish = bool((source.config or {}).get("publish_immediately"))
    default_cat = (source.config or {}).get("default_category")
    seen_ids = set()
    for idx, listing in enumerate(listings):
        problems = _validate(listing)
        if problems:
            run.failed_count += 1
            run.errors.append({"row": idx + 1, "external_id": listing.external_id, "title": listing.title[:80], "errors": problems})
            continue
        try:
            with transaction.atomic():
                outcome, job = _upsert(source, listing, publish, default_cat, now)
        except Exception as exc:  # one bad row must not abort the run
            logger.exception("Import row failed")
            run.failed_count += 1
            run.errors.append({"row": idx + 1, "external_id": listing.external_id, "errors": [f"{type(exc).__name__}: {exc}"[:300]]})
            continue
        if job is not None:
            seen_ids.add(job.pk)
        if outcome == "created":
            run.created_count += 1
        elif outcome == "updated":
            run.updated_count += 1
        else:
            run.duplicate_count += 1

    if full_refresh:
        missing = Job.objects.filter(source=source, status=Job.Status.PUBLISHED).exclude(pk__in=seen_ids)
        for job in missing:
            job.missed_refreshes += 1
            fields = ["missed_refreshes", "updated_at"]
            if job.missed_refreshes >= source.expire_after_missed_runs:
                job.status = Job.Status.EXPIRED
                fields.append("status")
                run.expired_count += 1
            Job.objects.filter(pk=job.pk).update(**{f: getattr(job, f) for f in fields if f != "updated_at"}, updated_at=now)


def _upsert(source, listing: Listing, publish, default_cat, now):
    platform = source.platform
    fingerprint = Job.compute_fingerprint(platform.pk, listing.title, listing.application_url)
    job = None
    if listing.external_id:
        job = Job.objects.filter(source=source, external_id=listing.external_id).first()
    if job is None:
        dup = Job.objects.filter(platform=platform, fingerprint=fingerprint).exclude(status=Job.Status.REJECTED).first()
        if dup is not None and (dup.source_id != source.pk or not listing.external_id):
            # Same job already known (another source, manual entry, or repeated CSV row): refresh verification only.
            if dup.source_id == source.pk or dup.source_id is None:
                Job.objects.filter(pk=dup.pk).update(last_verified_at=now, missed_refreshes=0)
            return "duplicate", dup
        job = dup

    created = job is None
    if created:
        job = Job(
            platform=platform,
            source=source,
            external_id=listing.external_id,
            status=Job.Status.PUBLISHED if publish else Job.Status.DRAFT,
            needs_review=not publish,
        )

    if listing.external_id:
        job.external_id = listing.external_id
    job.title = listing.title[:200]
    job.description = listing.description
    job.preferred_qualifications = listing.preferred_qualifications
    job.company_name = listing.company_name[:150]
    job.application_url = safe_url(listing.application_url)
    job.source_url = safe_url(listing.source_url) or job.application_url
    job.employment_type = _choice(listing.employment_type, Job.EmploymentType, job.employment_type or Job.EmploymentType.CONTRACT)
    job.work_arrangement = _choice(listing.work_arrangement, Job.WorkArrangement, Job.WorkArrangement.REMOTE)
    job.eligibility_scope = _choice(listing.eligibility_scope, Job.Eligibility, Job.Eligibility.UNKNOWN)
    job.eligible_countries = listing.eligible_countries
    job.excluded_countries = listing.excluded_countries
    if job.eligibility_scope == Job.Eligibility.RESTRICTED and not job.eligible_countries:
        job.eligibility_scope = Job.Eligibility.UNKNOWN
    job.experience_level = _choice(listing.experience_level, Job.ExperienceLevel, job.experience_level or Job.ExperienceLevel.ANY)
    job.compensation_min, job.compensation_max = listing.compensation_min, listing.compensation_max
    job.compensation_currency = listing.compensation_currency
    job.compensation_period = _choice(listing.compensation_period, Job.CompPeriod, "")
    job.compensation_verified = listing.compensation_verified and bool(listing.compensation_currency)
    job.published_at = listing.published_at or job.published_at
    job.expires_at = listing.expires_at
    job.application_deadline = listing.application_deadline
    job.tags = [t[:40] for t in listing.tags][:15]
    job.last_verified_at = now
    job.missed_refreshes = 0
    if job.status == Job.Status.EXPIRED and not created:
        job.status = Job.Status.PUBLISHED if publish else Job.Status.DRAFT  # reappeared in the source
    cat_slug = listing.category or default_cat
    if cat_slug:
        job.category = Category.objects.filter(slug=cat_slug).first() or Category.objects.filter(name__iexact=cat_slug).first() or job.category
    if job.category is None:
        from .categories import infer_category

        job.category = infer_category(job.title, job.description)
    job.save()
    if listing.skills:
        skills = []
        for name in listing.skills[:25]:
            name = name.strip()[:80]
            skill = Skill.objects.filter(name__iexact=name).first() or Skill.objects.create(name=name)
            skills.append(skill)
        job.skills.set(skills)
        job.refresh_search_vector()
    return ("created" if created else "updated"), job


def run_source(source: JobSource, *, user=None, csv_file=None) -> ImportRun:
    run = ImportRun.objects.create(source=source, triggered_by=user)
    now = timezone.now()
    try:
        if csv_file is not None:
            listings = parse_csv(csv_file)
            full_refresh = False
        elif source.kind in FETCHERS:
            if not source.permission_confirmed:
                raise ImportErrorSafe("Automated retrieval is disabled until an admin confirms the source's terms permit it.")
            if not source.is_active:
                raise ImportErrorSafe("Source is inactive.")
            listings = FETCHERS[source.kind](source)
            full_refresh = True
        else:
            raise ImportErrorSafe("This source has no automated fetcher. Use CSV import or manual entry.")
        ingest(source, listings, run, full_refresh=full_refresh)
        run.status = ImportRun.Status.PARTIAL if run.failed_count else ImportRun.Status.SUCCESS
        source.last_success_at = now
    except (ImportErrorSafe, requests.RequestException, ValueError) as exc:
        run.status = ImportRun.Status.FAILED
        run.errors.append({"fatal": f"{type(exc).__name__}: {exc}"[:500]})
    run.finished_at = timezone.now()
    run.errors = run.errors[:500]
    run.save()
    source.last_run_at = now
    source.save(update_fields=["last_run_at", "last_success_at"])
    return run


# --- Maintenance -----------------------------------------------------------------------------------


def expire_jobs(now=None):
    now = now or timezone.now()
    n = Job.objects.filter(status=Job.Status.PUBLISHED, expires_at__lte=now).update(status=Job.Status.EXPIRED, updated_at=now)
    n += Job.objects.filter(status=Job.Status.PUBLISHED, application_deadline__lt=now.date()).update(status=Job.Status.EXPIRED, updated_at=now)
    return n


def check_link(job: Job):
    """Flag listings whose application link is definitively gone (404/410). Network errors are not flagged."""
    try:
        resp = requests.head(job.application_url, allow_redirects=True, timeout=10, headers={"User-Agent": USER_AGENT})
        if resp.status_code in (403, 405, 501):  # some sites reject HEAD
            resp = requests.get(job.application_url, allow_redirects=True, timeout=10, headers={"User-Agent": USER_AGENT}, stream=True)
            resp.close()
        broken = resp.status_code in (404, 410)
    except requests.RequestException:
        broken = None
    fields = {"link_checked_at": timezone.now()}
    if broken:
        fields.update(link_broken=True, needs_review=True, review_note="Application link returned 404/410")
    elif broken is False and job.link_broken:
        fields.update(link_broken=False)
    Job.objects.filter(pk=job.pk).update(**fields)
    return broken
