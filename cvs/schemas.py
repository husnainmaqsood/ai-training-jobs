"""
Pydantic schemas for structured CVs and AI outputs.

Factual-preservation design: the tailoring model never outputs employers, job titles, dates, degrees,
institutions or certification names. It references source items by `id` and may only rewrite wording
(summary, bullets, project descriptions) and choose ordering/emphasis. The final document is rebuilt from
the original facts in `StructuredCV`, so those facts cannot be altered or invented by the model.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Contact(BaseModel):
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)


class ExperienceItem(BaseModel):
    id: str
    employer: str
    title: str
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    id: str
    institution: str
    qualification: str
    field: str = ""
    start_date: str = ""
    end_date: str = ""
    details: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    id: str
    name: str
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class CertificationItem(BaseModel):
    id: str
    name: str
    issuer: str = ""
    date: str = ""


class OtherSection(BaseModel):
    title: str
    items: list[str] = Field(default_factory=list)


class StructuredCV(BaseModel):
    full_name: str = ""
    headline: str = ""
    contact: Contact = Field(default_factory=Contact)
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    other_sections: list[OtherSection] = Field(default_factory=list)


# --- Model output: parsing -------------------------------------------------------------


class ParsedCVOutput(BaseModel):
    cv: StructuredCV
    warnings: list[str] = Field(default_factory=list)


# --- Model output: tailoring --------------------------------------------------------------

SectionName = Literal["summary", "skills", "experience", "projects", "education", "certifications", "languages", "other"]


class RequirementAssessment(BaseModel):
    requirement: str
    importance: Literal["required", "preferred", "responsibility"]
    support: Literal["supported", "partial", "missing"]
    evidence: list[str] = Field(description="Short verbatim quotes from the CV that support this; empty if missing.")
    note: str


class TailoredExperience(BaseModel):
    source_id: str
    include: bool
    bullets: list[str]


class TailoredProject(BaseModel):
    source_id: str
    include: bool
    description: str
    bullets: list[str]


class TailoredContent(BaseModel):
    headline: str
    summary: str
    skills: list[str]
    section_order: list[SectionName]
    experience: list[TailoredExperience]
    projects: list[TailoredProject]
    education_order: list[str]
    certification_order: list[str]


class TailorOutput(BaseModel):
    job_title: str
    assessments: list[RequirementAssessment]
    emphasize: list[str]
    recommendations: list[str]
    potential_inconsistencies: list[str]
    clarifying_questions: list[str]
    tailored: TailoredContent


class SectionRegenOutput(BaseModel):
    summary: str
    skills: list[str]
    bullets: list[str]
    description: str


# --- Final rendered document --------------------------------------------------------------


class RenderedCV(BaseModel):
    """What we store in GeneratedCV.content and render to DOCX/PDF. Facts come from StructuredCV."""

    full_name: str = ""
    headline: str = ""
    contact: Contact = Field(default_factory=Contact)
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)
    experience: list[ExperienceItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    other_sections: list[OtherSection] = Field(default_factory=list)


DEFAULT_SECTION_ORDER = ["summary", "skills", "experience", "projects", "education", "certifications", "languages", "other"]
