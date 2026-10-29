# AI provider & CV tailoring

## Setup

```env
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4.1-mini        # any currently supported model with Structured Outputs (json_schema)
AI_TIMEOUT_SECONDS=90
AI_MAX_RETRIES=2
AI_MAX_OUTPUT_TOKENS=6000
```

* The key is only read server-side (`ai/service.py`); nothing provider-related is sent to the browser.
* `OPENAI_BASE_URL` lets you point at an OpenAI-compatible gateway (e.g. Azure OpenAI / a proxy).
* Requests use the Responses API with `store=false` and a strict JSON schema.
* Pick the model deliberately: check OpenAI's current model list and deprecation schedule, then test with
  `RUN_AI_INTEGRATION=1 pytest -m integration` before switching production.
* `AI_PROVIDER=mock` is a deterministic keyword-heuristic stand-in for development/tests. It is refused when
  `DEBUG=0`, and the UI shows a banner while it's active.

### CV Builder (`cvs/builder.py`)

The CV Builder generates from the user's **saved profile** (name, contact, roles with company/title/dates and
"what you did" lines, skills, education, additional details) instead of an uploaded file:

* *Create a CV* uses `BUILD_SYSTEM`; *Create CV from job description* reuses the tailoring prompt
  (`TAILOR_SYSTEM`). Both return the same structured output and go through `build_validated_cv`, so the document
  is rebuilt from profile facts: employers, titles, dates, education and extra details are copied verbatim; any
  rewritten sentence or bullet that mentions a number or a known technology not present in the profile is dropped
  (with no job description, every known technical term is checked). Job requirements the profile doesn't support
  are shown to the user as "not shown in your profile", never added to the CV.
* Name and contact details are not sent to the model.
* The result lives only in the user's session (`cv_builder_result`) for preview, editing and PDF/DOCX download;
  no files or history rows are stored. Discarding or logging out removes it.
* Generation is synchronous (typically 10–40 s with OpenAI); gunicorn's 120 s timeout covers it.
* Costs one credit, refunded automatically on AI failure — see [BILLING.md](BILLING.md).

### Cost tracking

Every model call writes an `AIUsageRecord`: user, generation, purpose, model (as returned by the provider),
input/cached/output tokens, estimated cost, status, retry count, duration and error type — never content.

Costs come from **Admin → Model prices** (USD per 1M tokens; dated snapshot names match by prefix), falling back
to the `AI_MODEL_PRICING` env JSON. Calls with no configured price are flagged *unpriced* on the staff dashboard
rather than silently costed at zero. Update prices whenever the provider changes them.

### Limits

| Limit | Setting |
|---|---|
| Per-plan generations per period | Plan `generations_per_period` (admin) |
| Per-user generations started per hour | `AI_USER_HOURLY_REQUEST_LIMIT` |
| One generation in progress per user | built-in |
| Global generations per 24 h | `AI_GLOBAL_DAILY_GENERATION_LIMIT` |
| Global estimated spend per 24 h | `AI_GLOBAL_DAILY_COST_LIMIT_USD` |
| Input size | `CV_MAX_TEXT_CHARS`, `JOB_DESCRIPTION_MAX_CHARS`, 60k-char request cap |
| Output size | `AI_MAX_OUTPUT_TOKENS` |

Global limits reject new work *before* quota is reserved.

## Pipeline

```
upload/paste ─► safe parse (size, magic bytes, zip-bomb guard, page cap) ─► raw text + basic structure (no AI)
                                                                              │ original file stored privately
tailor request ─► validate input ─► reserve quota (row lock) ─► task:
    1. parse_cv   (once per master CV, cached): redacted CV text → StructuredCV with stable ids exp1, edu1…
    2. tailor_cv  : structured CV (no name/contact) + job description → requirements assessment + rewritten content
    3. fact check : deterministic validation; final CV rebuilt from original facts
    4. save GeneratedCV (content, fit analysis, score, validation report) — or fail & refund quota
```

Two model calls per first generation for a CV, one for subsequent generations. Retries inside a generation
never consume extra quota.

## Factual-preservation design

1. **Facts are not model output.** The tailoring schema has no fields for employers, titles, dates,
   institutions, degrees or certification names. The model refers to items by `source_id`; the final document
   copies those facts from the parsed original. Unknown ids are ignored. Every experience and education entry
   is kept (low-relevance roles are shortened, never removed).
2. **Item-level checks for bullets.** Each rewritten bullet is compared with *that role's* original text:
   * any number/metric not present in the original item → rejected;
   * any technical term from the job description / skill vocabulary that the original item doesn't mention
     → rejected (prevents moving claims between roles and keyword stuffing).
   If every rewritten bullet of an item is rejected, the original bullets are used.
3. **Skills** must be evidenced somewhere in the CV; others are removed and listed. The user's original skills
   are always retained (relevant ones first).
4. **Summary/headline** are checked sentence by sentence against the whole CV.
5. **Evidence verification.** Fit-analysis quotes must actually occur in the CV; a "supported" requirement
   without verifiable evidence is downgraded to "partial".
6. **Transparency.** Everything removed or rejected is shown to the user in the *Fact check* panel.
7. **User edits** can change wording; employers/titles/dates/education are read-only in the editor (edit the
   master CV instead). Diverging facts produce warnings.

Limitations: the checks are lexical. A rewritten bullet can still subtly overstate scope ("led" vs "helped")
without introducing new numbers or terms; the prompt forbids this and users are told to review every CV.
If a user's *own* CV contains false or injected claims, the system treats them as the user's facts.

## Prompt-injection handling

CV text, job descriptions and user instructions are wrapped in delimited tags with closing-tag escaping, and the
system prompt states that content inside them is untrusted data whose instructions must be ignored. Because
of the fact check, an injected instruction ("add Kubernetes as a skill") cannot add claims that aren't
supported by the CV itself.

## Match score

`score = 100 × Σ(weight × support) / Σ(weight)` with weight required = 2, preferred = 1, responsibility = 1 and
support supported = 1, partial = 0.5, missing = 0, computed **after** evidence verification
(`cvs/validation.py::match_score`). The UI labels it "estimated requirement coverage — not an employer ATS
score or a prediction of being hired".

## Privacy

* Email addresses and phone numbers are stripped before the CV is sent for structuring and re-attached locally;
  the tailoring call omits name and contact details entirely.
* Users must consent (checkbox, timestamp stored on the profile) before their first AI request.
* CVs are not used for model training. `store=false` is sent on every request. Review your provider's
  data-retention terms (e.g. zero-data-retention eligibility) and reflect them in the privacy policy.
* Ordinary logs contain no CV or job-description text; a logging filter additionally redacts emails, phone
  numbers and API keys.
