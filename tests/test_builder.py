import io
import re
from unittest import mock

import pytest
from django.urls import reverse

from accounts.models import WorkExperience
from ai.service import AIRateLimited
from billing import credits
from billing.models import CreditTransaction
from cvs.builder_views import SESSION_KEY

pytestmark = pytest.mark.django_db

JD = (
    "We are hiring an AI Trainer to evaluate Python code produced by large language models. You will write "
    "clear feedback, rank responses and review pandas data pipelines. Kubernetes and Rust experience required."
)


@pytest.fixture
def ready_user(user):
    p = user.profile
    p.full_name, p.phone, p.location = "Alice Example", "+92 300 0000000", "Lahore, Pakistan"
    p.headline, p.skills = "Data analyst", ["Python", "pandas", "SQL"]
    p.education = "BSc Computer Science, University of the Punjab, 2018"
    p.additional_info = "Fluent in English and Urdu"
    p.save()
    WorkExperience.objects.create(
        user=user,
        company="Example Analytics Ltd",
        title="Data Analyst",
        start_date="Jan 2020",
        end_date="",
        description="Built Python reports with pandas\nReduced report time by 40%",
    )
    return user


def give(user, n=1):
    credits.grant(user, n, CreditTransaction.Kind.ADJUST)


# --- Profile --------------------------------------------------------------------------------------


def profile_post(**overrides):
    data = {
        "full_name": "Alice Example",
        "headline": "",
        "phone": "123",
        "location": "",
        "links": "",
        "country": "",
        "education": "",
        "additional_info": "",
        "skills_text": "Python, SQL, Python",
        "exp-TOTAL_FORMS": "1",
        "exp-INITIAL_FORMS": "0",
        "exp-MIN_NUM_FORMS": "0",
        "exp-MAX_NUM_FORMS": "20",
        "exp-0-company": "Acme",
        "exp-0-title": "Annotator",
        "exp-0-start_date": "2021",
        "exp-0-end_date": "",
        "exp-0-location": "",
        "exp-0-description": "Labelled data",
    }
    data.update(overrides)
    return data


def test_profile_saves_details_and_experience(client_user, user):
    resp = client_user.post(reverse("accounts:profile"), profile_post())
    assert resp.status_code == 302
    user.profile.refresh_from_db()
    assert user.profile.full_name == "Alice Example" and user.profile.phone == "123"
    assert user.profile.skills == ["Python", "SQL"]
    exp = WorkExperience.objects.get(user=user)
    assert (exp.company, exp.title, exp.end_display) == ("Acme", "Annotator", "Present")


def test_profile_can_delete_roles_and_cannot_touch_other_users(client_user, user, other_user):
    mine = WorkExperience.objects.create(user=user, company="Mine", title="T", start_date="2020")
    theirs = WorkExperience.objects.create(user=other_user, company="Theirs", title="T", start_date="2020")
    data = profile_post(
        **{
            "exp-TOTAL_FORMS": "2",
            "exp-INITIAL_FORMS": "1",
            "exp-0-id": str(mine.pk),
            "exp-0-user": str(user.pk),
            "exp-0-company": "Mine",
            "exp-0-DELETE": "on",
            "exp-1-id": str(theirs.pk),
            "exp-1-company": "Hacked",
            "exp-1-title": "x",
            "exp-1-start_date": "x",
        }
    )
    client_user.post(reverse("accounts:profile"), data)
    assert not WorkExperience.objects.filter(pk=mine.pk).exists()
    theirs.refresh_from_db()
    assert theirs.company == "Theirs" and theirs.user == other_user


# --- Builder ----------------------------------------------------------------------------------------


def test_builder_page_is_public_and_generation_needs_login(client):
    assert client.get(reverse("cvs:builder")).status_code == 200
    resp = client.post(reverse("cvs:builder_generate"), {"mode": "general"})
    assert resp.status_code == 302 and reverse("accounts:login") in resp["Location"]


def test_generation_requires_complete_profile(client_user, user):
    give(user)
    resp = client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    assert resp.status_code == 400 and b"Complete your profile" in resp.content
    assert credits.balance(user) == 1


def test_generation_requires_a_credit_and_never_calls_ai_without_one(client_user, ready_user):
    with mock.patch("cvs.builder.run_structured") as ai:
        resp = client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    assert resp.status_code == 402 and not ai.called
    assert SESSION_KEY not in client_user.session


def test_general_cv_uses_one_credit_and_keeps_profile_facts(client_user, ready_user):
    give(ready_user, 2)
    resp = client_user.post(reverse("cvs:builder_generate"), {"mode": "general", "target_title": "AI Trainer"})
    assert resp.status_code == 302 and resp["Location"] == reverse("cvs:builder_result")
    assert credits.balance(ready_user) == 1
    cv = client_user.session[SESSION_KEY]["content"]
    assert cv["full_name"] == "Alice Example" and cv["contact"]["email"] == "alice@example.com"
    exp = cv["experience"][0]
    assert (exp["employer"], exp["title"], exp["start_date"], exp["end_date"]) == ("Example Analytics Ltd", "Data Analyst", "Jan 2020", "Present")
    page = client_user.get(reverse("cvs:builder_result"))
    assert page.status_code == 200 and b"Example Analytics Ltd" in page.content


def test_jd_cv_never_adds_skills_the_profile_lacks(client_user, ready_user):
    give(ready_user)
    client_user.post(reverse("cvs:builder_generate"), {"mode": "jd", "target_title": "AI Trainer", "job_description": JD})
    data = client_user.session[SESSION_KEY]
    text = str(data["content"]).lower()
    assert "kubernetes" not in text and "rust" not in text
    assert any("kubernetes" in m.lower() for m in data["missing"])  # reported to the user instead


def test_jd_mode_rejects_short_description_without_using_credit(client_user, ready_user):
    give(ready_user)
    resp = client_user.post(reverse("cvs:builder_generate"), {"mode": "jd", "job_description": "too short"})
    assert resp.status_code == 400 and credits.balance(ready_user) == 1


def test_ai_failure_refunds_the_credit(client_user, ready_user):
    give(ready_user)
    with mock.patch("cvs.builder.run_structured", side_effect=AIRateLimited("busy")):
        resp = client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    assert resp.status_code == 400 and credits.balance(ready_user) == 1
    assert list(CreditTransaction.objects.filter(user=ready_user).values_list("kind", flat=True).order_by("created_at")) == [
        "adjust",
        "consume",
        "refund",
    ]


def test_invented_numbers_from_the_model_are_dropped(client_user, ready_user):
    give(ready_user)
    from ai import mock as ai_mock

    real = ai_mock._tailor

    def lying(payload):
        out = real(payload)
        out["tailored"]["experience"][0]["bullets"] = ["Grew revenue by 300% for 5000 clients", "Built Python reports with pandas"]
        out["tailored"]["summary"] = "Led a team of 50 engineers. Data analyst who builds Python reports."
        return out

    with mock.patch("ai.mock._tailor", side_effect=lying):
        client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    data = client_user.session[SESSION_KEY]
    assert data["content"]["experience"][0]["bullets"] == ["Built Python reports with pandas"]
    assert "50 engineers" not in data["content"]["summary"]
    assert data["rejected"] >= 2


def test_edit_and_download_pdf_and_docx(client_user, ready_user):
    give(ready_user)
    client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    client_user.post(
        reverse("cvs:builder_result"), {"headline": "Edited headline", "summary": "Mine.", "skills": "Python, SQL", "exp_exp1": "One\nTwo"}
    )
    cv = client_user.session[SESSION_KEY]["content"]
    assert cv["headline"] == "Edited headline" and cv["experience"][0]["bullets"] == ["One", "Two"]

    pdf = client_user.get(reverse("cvs:builder_download", args=["pdf"]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf["Cache-Control"] == "private, no-store"
    docx = client_user.get(reverse("cvs:builder_download", args=["docx"]))
    import docx as python_docx

    text = "\n".join(p.text for p in python_docx.Document(io.BytesIO(docx.content)).paragraphs)
    assert "Edited headline" in text and "Example Analytics Ltd" in text
    assert client_user.get(reverse("cvs:builder_download", args=["exe"])).status_code == 404


def test_results_are_per_session_and_discardable(client, client_user, ready_user, other_user):
    give(ready_user)
    client_user.post(reverse("cvs:builder_generate"), {"mode": "general"})
    client_user.post(reverse("cvs:builder_discard"))
    assert SESSION_KEY not in client_user.session
    assert client_user.get(reverse("cvs:builder_download", args=["pdf"])).status_code == 302
    client.force_login(other_user)
    assert client.get(reverse("cvs:builder_result")).status_code == 302


def test_job_link_prefills_jd_mode(client_user, ready_user, make_job):
    job = make_job(title="LLM Evaluator")
    page = client_user.get(reverse("cvs:builder") + f"?job={job.pk}").content.decode()
    assert "LLM Evaluator" in page
    assert re.search(r'value="jd"[^>]*checked', page) and not re.search(r'value="general"[^>]*checked', page)
