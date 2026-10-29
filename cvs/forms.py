from django import forms
from django.conf import settings

from .models import MasterCV


class UploadCVForm(forms.Form):
    title = forms.CharField(
        max_length=150, required=False, widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. Software engineer CV"})
    )
    file = forms.FileField(required=False, widget=forms.ClearableFileInput(attrs={"class": "file-input", "accept": ".pdf,.docx"}))
    text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "textarea font-mono text-sm", "rows": 12, "placeholder": "…or paste your CV text here"}),
        max_length=settings.CV_MAX_TEXT_CHARS,
    )
    make_primary = forms.BooleanField(required=False, initial=True, widget=forms.CheckboxInput(attrs={"class": "checkbox"}))

    def clean(self):
        data = super().clean()
        if not data.get("file") and not (data.get("text") or "").strip():
            raise forms.ValidationError("Upload a PDF/DOCX file or paste your CV text.")
        if data.get("file") and (data.get("text") or "").strip():
            raise forms.ValidationError("Please either upload a file or paste text, not both.")
        return data


class EditMasterTextForm(forms.Form):
    title = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"class": "input"}))
    raw_text = forms.CharField(
        widget=forms.Textarea(attrs={"class": "textarea font-mono text-sm", "rows": 24}), max_length=settings.CV_MAX_TEXT_CHARS
    )


class TailorForm(forms.Form):
    master_cv = forms.ModelChoiceField(
        queryset=MasterCV.objects.none(), widget=forms.Select(attrs={"class": "select"}), empty_label=None, label="Your CV"
    )
    target_title = forms.CharField(max_length=200, label="Job title", widget=forms.TextInput(attrs={"class": "input"}))
    target_company = forms.CharField(max_length=150, required=False, label="Company / platform", widget=forms.TextInput(attrs={"class": "input"}))
    job_description = forms.CharField(
        label="Job description",
        max_length=settings.JOB_DESCRIPTION_MAX_CHARS,
        widget=forms.Textarea(attrs={"class": "textarea text-sm", "rows": 14, "placeholder": "Paste the complete job description"}),
    )
    job_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    ai_consent = forms.BooleanField(
        required=False,
        label="I agree that the text of my CV and this job description will be sent to our AI provider to generate the tailored CV.",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["master_cv"].queryset = MasterCV.objects.filter(user=user)

    def clean(self):
        data = super().clean()
        if not self.user.profile.ai_processing_consent_at and not data.get("ai_consent"):
            self.add_error("ai_consent", "Please confirm you agree to AI processing of your CV.")
        return data


class SectionRegenForm(forms.Form):
    section = forms.ChoiceField(choices=[("summary", "summary"), ("skills", "skills"), ("experience", "experience"), ("project", "project")])
    item_id = forms.CharField(required=False, max_length=20)
    instruction = forms.CharField(required=False, max_length=300)
