from django import forms

from core.countries import FILTER_CHOICES

from .models import Category, Job, JobReport, Platform, Skill


class JobFilterForm(forms.Form):
    SORTS = [("newest", "Newest"), ("relevance", "Best match"), ("pay", "Highest pay")]

    q = forms.CharField(
        required=False, max_length=120, widget=forms.SearchInput(attrs={"placeholder": "Search by title, skill or company", "class": "input pl-9"})
    )
    country = forms.ChoiceField(required=False, choices=[("", "Any country")] + FILTER_CHOICES, widget=forms.Select(attrs={"class": "select"}))
    category = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "select"}))
    skill = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "select"}))
    platform = forms.ChoiceField(required=False, widget=forms.Select(attrs={"class": "select"}))
    employment_type = forms.ChoiceField(
        required=False, choices=[("", "Any type")] + Job.EmploymentType.choices, widget=forms.Select(attrs={"class": "select"})
    )
    experience = forms.ChoiceField(
        required=False, choices=[("", "Any level")] + Job.ExperienceLevel.choices, widget=forms.Select(attrs={"class": "select"})
    )
    remote_only = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={"class": "checkbox"}))
    worldwide_only = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "checkbox"}))
    min_hourly = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=1000,
        decimal_places=0,
        label="Min. USD/hour",
        widget=forms.NumberInput(attrs={"class": "input", "placeholder": "e.g. 20"}),
    )
    sort = forms.ChoiceField(required=False, choices=SORTS, widget=forms.Select(attrs={"class": "select"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].choices = [("", "All categories")] + list(Category.objects.values_list("slug", "name"))
        self.fields["platform"].choices = [("", "All companies")] + list(Platform.objects.filter(is_active=True).values_list("slug", "name"))
        self.fields["skill"].choices = [("", "Any skill")] + list(
            Skill.objects.filter(jobs__status=Job.Status.PUBLISHED).distinct().values_list("slug", "name")
        )


class JobReportForm(forms.ModelForm):
    class Meta:
        model = JobReport
        fields = ["reason", "details"]
        widgets = {
            "reason": forms.Select(attrs={"class": "select"}),
            "details": forms.Textarea(attrs={"rows": 3, "class": "textarea", "placeholder": "What's wrong with this listing? (optional)"}),
        }
