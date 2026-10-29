"""
Deterministic factual-consistency validation of AI tailoring output.

The model's output is never trusted directly. This module rebuilds the final CV from the original facts and
only accepts rewritten text that passes these checks:

1. Unknown source ids are ignored; every original experience and education entry is retained.
2. Numbers: every number/metric in a rewritten bullet must already appear in that same source item (or, for
   the summary/headline, anywhere in the CV). Otherwise the bullet is rejected.
3. Unsupported job terms: technical terms that occur in the job description (and the skill vocabulary) but
   NOT in the relevant source text are treated as fabricated claims. A bullet/sentence containing one is
   rejected; a skill that is not evidenced anywhere in the CV is removed.
4. If all rewritten bullets of an item are rejected, the original bullets are used.
5. Fit-analysis evidence quotes must be found in the CV; "supported" without verifiable evidence is
   downgraded to "partial".

The report of everything that was changed is stored with the generated CV and shown to the user.
"""

import re
import unicodedata

from .schemas import (
    DEFAULT_SECTION_ORDER,
    RenderedCV,
    StructuredCV,
    TailorOutput,
)

# Common technical / AI-training vocabulary used to spot unsupported claims (in addition to DB skills).
TECH_VOCAB = {
    "python",
    "java",
    "javascript",
    "typescript",
    "c++",
    "c#",
    "golang",
    "rust",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "matlab",
    "sql",
    "nosql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "django",
    "flask",
    "fastapi",
    "react",
    "vue",
    "angular",
    "node.js",
    "nodejs",
    "next.js",
    "aws",
    "gcp",
    "azure",
    "docker",
    "kubernetes",
    "terraform",
    "linux",
    "git",
    "pytorch",
    "tensorflow",
    "keras",
    "jax",
    "pandas",
    "numpy",
    "scikit-learn",
    "spark",
    "hadoop",
    "airflow",
    "tableau",
    "power bi",
    "excel",
    "llm",
    "llms",
    "rlhf",
    "nlp",
    "computer vision",
    "machine learning",
    "deep learning",
    "data annotation",
    "data labeling",
    "prompt engineering",
    "red teaming",
    "fine-tuning",
    "transformers",
    "hugging face",
    "langchain",
    "openai",
    "statistics",
    "phd",
    "mba",
    "cpa",
    "cfa",
    "graphql",
    "html",
    "css",
    "tailwind",
    "figma",
    "jira",
    "agile",
    "scrum",
    "ci/cd",
    "bash",
    "selenium",
    "pytest",
}

ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "postgres": "postgresql",
    "nodejs": "node.js",
    "ml": "machine learning",
    "dl": "deep learning",
    "k8s": "kubernetes",
    "sklearn": "scikit-learn",
    "reinforcement learning from human feedback": "rlhf",
    "large language models": "llm",
    "llms": "llm",
    "large language model": "llm",
    "natural language processing": "nlp",
    "labeling": "labelling",
}

ACRONYM_STOPWORDS = {
    "and",
    "or",
    "the",
    "we",
    "you",
    "us",
    "uk",
    "usa",
    "eu",
    "faq",
    "ok",
    "i",
    "a",
    "demo",
    "note",
    "only",
    "new",
    "must",
    "all",
    "any",
    "who",
    "why",
    "how",
    "what",
    "our",
    "your",
    "job",
    "role",
    "work",
    "apply",
    "pay",
    "full",
    "part",
    "time",
    "and/or",
    "us/uk",
    "tbd",
    "asap",
    "etc",
    "eg",
    "ie",
    "mon",
    "fri",
    "est",
    "pst",
    "gmt",
    "utc",
    "usd",
    "gbp",
    "eur",
}

NUMBER_RE = re.compile(r"(?<![\w.])[$€£]?\d[\d,]*(?:\.\d+)?\s*(?:%|k\b|m\b|x\b|\+)?", re.I)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("–", "-").replace("—", "-").replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return f" {text} "


def _canon(term: str) -> str:
    term = term.strip().lower()
    return ALIASES.get(term, term)


def contains_term(term: str, corpus_norm: str) -> bool:
    term = term.strip().lower()
    if not term:
        return True
    variants = {term, _canon(term)} | {k for k, v in ALIASES.items() if v == _canon(term)}
    for v in variants:
        pattern = r"(?<![a-z0-9+#])" + re.escape(v) + r"(?![a-z0-9+#])"
        if re.search(pattern, corpus_norm):
            return True
    return False


def numbers_in(text: str) -> set[str]:
    out = set()
    for m in NUMBER_RE.finditer(text or ""):
        digits = re.sub(r"[^\d.]", "", m.group()).strip(".")
        if digits:
            out.add(digits.lstrip("0") or "0")
    return out


def job_terms(job_description: str, skill_vocab=()) -> set[str]:
    """Technical terms the job asks for (used to detect keyword-stuffed or invented claims)."""
    jd = normalize(job_description)
    vocab = {v.lower() for v in skill_vocab if v and len(v) <= 40} | TECH_VOCAB
    found = {t for t in vocab if contains_term(t, jd)}
    # ALLCAPS acronyms and tokens with tech punctuation (C++, Node.js, CI/CD) found in the JD itself
    for tok in re.findall(r"\b[A-Z][A-Z0-9]{1,4}\b|\b\w+(?:\+\+|#|\.js|/\w+)", job_description or ""):
        if tok.lower() not in ACRONYM_STOPWORDS and "/" not in tok[:1]:
            found.add(tok.lower())
    return found


def all_known_terms(skill_vocab=()) -> set[str]:
    """Every technical term we know of — used when there is no job description to take terms from."""
    return {v.lower() for v in skill_vocab if v and len(v) <= 40} | TECH_VOCAB


def _unsupported_terms(text, source_norm, terms):
    text_norm = normalize(text)
    return sorted(t for t in terms if contains_term(t, text_norm) and not contains_term(t, source_norm))


def _unsupported_numbers(text, allowed_numbers):
    return sorted(n for n in numbers_in(text) if n not in allowed_numbers)


def full_corpus(cv: StructuredCV, raw_text: str = "") -> str:
    parts = [raw_text, cv.headline, cv.summary, " ".join(cv.skills), " ".join(cv.languages)]
    for e in cv.experience:
        parts += [e.employer, e.title, e.location, e.start_date, e.end_date, *e.bullets]
    for e in cv.education:
        parts += [e.institution, e.qualification, e.field, e.start_date, e.end_date, *e.details]
    for p in cv.projects:
        parts += [p.name, p.description, *p.technologies, *p.bullets]
    for c in cv.certifications:
        parts += [c.name, c.issuer, c.date]
    for s in cv.other_sections:
        parts += [s.title, *s.items]
    return normalize("\n".join(p for p in parts if p))


def check_text(text, source_text, terms, allowed_numbers):
    """Returns a list of human-readable problems ([] if OK)."""
    problems = []
    src = normalize(source_text)
    bad_terms = _unsupported_terms(text, src, terms)
    if bad_terms:
        problems.append("mentions " + ", ".join(bad_terms) + " which the original does not")
    bad_nums = _unsupported_numbers(text, allowed_numbers)
    if bad_nums:
        problems.append("uses numbers not in the original (" + ", ".join(bad_nums) + ")")
    return problems


def _split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def _check_paragraph(text, corpus_raw, terms, allowed, label, report):
    """Keep only sentences that pass; returns cleaned paragraph (may be empty)."""
    kept = []
    for sentence in _split_sentences(text):
        problems = check_text(sentence, corpus_raw, terms, allowed)
        if problems:
            report["rejected_text"].append({"section": label, "text": sentence, "reasons": problems})
        else:
            kept.append(sentence)
    return " ".join(kept)


MAX_BULLETS = 8


def verify_evidence(assessments, corpus_norm):
    verified = []
    for a in assessments:
        good = [q for q in a.get("evidence", []) if q and _quote_in(q, corpus_norm)]
        dropped = len(a.get("evidence", [])) - len(good)
        item = dict(a, evidence=good)
        if dropped:
            item["evidence_unverified"] = dropped
        if item["support"] == "supported" and not good:
            item["support"] = "partial"
            item["note"] = (item.get("note", "") + " (Evidence could not be verified in your CV; downgraded.)").strip()
        verified.append(item)
    return verified


def _quote_in(quote, corpus_norm):
    q = normalize(quote).strip()
    if len(q) < 3:
        return False
    if q in corpus_norm:
        return True
    # tolerate minor punctuation differences
    strip = lambda s: re.sub(r"[^a-z0-9 ]", "", s)  # noqa: E731
    return strip(q).strip() in strip(corpus_norm)


SCORE_WEIGHTS = {"required": 2.0, "preferred": 1.0, "responsibility": 1.0}
SUPPORT_VALUES = {"supported": 1.0, "partial": 0.5, "missing": 0.0}


def match_score(assessments):
    """Estimated keyword/evidence coverage, 0–100. NOT an employer ATS score or a hiring probability.

    score = 100 × Σ(weight × support) / Σ(weight), with weight required=2, preferred=1, responsibility=1 and
    support supported=1, partial=0.5, missing=0 — computed after evidence verification.
    """
    total = sum(SCORE_WEIGHTS.get(a["importance"], 1.0) for a in assessments)
    if not total:
        return None
    got = sum(SCORE_WEIGHTS.get(a["importance"], 1.0) * SUPPORT_VALUES.get(a["support"], 0) for a in assessments)
    return round(100 * got / total)


def build_validated_cv(source: StructuredCV, raw_text: str, out: TailorOutput, job_description: str, skill_vocab=(), terms=None):
    """`terms`: technical terms a claim may not mention unless the source does. Defaults to the job's terms."""
    if terms is None:
        terms = job_terms(job_description, skill_vocab)
    corpus = full_corpus(source, raw_text)
    all_numbers = numbers_in(corpus)
    report = {"rejected_text": [], "removed_skills": [], "ignored_ids": [], "reverted_items": [], "notes": []}
    t = out.tailored

    # --- headline & summary ---------------------------------------------------------------
    headline = _check_paragraph(t.headline, corpus, terms, all_numbers, "headline", report) or source.headline
    summary = _check_paragraph(t.summary, corpus, terms, all_numbers, "summary", report)
    if not summary:
        summary = source.summary
        if t.summary:
            report["notes"].append("The rewritten summary contained unsupported claims; your original summary was kept.")

    # --- skills ------------------------------------------------------------------------------
    skills = []
    for s in t.skills:
        s_clean = s.strip()
        if not s_clean or len(s_clean) > 60:
            continue
        if contains_term(s_clean, corpus):
            if s_clean.lower() not in {x.lower() for x in skills}:
                skills.append(s_clean)
        else:
            report["removed_skills"].append(s_clean)
    for s in source.skills:  # never silently drop the candidate's own skills — relevant ones are first
        if s.lower() not in {x.lower() for x in skills}:
            skills.append(s)

    # --- experience --------------------------------------------------------------------------
    tailored_exp = {}
    known_exp = {e.id for e in source.experience}
    for te in t.experience:
        if te.source_id in known_exp:
            tailored_exp[te.source_id] = te
        else:
            report["ignored_ids"].append(te.source_id)
    experience = []
    for item in source.experience:
        te = tailored_exp.get(item.id)
        item_source = "\n".join([item.title, item.employer, *item.bullets])
        item_numbers = numbers_in(item_source)
        bullets = []
        if te:
            for b in te.bullets[:MAX_BULLETS]:
                b = b.strip()
                if not b:
                    continue
                problems = check_text(b, item_source, terms, item_numbers)
                if problems:
                    report["rejected_text"].append({"section": f"experience: {item.title} @ {item.employer}", "text": b, "reasons": problems})
                else:
                    bullets.append(b)
        if not bullets:
            bullets = list(item.bullets)
            if te and te.bullets:
                report["reverted_items"].append(f"{item.title} @ {item.employer}")
        if te and not te.include:
            bullets = bullets[:2]
        experience.append(item.model_copy(update={"bullets": bullets}))

    # --- projects ----------------------------------------------------------------------------
    known_proj = {p.id: p for p in source.projects}
    projects, seen = [], set()
    for tp in t.projects:
        p = known_proj.get(tp.source_id)
        if p is None:
            report["ignored_ids"].append(tp.source_id)
            continue
        if tp.source_id in seen or not tp.include:
            continue
        seen.add(tp.source_id)
        p_source = "\n".join([p.name, p.description, *p.technologies, *p.bullets])
        p_nums = numbers_in(p_source)
        desc = tp.description.strip()
        if desc and check_text(desc, p_source, terms, p_nums):
            report["rejected_text"].append({"section": f"project: {p.name}", "text": desc, "reasons": check_text(desc, p_source, terms, p_nums)})
            desc = p.description
        bullets = []
        for b in tp.bullets[:MAX_BULLETS]:
            problems = check_text(b, p_source, terms, p_nums)
            if problems:
                report["rejected_text"].append({"section": f"project: {p.name}", "text": b, "reasons": problems})
            elif b.strip():
                bullets.append(b.strip())
        projects.append(p.model_copy(update={"description": desc or p.description, "bullets": bullets or list(p.bullets)}))
    for p in source.projects:  # projects the model did not mention are kept unchanged
        if p.id not in seen and p.id not in {tp.source_id for tp in t.projects if not tp.include}:
            projects.append(p)

    # --- education & certifications: always all of them, optionally reordered -----------------
    def ordered(items, order):
        by_id = {i.id: i for i in items}
        result = [by_id[i] for i in dict.fromkeys(order) if i in by_id]
        return result + [i for i in items if i not in result]

    education = ordered(source.education, t.education_order)
    certifications = ordered(source.certifications, t.certification_order)

    section_order = [s for s in dict.fromkeys(t.section_order) if s in DEFAULT_SECTION_ORDER]
    section_order += [s for s in DEFAULT_SECTION_ORDER if s not in section_order]

    rendered = RenderedCV(
        full_name=source.full_name,
        headline=headline,
        contact=source.contact,
        summary=summary,
        skills=skills,
        section_order=section_order,
        experience=experience,
        projects=projects,
        education=education,
        certifications=certifications,
        languages=source.languages,
        other_sections=source.other_sections,
    )

    assessments = verify_evidence([a.model_dump() for a in out.assessments], corpus)
    fit = {
        "assessments": assessments,
        "emphasize": out.emphasize[:10],
        "recommendations": out.recommendations[:10],
        "potential_inconsistencies": out.potential_inconsistencies[:10],
        "clarifying_questions": out.clarifying_questions[:5],
        "counts": {k: sum(1 for a in assessments if a["support"] == k) for k in ("supported", "partial", "missing")},
    }
    report["passed_without_changes"] = not (report["rejected_text"] or report["removed_skills"] or report["ignored_ids"])
    return rendered, fit, match_score(assessments), report


def validate_rendered_edit(source: StructuredCV, raw_text: str, rendered: RenderedCV):
    """Check user edits: facts that must not change (employers/titles/dates/degrees) are compared to source.

    Users may edit wording freely — it's their CV — but we warn if core facts differ from the original.
    """
    warnings = []
    src_exp = {e.id: e for e in source.experience}
    for e in rendered.experience:
        o = src_exp.get(e.id)
        if o is None:
            warnings.append(f"Experience '{e.title} @ {e.employer}' is not in your original CV.")
            continue
        for field in ("employer", "title", "start_date", "end_date"):
            if getattr(o, field).strip() != getattr(e, field).strip():
                warnings.append(f"{field.replace('_', ' ').title()} for '{o.title} @ {o.employer}' differs from your original CV.")
    src_edu = {e.id: e for e in source.education}
    for e in rendered.education:
        o = src_edu.get(e.id)
        if o and (o.institution.strip() != e.institution.strip() or o.qualification.strip() != e.qualification.strip()):
            warnings.append(f"Education entry '{o.qualification or o.institution}' differs from your original CV.")
    return warnings
