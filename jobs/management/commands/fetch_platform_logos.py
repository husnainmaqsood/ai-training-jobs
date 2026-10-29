from django.core.management.base import BaseCommand

from jobs import logos
from jobs.models import Platform


class Command(BaseCommand):
    help = "Fetch each platform's official icon from its website. By default only platforms without a logo."

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="Only these platforms (default: all active platforms)")
        parser.add_argument("--replace", action="store_true", help="Also replace logos that are already set")

    def handle(self, *slugs, replace=False, **options):
        platforms = Platform.objects.filter(is_active=True).order_by("name")
        if slugs:
            platforms = platforms.filter(slug__in=slugs)
        if not replace:
            platforms = platforms.filter(logo_version="")
        for platform in platforms:
            try:
                png, url = logos.fetch_logo(platform)
                logos.save_logo(platform, png, source=url)
            except logos.LogoError as exc:
                self.stdout.write(self.style.WARNING(f"✗ {platform.name}: {exc}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"✓ {platform.name}: {url}"))
