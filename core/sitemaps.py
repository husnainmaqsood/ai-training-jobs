from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.db.models import Count, Q
from django.urls import reverse

from jobs.models import Category, Job, Platform

MIN_INDEXABLE = 3


class StaticSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        member_pages = ["billing:pricing", "core:how_it_works"] if settings.USER_ACCOUNTS_ENABLED else []
        return [
            "core:home",
            "jobs:list",
            "jobs:platform_list",
            *member_pages,
            "core:faq",
            "core:referral_disclosure",
            "core:privacy",
            "core:terms",
            "core:contact",
        ]

    def location(self, item):
        return reverse(item)


class JobSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.8
    limit = 5000

    def items(self):
        return Job.objects.public().order_by("-published_at")

    def lastmod(self, obj):
        return obj.updated_at


class CategorySitemap(Sitemap):
    changefreq = "daily"
    priority = 0.7

    def items(self):
        public = Job.objects.public().values("pk")
        return Category.objects.annotate(n=Count("jobs", filter=Q(jobs__in=public))).filter(n__gte=MIN_INDEXABLE).order_by("pk")


class PlatformSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.5

    def items(self):
        return Platform.objects.filter(is_active=True).order_by("pk")

    def lastmod(self, obj):
        return obj.jobs.filter(pk__in=Job.objects.public().values("pk")).order_by("-updated_at").values_list("updated_at", flat=True).first()


sitemaps = {"static": StaticSitemap, "jobs": JobSitemap, "categories": CategorySitemap, "platforms": PlatformSitemap}
