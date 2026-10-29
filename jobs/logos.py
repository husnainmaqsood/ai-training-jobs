"""
Platform logos: validate, normalise and store; optionally fetch the official icon from the platform's own website.

Every image is decoded with Pillow and re-encoded as a PNG of at most 256 px, so whatever was uploaded (or fetched)
is never served as-is. SVG is refused (it can carry scripts); export a PNG instead.
"""

import hashlib
import io
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
from django.db import transaction
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import Platform, PlatformLogo

MAX_PX = 256
MIN_PX = 32
MAX_DOWNLOAD = 3 * 1024 * 1024
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "ICO", "BMP"}
HTTP_TIMEOUT = 10
USER_AGENT = "Mozilla/5.0 (compatible; OrexiaAI-logo-fetch/1.0)"
PLACEHOLDER_HOSTS = {"example.com", "www.example.com"}  # set by imports that only knew the company name


class LogoError(Exception):
    """Message is safe to show to the admin."""


def normalise(data):
    """Bytes of any supported raster image → (PNG bytes, (width, height))."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise LogoError("That file isn't a readable image. Use PNG, JPG or WebP (SVG isn't supported — export a PNG).") from exc
    if img.format not in ALLOWED_FORMATS:
        raise LogoError(f"{img.format} images aren't supported. Use PNG, JPG or WebP.")
    if img.format == "ICO" and getattr(img, "ico", None):
        img = img.ico.getimage(max(img.ico.sizes()))  # the largest icon in the .ico
    img = ImageOps.exif_transpose(img).convert("RGBA")
    box = img.getchannel("A").getbbox()  # trim transparent margins
    if box:
        img = img.crop(box)
    if max(img.size) < MIN_PX:
        raise LogoError(f"The image is too small ({img.width}×{img.height}). Use at least {MIN_PX}×{MIN_PX} px.")
    img.thumbnail((MAX_PX, MAX_PX), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), img.size


@transaction.atomic
def save_logo(platform, data, source=""):
    png, _ = normalise(data)
    PlatformLogo.objects.update_or_create(platform=platform, defaults={"image": png, "source": source[:500]})
    platform.logo_version = hashlib.sha256(png).hexdigest()[:12]
    Platform.objects.filter(pk=platform.pk).update(logo_version=platform.logo_version)
    return platform


@transaction.atomic
def remove_logo(platform):
    PlatformLogo.objects.filter(platform=platform).delete()
    platform.logo_version = ""
    Platform.objects.filter(pk=platform.pk).update(logo_version="")


# --- Fetching from the platform's website ---------------------------------------------------------


class _IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons = []  # (priority, declared size, href)

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        rel, href = a.get("rel", "").lower(), a.get("href", "")
        if not href or href.lower().endswith(".svg") or "svg" in a.get("type", ""):
            return
        sizes = [int(n) for n in re.findall(r"(\d+)x\d+", a.get("sizes", ""))]
        if "apple-touch-icon" in rel:
            self.icons.append((0, max(sizes or [180]), href))
        elif "icon" in rel.split() or rel == "shortcut icon":
            self.icons.append((1, max(sizes or [32]), href))


def _get(url):
    if urlparse(url).scheme not in {"http", "https"}:
        raise LogoError("The website must be an http(s) URL.")
    resp = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True)
    resp.raise_for_status()
    data = resp.raw.read(MAX_DOWNLOAD + 1, decode_content=True)
    if len(data) > MAX_DOWNLOAD:
        raise LogoError("The file is too large.")
    return resp.url, data


def candidate_urls(website):
    """Icon URLs declared by the site (largest first), then the conventional locations."""
    urls = []
    try:
        final_url, html = _get(website)
        parser = _IconLinks()
        parser.feed(html[:500_000].decode("utf-8", errors="replace"))
        for _, _, href in sorted(parser.icons, key=lambda i: (-i[1], i[0])):
            urls.append(urljoin(final_url, href))
        base = final_url
    except (requests.RequestException, LogoError):
        base = website
    urls += [urljoin(base, "/apple-touch-icon.png"), urljoin(base, "/favicon.ico")]
    return list(dict.fromkeys(urls))


def fetch_logo(platform):
    """Download the best icon the platform's website publishes. Returns (png bytes, source url); raises LogoError."""
    if not platform.website or urlparse(platform.website).hostname in PLACEHOLDER_HOSTS:
        raise LogoError(f"{platform.name} has no real website set. Add its website, or upload a logo instead.")
    best = None
    for url in candidate_urls(platform.website):
        try:
            _, data = _get(url)
            png, size = normalise(data)
        except (requests.RequestException, LogoError):
            continue
        if best is None or max(size) > max(best[2]):
            best = (png, url, size)
        if max(size) >= 120:
            break  # good enough; don't download every icon
    if best is None:
        raise LogoError(f"Couldn't find a usable icon on {platform.website}. Upload the logo instead.")
    return best[0], best[1]


def fetch_and_save(platform):
    png, url = fetch_logo(platform)
    return save_logo(platform, png, source=url)
