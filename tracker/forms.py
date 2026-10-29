from django import forms

from cvs.models import GeneratedCV

from .models import Application


class ApplicationForm(forms.ModelForm):
    class Meta:
        model = Application
        fields = ["title", "company", "external_url", "status", "generated_cv", "applied_at", "next_step_at", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input"}),
            "company": forms.TextInput(attrs={"class": "input"}),
            "external_url": forms.URLInput(attrs={"class": "input"}),
            "status": forms.Select(attrs={"class": "select"}),
            "generated_cv": forms.Select(attrs={"class": "select"}),
            "applied_at": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "next_step_at": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "textarea", "rows": 5}),
        }
        labels = {"generated_cv": "CV version used", "applied_at": "Date you applied", "next_step_at": "Next step / follow-up date"}

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only the user's own CV versions can be linked (prevents IDOR via form tampering).
        self.fields["generated_cv"].queryset = GeneratedCV.objects.filter(user=user)
        self.fields["generated_cv"].required = False


class StatusForm(forms.Form):
    status = forms.ChoiceField(choices=Application.Status.choices)
    note = forms.CharField(required=False, max_length=500)
