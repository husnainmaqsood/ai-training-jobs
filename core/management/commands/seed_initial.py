"""Idempotent seed of reference data: categories, subscription plans and platform records.

Safe for production: it creates no job listings and no referral URLs. Existing rows are left unchanged
unless --update is given.
"""

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from billing.models import Plan
from jobs.models import Category, Platform
from referrals.models import ReferralProgram

CATEGORIES = [
    ("LLM evaluation & RLHF", "Rating, ranking and writing feedback on AI model responses."),
    ("AI coding & software evaluation", "Writing, reviewing and evaluating code for AI model training."),
    ("Data annotation & labelling", "Labelling text, images, audio or video to build training datasets."),
    ("Domain expert AI training", "Subject-matter experts (STEM, law, medicine, finance…) creating and reviewing AI training data."),
    ("Writing & language", "Writing, editing, translation and linguistic evaluation for AI systems."),
    ("Search & ads evaluation", "Rating search results, ads and content relevance."),
    ("AI red teaming & safety", "Adversarial testing and safety evaluation of AI systems."),
    ("Speech & audio data", "Recording, transcribing and evaluating speech and audio data."),
]

PLANS = [
    dict(
        code="free",
        name="Free",
        tagline="Explore jobs and try AI tailoring",
        price_cents=0,
        interval="none",
        sort_order=0,
        is_default_free=True,
        generations_per_period=2,
        max_master_cvs=1,
        max_saved_jobs=20,
        max_applications=10,
        max_generated_cvs_visible=5,
        section_regenerations_per_cv=0,
        can_export_pdf=True,
        can_export_docx=False,
        detailed_fit_analysis=False,
        can_edit_generated=True,
        premium_tracking=False,
        marketing_features=[
            "Public job discovery & filters",
            "1 saved master CV",
            "2 AI CV generations per month",
            "Basic job-fit summary",
            "PDF export",
            "Save up to 20 jobs, track 10 applications",
        ],
    ),
    dict(
        code="premium_monthly",
        name="Premium Monthly",
        tagline="For active job seekers",
        price_cents=1200,
        interval="month",
        sort_order=1,
        highlight=True,
        generations_per_period=30,
        max_master_cvs=5,
        max_saved_jobs=500,
        max_applications=500,
        max_generated_cvs_visible=500,
        section_regenerations_per_cv=5,
        can_export_pdf=True,
        can_export_docx=True,
        detailed_fit_analysis=True,
        can_edit_generated=True,
        premium_tracking=True,
        marketing_features=[
            "30 AI CV generations per billing period",
            "Up to 5 master CVs & unlimited history",
            "DOCX and PDF exports",
            "Detailed, evidence-based job-fit analysis",
            "Regenerate individual sections",
            "Premium application tracking",
        ],
    ),
    dict(
        code="premium_annual",
        name="Premium Annual",
        tagline="Same Premium features, billed yearly",
        price_cents=9600,
        interval="year",
        sort_order=2,
        generations_per_period=30,
        max_master_cvs=5,
        max_saved_jobs=500,
        max_applications=500,
        max_generated_cvs_visible=500,
        section_regenerations_per_cv=5,
        can_export_pdf=True,
        can_export_docx=True,
        detailed_fit_analysis=True,
        can_edit_generated=True,
        premium_tracking=True,
        marketing_features=["Everything in Premium Monthly", "30 AI CV generations every month", "Billed once a year"],
    ),
]

PLATFORMS = [
    ("Mercor", "https://www.mercor.com", "AI training, evaluation and expert contracting"),
    ("micro1", "https://www.micro1.ai", "AI training and expert talent"),
    ("Alignerr", "https://www.alignerr.com", "AI training and evaluation"),
    ("Outlier", "https://outlier.ai", "AI training and RLHF tasks"),
    ("DataAnnotation", "https://www.dataannotation.tech", "AI training, writing and coding tasks"),
    ("Turing", "https://www.turing.com", "AI training and software engineering"),
    ("Mindrift", "https://mindrift.ai", "AI training data creation"),
    ("OneForma", "https://www.oneforma.com", "Data annotation, transcription, linguistics"),
    ("RWS (TrainAI)", "https://www.rws.com", "AI data services and linguistics"),
    ("TELUS Digital", "https://www.telusdigital.com", "AI data solutions and annotation"),
]


class Command(BaseCommand):
    help = "Seed categories, subscription plans and platform records (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--update", action="store_true", help="Overwrite existing plan values with the defaults")

    def handle(self, *args, update=False, **options):
        for i, (name, desc) in enumerate(CATEGORIES):
            Category.objects.get_or_create(slug=slugify(name), defaults={"name": name, "description": desc, "sort_order": i})
        for plan in PLANS:
            code = plan["code"]
            if update:
                Plan.objects.update_or_create(code=code, defaults=plan)
            else:
                Plan.objects.get_or_create(code=code, defaults=plan)
        for name, url, work in PLATFORMS:
            platform, _ = Platform.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    "name": name,
                    "website": url,
                    "work_types": work,
                    "description": f"{name} is a provider of {work.lower()}. Availability, pay and eligible countries change "
                    "often — always confirm details on the official website before applying.",
                },
            )
            # Placeholder program record so admins see what still needs verifying. No URLs are invented.
            ReferralProgram.objects.get_or_create(platform=platform, defaults={"status": ReferralProgram.Status.UNVERIFIED})
        self.stdout.write(
            self.style.SUCCESS(f"Seeded {Category.objects.count()} categories, {Plan.objects.count()} plans, {Platform.objects.count()} platforms.")
        )
        self.stdout.write("Next: set provider_price_id on paid plans (Stripe price IDs) in the admin.")
