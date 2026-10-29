"""Development-only demo listings. Refuses to run unless DEBUG=True. Every row is flagged is_demo=True."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import Category, Job, Platform, Skill

DEMO = [
    (
        "[DEMO] LLM Response Evaluator (English)",
        "llm-evaluation-rlhf",
        ["RLHF", "English writing", "Critical thinking"],
        "worldwide",
        [],
        "hourly",
        (15, 25),
    ),
    (
        "[DEMO] Python Coding Expert for AI Training",
        "ai-coding-software-evaluation",
        ["Python", "Code review", "Algorithms"],
        "restricted",
        ["US", "GB", "CA", "IN", "PK"],
        "contract",
        (30, 60),
    ),
    (
        "[DEMO] Data Annotator — Image Labelling",
        "data-annotation-labelling",
        ["Data annotation", "Attention to detail"],
        "restricted",
        ["IN", "PK", "PH", "BD"],
        "part_time",
        None,
    ),
    (
        "[DEMO] Medical Doctor — AI Model Reviewer",
        "domain-expert-ai-training",
        ["Medicine", "Clinical reasoning"],
        "restricted",
        ["US"],
        "freelance",
        (60, 120),
    ),
    ("[DEMO] Urdu Language AI Trainer", "writing-language", ["Urdu", "Translation", "Writing"], "unknown", [], "hourly", None),
    (
        "[DEMO] AI Red Teamer — Safety Evaluation",
        "ai-red-teaming-safety",
        ["Red teaming", "Prompt engineering", "LLM"],
        "worldwide",
        ["IR", "KP"],
        "contract",
        (25, 45),
    ),
    ("[DEMO] Search Quality Rater (UK)", "search-ads-evaluation", ["Search evaluation", "English"], "restricted", ["GB"], "part_time", (12, 14)),
    (
        "[DEMO] Machine Learning Engineer — Evaluation Pipelines",
        "ai-coding-software-evaluation",
        ["Python", "PyTorch", "Machine learning", "SQL"],
        "restricted",
        ["US", "GB", "DE", "IN"],
        "contract",
        (50, 90),
    ),
]


class Command(BaseCommand):
    help = "Create clearly-labelled demo job listings for local development (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--clear", action="store_true", help="Delete existing demo listings first")

    def handle(self, *args, clear=False, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to create demo listings when DEBUG is off.")
        if clear:
            Job.objects.filter(is_demo=True).delete()
        platforms = list(Platform.objects.all())
        if not platforms:
            raise CommandError("Run `manage.py seed_initial` first.")
        now = timezone.now()
        for i, (title, cat_slug, skills, scope, countries, emp, comp) in enumerate(DEMO):
            platform = platforms[i % len(platforms)]
            excluded = countries if scope == "worldwide" else []
            eligible = countries if scope == "restricted" else []
            job, created = Job.objects.get_or_create(
                title=title,
                platform=platform,
                is_demo=True,
                defaults=dict(
                    description=(
                        "DEMO LISTING — not a real job. This record exists only to exercise the interface in development.\n\n"
                        f"You will work on {title.replace('[DEMO] ', '').lower()} tasks, following detailed guidelines, writing "
                        "clear rationales and meeting quality targets.\n\nRequirements:\n• "
                        + "\n• ".join(skills)
                        + "\n• Strong written English\n• Reliable internet connection"
                    ),
                    preferred_qualifications="Prior experience in AI training or evaluation work is a plus.",
                    category=Category.objects.filter(slug=cat_slug).first(),
                    employment_type=emp,
                    work_arrangement="remote",
                    eligibility_scope=scope,
                    eligible_countries=eligible,
                    excluded_countries=excluded,
                    compensation_min=comp[0] if comp else None,
                    compensation_max=comp[1] if comp else None,
                    compensation_currency="USD" if comp else "",
                    compensation_period="hour" if comp else "",
                    compensation_verified=bool(comp),
                    application_url=f"{platform.website.rstrip('/')}/",
                    source_url=platform.website,
                    status=Job.Status.PUBLISHED,
                    published_at=now - timedelta(days=i),
                    last_verified_at=now,
                    expires_at=now + timedelta(days=30),
                ),
            )
            if created:
                job.skills.set([Skill.objects.get_or_create(name=s)[0] for s in skills])
                job.refresh_search_vector()
        self.stdout.write(self.style.WARNING(f"{Job.objects.filter(is_demo=True).count()} DEMO listings present (shown only with SHOW_DEMO_JOBS=1)."))
