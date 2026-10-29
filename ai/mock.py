"""
Deterministic development stand-in for the AI provider (AI_PROVIDER=mock, DEBUG/TESTING only).

It uses keyword heuristics, not a language model, so developers can exercise the full pipeline without an
API key. The UI shows a banner whenever this provider is active.
"""

import json
import re


def _between(text, tag):
    m = re.search(rf"<{tag}>\n(.*)\n</{tag}>", text, re.S)
    return m.group(1) if m else ""


def respond(schema_name, user_payload):
    if schema_name == "ParsedCVOutput":
        from cvs.parsing import heuristic_structure

        cv = heuristic_structure(_between(user_payload, "cv_document"))
        cv.contact.email = ""
        cv.contact.phone = ""
        return {"cv": cv.model_dump(), "warnings": ["Parsed by the development mock (heuristics, not AI)."]}
    if schema_name == "TailorOutput":
        return _tailor(user_payload)
    if schema_name == "SectionRegenOutput":
        data = json.loads(_between(user_payload, "structured_cv") or "{}")
        return {
            "summary": data.get("summary", ""),
            "skills": data.get("skills", []),
            "bullets": list(reversed(data.get("bullets", []))),
            "description": data.get("description", ""),
        }
    raise ValueError(f"mock has no response for {schema_name}")


def _tailor(user_payload):
    from cvs.validation import contains_term, job_terms, normalize

    jd = _between(user_payload, "job_description")
    cv = json.loads(_between(user_payload, "structured_cv") or "{}")
    title_match = re.search(r"Target job title: (.*)", user_payload)
    job_title = title_match.group(1).strip() if title_match else ""
    terms = sorted(job_terms(jd))[:12]

    snippets = []
    for e in cv.get("experience", []):
        snippets += e.get("bullets", [])
    for p in cv.get("projects", []):
        snippets += p.get("bullets", []) + [p.get("description", "")]
    snippets += cv.get("skills", [])
    corpus = normalize(" ".join(snippets + [cv.get("summary", "")]))

    assessments = []
    for t in terms:
        evidence = [s for s in snippets if s and contains_term(t, normalize(s))][:2]
        support = "supported" if evidence else ("partial" if contains_term(t, corpus) else "missing")
        assessments.append(
            {
                "requirement": f"Experience with {t}",
                "importance": "required",
                "support": support,
                "evidence": evidence,
                "note": "Keyword match (development mock).",
            }
        )

    def relevance(text):
        n = normalize(text)
        return -sum(1 for t in terms if contains_term(t, n))

    experience = [{"source_id": e["id"], "include": True, "bullets": sorted(e.get("bullets", []), key=relevance)} for e in cv.get("experience", [])]
    projects = [
        {"source_id": p["id"], "include": True, "description": p.get("description", ""), "bullets": p.get("bullets", [])}
        for p in cv.get("projects", [])
    ]
    return {
        "job_title": job_title,
        "assessments": assessments,
        "emphasize": [a["requirement"] for a in assessments if a["support"] == "supported"][:5],
        "recommendations": [
            f"If you have genuine experience with {a['requirement'].removeprefix('Experience with ')}, add a specific bullet describing it."
            for a in assessments
            if a["support"] == "missing"
        ][:5],
        "potential_inconsistencies": [],
        "clarifying_questions": [],
        "tailored": {
            "headline": cv.get("headline", ""),
            "summary": cv.get("summary", ""),
            "skills": sorted(cv.get("skills", []), key=relevance),
            "section_order": ["summary", "skills", "experience", "projects", "education", "certifications", "languages", "other"],
            "experience": experience,
            "projects": projects,
            "education_order": [e["id"] for e in cv.get("education", [])],
            "certification_order": [c["id"] for c in cv.get("certifications", [])],
        },
    }
