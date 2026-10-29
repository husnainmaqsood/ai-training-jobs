"""
Centralized AI service layer. Views and tasks call `run_structured()`; nothing else talks to a model.

* Provider and model come from settings (env vars). API keys never leave the server.
* Outputs are requested as strict JSON schema and validated again with Pydantic.
* Controlled retries for rate limits, timeouts, transient provider errors and invalid output.
* Every call writes an AIUsageRecord (tokens, estimated cost, duration, status, retries) — never content.
"""

import copy
import json
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from pydantic import BaseModel, ValidationError

from .models import AIUsageRecord, ModelPrice

logger = logging.getLogger(__name__)


class AIError(Exception):
    """Base error. `user_message` is safe to show."""

    user_message = "The AI service is temporarily unavailable. Your quota has not been used. Please try again."


class AINotConfigured(AIError):
    user_message = "AI features are not configured on this server yet."


class AIRateLimited(AIError):
    user_message = "The AI provider is busy right now. Please try again in a few minutes."


class AIInvalidOutput(AIError):
    user_message = "The AI returned an unusable result. Your quota has not been used. Please try again."


class AIGlobalLimit(AIError):
    user_message = "AI generation is temporarily paused due to high demand. Please try again later."


class AIInputTooLarge(AIError):
    user_message = "The CV or job description is too long. Please shorten it and try again."


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    model: str = ""


class TransientProviderError(Exception):
    def __init__(self, kind, original=None):
        super().__init__(kind)
        self.kind = kind  # "rate_limited" | "timeout" | "error"
        self.original = original


# --- Schema helpers ----------------------------------------------------------------


def strict_json_schema(model_cls: type[BaseModel]) -> dict:
    """Convert a Pydantic schema to the strict subset accepted by structured-output APIs."""
    schema = copy.deepcopy(model_cls.model_json_schema())

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:  # strict mode does not allow sibling keywords next to $ref
                for key in [k for k in node if k != "$ref"]:
                    node.pop(key)
                return
            node.pop("title", None)
            node.pop("default", None)
            node.pop("examples", None)
            if node.get("type") == "object" or "properties" in node:
                props = node.get("properties", {})
                node["required"] = list(props.keys())
                node["additionalProperties"] = False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return schema


# --- Providers ------------------------------------------------------------------------


class OpenAIProvider:
    name = "openai"

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise AINotConfigured("OPENAI_API_KEY is not set")
        import openai

        self._openai = openai
        self.client = openai.OpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            timeout=settings.AI_TIMEOUT_SECONDS,
            max_retries=0,  # retries are controlled (and counted) by run_structured
        )

    def complete(self, *, model, system, user, schema_name, schema, max_output_tokens) -> ProviderResponse:
        o = self._openai
        try:
            resp = self.client.responses.create(
                model=model,
                instructions=system,
                input=[{"role": "user", "content": user}],
                text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
                max_output_tokens=max_output_tokens,
                store=False,
            )
        except o.RateLimitError as exc:
            raise TransientProviderError("rate_limited", exc) from exc
        except o.APITimeoutError as exc:
            raise TransientProviderError("timeout", exc) from exc
        except (o.APIConnectionError, o.InternalServerError) as exc:
            raise TransientProviderError("error", exc) from exc
        except o.AuthenticationError as exc:
            raise AINotConfigured("OpenAI authentication failed") from exc
        usage = getattr(resp, "usage", None)
        cached = 0
        if usage is not None and getattr(usage, "input_tokens_details", None) is not None:
            cached = getattr(usage.input_tokens_details, "cached_tokens", 0) or 0
        return ProviderResponse(
            text=resp.output_text or "",
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_input_tokens=cached,
            model=getattr(resp, "model", model) or model,
        )


class MockProvider:
    """Deterministic, non-AI provider for local development and tests. Clearly labelled in the UI.

    It delegates to `ai.mock.respond(schema_name, user_payload)` which uses keyword heuristics.
    """

    name = "mock"

    def __init__(self):
        if not (settings.DEBUG or settings.TESTING):
            raise AINotConfigured("The mock AI provider is disabled outside development.")

    def complete(self, *, model, system, user, schema_name, schema, max_output_tokens) -> ProviderResponse:
        from . import mock

        text = json.dumps(mock.respond(schema_name, user))
        return ProviderResponse(text=text, input_tokens=len(user) // 4, output_tokens=len(text) // 4, model="mock")


def get_provider():
    if settings.AI_PROVIDER == "mock":
        return MockProvider()
    if settings.AI_PROVIDER == "openai":
        return OpenAIProvider()
    raise AINotConfigured(f"Unknown AI_PROVIDER '{settings.AI_PROVIDER}'")


def ai_is_configured():
    try:
        get_provider()
        return True
    except AINotConfigured:
        return False


# --- Cost ---------------------------------------------------------------------------------


def estimate_cost(model, input_tokens, output_tokens, cached_input_tokens=0):
    """Returns (cost_usd: Decimal, priced: bool). Pricing comes from ModelPrice rows, then AI_MODEL_PRICING."""
    price = ModelPrice.objects.filter(model=model).first()
    if price is None:
        # allow dated model snapshots such as "gpt-4.1-mini-2025-04-14" to match "gpt-4.1-mini"
        for candidate in ModelPrice.objects.all():
            if model.startswith(candidate.model):
                price = candidate
                break
    if price is not None:
        inp, out = price.input_per_million, price.output_per_million
        cached = price.cached_input_per_million if price.cached_input_per_million is not None else inp
    else:
        cfg = settings.AI_MODEL_PRICING.get(model)
        if not cfg:
            return Decimal("0"), False
        inp, out = Decimal(str(cfg["input"])), Decimal(str(cfg["output"]))
        cached = Decimal(str(cfg.get("cached_input", cfg["input"])))
    uncached = max(input_tokens - cached_input_tokens, 0)
    cost = (Decimal(uncached) * inp + Decimal(cached_input_tokens) * cached + Decimal(output_tokens) * out) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001")), True


def check_global_limits():
    since = timezone.now() - timedelta(days=1)
    recent = AIUsageRecord.objects.filter(created_at__gte=since)
    if recent.filter(purpose__in=["tailor_cv", "build_cv"]).count() >= settings.AI_GLOBAL_DAILY_GENERATION_LIMIT:
        raise AIGlobalLimit("global daily generation limit")
    spent = recent.aggregate(total=Sum("estimated_cost_usd"))["total"] or Decimal("0")
    if spent >= Decimal(str(settings.AI_GLOBAL_DAILY_COST_LIMIT_USD)):
        raise AIGlobalLimit("global daily cost limit")


# --- Main entry point ---------------------------------------------------------------------


def run_structured(
    *,
    purpose: str,
    system: str,
    user_content: str,
    output_model: type[BaseModel],
    user=None,
    generation=None,
    max_output_tokens=None,
    max_input_chars=60000,
    provider=None,
):
    if len(user_content) > max_input_chars:
        raise AIInputTooLarge(f"input {len(user_content)} chars > {max_input_chars}")
    provider = provider or get_provider()
    model = settings.OPENAI_MODEL if provider.name == "openai" else provider.name
    schema = strict_json_schema(output_model)
    max_tokens = max_output_tokens or settings.AI_MAX_OUTPUT_TOKENS
    max_retries = settings.AI_MAX_RETRIES

    totals = {"in": 0, "out": 0, "cached": 0}
    attempt = 0
    started = time.monotonic()
    last_status, last_error = AIUsageRecord.Status.ERROR, ""
    resolved_model = model
    try:
        while True:
            try:
                resp = provider.complete(
                    model=model,
                    system=system,
                    user=user_content,
                    schema_name=output_model.__name__,
                    schema=schema,
                    max_output_tokens=max_tokens,
                )
                resolved_model = resp.model or model
                totals["in"] += resp.input_tokens
                totals["out"] += resp.output_tokens
                totals["cached"] += resp.cached_input_tokens
                try:
                    result = output_model.model_validate_json(resp.text)
                except (ValidationError, ValueError) as exc:
                    last_status, last_error = AIUsageRecord.Status.INVALID_OUTPUT, type(exc).__name__
                    if attempt >= max_retries:
                        raise AIInvalidOutput("output failed schema validation") from exc
                    attempt += 1
                    continue
                last_status, last_error = AIUsageRecord.Status.SUCCESS, ""
                return result
            except TransientProviderError as exc:
                last_status = {
                    "rate_limited": AIUsageRecord.Status.RATE_LIMITED,
                    "timeout": AIUsageRecord.Status.TIMEOUT,
                }.get(exc.kind, AIUsageRecord.Status.ERROR)
                last_error = type(exc.original).__name__ if exc.original else exc.kind
                if attempt >= max_retries:
                    if exc.kind == "rate_limited":
                        raise AIRateLimited(exc.kind) from exc
                    raise AIError(exc.kind) from exc
                attempt += 1
                time.sleep(min(2**attempt, 20) if not settings.TESTING else 0)
    except AINotConfigured:
        last_status, last_error = AIUsageRecord.Status.ERROR, "not_configured"
        raise
    finally:
        cost, priced = estimate_cost(resolved_model, totals["in"], totals["out"], totals["cached"])
        AIUsageRecord.objects.create(
            user=user if (user is not None and user.is_authenticated) else None,
            generation=generation,
            purpose=purpose,
            provider=provider.name,
            model=resolved_model,
            input_tokens=totals["in"],
            output_tokens=totals["out"],
            cached_input_tokens=totals["cached"],
            estimated_cost_usd=cost,
            cost_is_estimated=priced,
            status=last_status,
            retry_count=attempt,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_type=last_error[:100],
        )
        logger.info(
            "ai_call purpose=%s model=%s status=%s in=%s out=%s retries=%s",
            purpose,
            resolved_model,
            last_status,
            totals["in"],
            totals["out"],
            attempt,
        )
