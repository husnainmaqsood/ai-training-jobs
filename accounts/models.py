from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified", True)
        current = self.get_admin()
        if current is not None:
            raise ValueError(f"The site already has an admin ({current.email}). Use `manage.py set_admin <email>` to hand the role over.")
        return self._create_user(email, password, **extra)

    def get_admin(self):
        """The site's single admin, or None."""
        return self.filter(is_staff=True).first()


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)
    email_verified = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # The site has exactly one admin: at most one staff row, and only staff may be superuser.
            models.UniqueConstraint(fields=["is_staff"], condition=Q(is_staff=True), name="single_admin"),
            models.CheckConstraint(condition=Q(is_superuser=False) | Q(is_staff=True), name="superuser_is_admin"),
        ]

    def __str__(self):
        return self.email


class Profile(models.Model):
    class ExperienceLevel(models.TextChoices):
        ENTRY = "entry", "Entry level"
        MID = "mid", "Mid level"
        SENIOR = "senior", "Senior"
        EXPERT = "expert", "Expert / PhD"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=150, blank=True)
    headline = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2 code")
    preferred_regions = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    skills = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    experience_level = models.CharField(max_length=20, choices=ExperienceLevel.choices, blank=True)
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    # Saved once, reused by the CV builder.
    phone = models.CharField(max_length=40, blank=True)
    location = models.CharField(max_length=120, blank=True, help_text="City, country")
    links = models.CharField(max_length=300, blank=True, help_text="LinkedIn, portfolio or GitHub URLs, comma separated")
    education = models.TextField(blank=True, help_text="One qualification per line")
    additional_info = models.TextField(blank=True, help_text="Certifications, languages, tools, achievements — one per line")
    # Explicit consent to send CV contents to the configured AI provider.
    ai_processing_consent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.user}"


class WorkExperience(models.Model):
    """A role the user has held. Facts here are copied verbatim into generated CVs; AI only rewords descriptions."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="work_experiences")
    company = models.CharField(max_length=150)
    title = models.CharField("job title", max_length=150)
    location = models.CharField(max_length=120, blank=True)
    start_date = models.CharField(max_length=40, help_text="e.g. Jan 2021")
    end_date = models.CharField(max_length=40, blank=True, help_text="Leave empty if this is your current role")
    description = models.TextField(blank=True, help_text="What you did and achieved — one point per line")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self):
        return f"{self.title} @ {self.company}"

    @property
    def end_display(self):
        return self.end_date or "Present"
