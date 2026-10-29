"""Optional live test against the configured AI provider.

Runs only when RUN_AI_INTEGRATION=1 and OPENAI_API_KEY are set. Costs a small amount of API usage.
    RUN_AI_INTEGRATION=1 OPENAI_API_KEY=sk-... OPENAI_MODEL=gpt-4.1-mini pytest -m integration
"""

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.skipif(not (os.environ.get("RUN_AI_INTEGRATION") == "1" and os.environ.get("OPENAI_API_KEY")), reason="live AI integration disabled"),
]


def test_live_parse_and_tailor(settings, user, plans, sample_cv_text):
    settings.AI_PROVIDER = "openai"
    settings.OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    settings.OPENAI_MODEL = os.environ.get("OPENAI_MODEL", settings.OPENAI_MODEL)
    from cvs.services import create_master_cv_from_text, start_generation

    cv = create_master_cv_from_text(user, "live", sample_cv_text)
    gen = start_generation(
        user=user,
        master=cv,
        target_title="Python AI Trainer",
        job_description=(
            "Evaluate and write Python code to train AI models. Requirements: Python, SQL, code review, pytest. "
            "Preferred: experience with RLHF and Kubernetes. Remote contract, worldwide."
        ),
    )
    gen.refresh_from_db()
    assert gen.status == "succeeded", gen.error_message
    gcv = gen.versions.get()
    employers = {e["employer"] for e in gcv.content["experience"]}
    assert "Example Analytics Ltd" in employers
    assert "Kubernetes" not in gcv.content["skills"]
