import json
from decimal import Decimal

import pytest
from pydantic import BaseModel

from ai import service
from ai.models import AIUsageRecord, ModelPrice

pytestmark = pytest.mark.django_db


class Out(BaseModel):
    answer: str
    items: list[str]


class Scripted:
    name = "openai"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def complete(self, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return service.ProviderResponse(text=r, input_tokens=1000, output_tokens=500, model="gpt-test-2026-01-01")


def run(provider, **kw):
    return service.run_structured(purpose="test", system="sys", user_content="hello", output_model=Out, provider=provider, **kw)


def test_success_records_usage_and_cost(settings, user):
    ModelPrice.objects.create(model="gpt-test", input_per_million=Decimal("1.00"), output_per_million=Decimal("4.00"))
    result = run(Scripted(json.dumps({"answer": "a", "items": []})), user=user)
    assert result.answer == "a"
    rec = AIUsageRecord.objects.get()
    assert (rec.status, rec.input_tokens, rec.output_tokens, rec.retry_count) == ("success", 1000, 500, 0)
    assert rec.estimated_cost_usd == Decimal("0.003000") and rec.cost_is_estimated and rec.user == user
    assert rec.model == "gpt-test-2026-01-01"


def test_transient_errors_retried_then_succeed(settings):
    settings.AI_MAX_RETRIES = 2
    p = Scripted(service.TransientProviderError("rate_limited"), service.TransientProviderError("timeout"), json.dumps({"answer": "ok", "items": []}))
    assert run(p).answer == "ok" and p.calls == 3
    assert AIUsageRecord.objects.get().retry_count == 2


def test_rate_limit_exhausted(settings):
    settings.AI_MAX_RETRIES = 1
    with pytest.raises(service.AIRateLimited):
        run(Scripted(service.TransientProviderError("rate_limited"), service.TransientProviderError("rate_limited")))
    assert AIUsageRecord.objects.get().status == "rate_limited"


def test_invalid_output_retried_then_fails(settings):
    settings.AI_MAX_RETRIES = 1
    with pytest.raises(service.AIInvalidOutput):
        run(Scripted("not json", json.dumps({"answer": 1})))
    rec = AIUsageRecord.objects.get()
    assert rec.status == "invalid_output" and rec.input_tokens == 2000  # tokens from both attempts counted


def test_input_limit():
    with pytest.raises(service.AIInputTooLarge):
        service.run_structured(purpose="t", system="s", user_content="x" * 100, output_model=Out, provider=Scripted(), max_input_chars=10)


def test_unpriced_model_flagged():
    run(Scripted(json.dumps({"answer": "a", "items": []})))
    assert AIUsageRecord.objects.get().cost_is_estimated is False


def test_env_pricing_fallback(settings):
    settings.AI_MODEL_PRICING = {"gpt-test-2026-01-01": {"input": 2, "output": 8}}
    cost, priced = service.estimate_cost("gpt-test-2026-01-01", 1_000_000, 1_000_000)
    assert priced and cost == Decimal("10.000000")


def test_strict_schema_shape():
    from cvs.schemas import TailorOutput

    schema = service.strict_json_schema(TailorOutput)

    def check(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert list(node) == ["$ref"]
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])
            assert "default" not in node
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(schema)


def test_openai_provider_requires_key(settings):
    settings.AI_PROVIDER = "openai"
    settings.OPENAI_API_KEY = ""
    assert service.ai_is_configured() is False


def test_mock_provider_disabled_in_production(settings):
    settings.DEBUG = False
    settings.TESTING = False
    settings.AI_PROVIDER = "mock"
    assert service.ai_is_configured() is False


def test_log_redaction():
    from core.logging import redact

    out = redact("user a.b@example.com called with key sk_test_abcdefghijk phone +44 20 7946 0958")
    assert "example.com" not in out and "sk_test_abc" not in out and "7946" not in out
