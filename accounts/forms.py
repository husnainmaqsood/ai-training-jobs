from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from core.countries import COUNTRY_CHOICES

from .models import Profile, User, WorkExperience

INPUT = "input"


class StyledMixin:
    def _style(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "checkbox")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "select")
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", "textarea")
            else:
                widget.attrs.setdefault("class", INPUT)


class LoginForm(StyledMixin, AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class RegistrationForm(StyledMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    full_name = forms.CharField(label="Full name", max_length=150, required=False)
    country = forms.ChoiceField(choices=[("", "Select your country (optional)")] + COUNTRY_CHOICES, required=False)
    password1 = forms.CharField(
        label="Password", help_text="At least 10 characters.", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    accept_terms = forms.BooleanField(label="I agree to the Terms of Service and Privacy Policy")

    field_order = ["full_name", "email", "password1", "password2", "country", "accept_terms"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        if p1:
            try:
                password_validation.validate_password(p1, User(email=data.get("email", "")))
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        user = User.objects.create_user(email=self.cleaned_data["email"], password=self.cleaned_data["password1"])
        profile = user.profile
        profile.full_name = self.cleaned_data.get("full_name", "")
        profile.country = self.cleaned_data.get("country", "")
        profile.save()
        return user


class ProfileForm(StyledMixin, forms.ModelForm):
    country = forms.ChoiceField(
        label="Country of residence", choices=[("", "—")] + COUNTRY_CHOICES, required=False, help_text="Used to show jobs open to you."
    )
    skills_text = forms.CharField(
        label="Skills", required=False, help_text="Comma separated, e.g. Python, RLHF, technical writing", widget=forms.Textarea(attrs={"rows": 2})
    )

    class Meta:
        model = Profile
        fields = ["full_name", "headline", "phone", "location", "links", "country", "education", "additional_info"]
        labels = {
            "headline": "Professional headline",
            "links": "Links",
            "education": "Education",
            "additional_info": "Additional details to include",
        }
        help_texts = {
            "headline": "e.g. Software engineer and AI data trainer",
            "education": "One per line, e.g. BSc Computer Science, University of Lahore, 2019",
        }
        widgets = {"education": forms.Textarea(attrs={"rows": 3}), "additional_info": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["skills_text"].initial = ", ".join(self.instance.skills)
        self.fields["full_name"].label = "Full name"
        self.fields["full_name"].required = True  # every CV starts with it
        self.fields["phone"].widget.attrs.setdefault("autocomplete", "tel")
        self.fields["full_name"].widget.attrs.setdefault("autocomplete", "name")
        self._style()

    def clean_skills_text(self):
        skills = [s.strip()[:60] for s in self.cleaned_data.get("skills_text", "").split(",") if s.strip()]
        return list(dict.fromkeys(skills))[:50]

    def save(self, commit=True):
        profile = super().save(commit=False)
        profile.skills = self.cleaned_data.get("skills_text", [])
        if commit:
            profile.save()
        return profile


class WorkExperienceForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = WorkExperience
        fields = ["company", "title", "location", "start_date", "end_date", "description"]
        widgets = {
            "start_date": forms.TextInput(attrs={"placeholder": "e.g. Jan 2021"}),
            "end_date": forms.TextInput(attrs={"placeholder": "Present"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "One point per line — what you did, tools you used, results"}),
            "location": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
        labels = {"start_date": "Start", "end_date": "End"}
        help_texts = {"start_date": "", "end_date": "Leave empty if current", "description": ""}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


def work_experience_formset(user, data=None):
    """Existing roles, plus one blank role for a user who hasn't added any yet."""
    extra = 0 if user.work_experiences.exists() else 1
    formset_class = forms.inlineformset_factory(
        User, WorkExperience, form=WorkExperienceForm, extra=extra, can_delete=True, max_num=20, validate_max=True
    )
    return formset_class(data, instance=user, prefix="exp")


class DeleteAccountForm(StyledMixin, forms.Form):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    confirm = forms.CharField(label='Type "DELETE" to confirm')

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self._style()

    def clean_password(self):
        if not self.user.check_password(self.cleaned_data["password"]):
            raise forms.ValidationError("Incorrect password.")
        return self.cleaned_data["password"]

    def clean_confirm(self):
        if self.cleaned_data["confirm"].strip() != "DELETE":
            raise forms.ValidationError("Please type DELETE in capital letters.")
        return self.cleaned_data["confirm"]
