"""
Safe CV document parsing.

Uploaded files are untrusted. We check size, magic bytes and container structure before parsing, cap the
number of PDF pages and the extracted text length, reject encrypted PDFs, and guard against zip bombs in DOCX.
"""

import hashlib
import io
import re
import zipfile

from django.conf import settings

from .schemas import (
    CertificationItem,
    Contact,
    EducationItem,
    ExperienceItem,
    OtherSection,
    ProjectItem,
    StructuredCV,
)

MAX_PDF_PAGES = 15
MAX_DOCX_UNCOMPRESSED = 40 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000


class CVParseError(Exception):
    """Message is safe to show to the user."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_type(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if data[:5] == b"%PDF-":
        if not name.endswith(".pdf"):
            raise CVParseError("The file extension does not match its contents.")
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        if not name.endswith(".docx"):
            raise CVParseError("Only .docx Word documents are supported (not .doc or other zip files).")
        return "docx"
    raise CVParseError("Unsupported file. Please upload a PDF or DOCX file.")


def read_upload(uploaded_file):
    if uploaded_file.size > settings.CV_MAX_UPLOAD_BYTES:
        raise CVParseError(f"File is too large (max {settings.CV_MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    data = uploaded_file.read(settings.CV_MAX_UPLOAD_BYTES + 1)
    if len(data) > settings.CV_MAX_UPLOAD_BYTES:
        raise CVParseError("File is too large.")
    if not data:
        raise CVParseError("The file is empty.")
    return detect_type(uploaded_file.name, data), data


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise CVParseError("Password-protected PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise CVParseError(f"PDF has too many pages (max {MAX_PDF_PAGES}).")
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > settings.CV_MAX_TEXT_CHARS * 2:
                break
        return "\n".join(parts)
    except CVParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise CVParseError("We couldn't read this PDF. Try exporting it again or upload a DOCX.") from exc


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_DOCX_ENTRIES or sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED:
                raise CVParseError("This DOCX file is too large to process.")
            if "word/document.xml" not in zf.namelist():
                raise CVParseError("This is not a valid Word document.")
    except zipfile.BadZipFile as exc:
        raise CVParseError("This is not a valid Word document.") from exc

    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of errors on malformed input
        raise CVParseError("We couldn't read this Word document.") from exc
    lines = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if "list" in style and not text.startswith(("•", "-", "*")):
            text = "• " + text
        lines.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(dict.fromkeys(cells)))
    return "\n".join(lines)


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[: settings.CV_MAX_TEXT_CHARS]


def extract_text(file_type: str, data: bytes) -> str:
    text = _extract_pdf(data) if file_type == "pdf" else _extract_docx(data)
    text = clean_text(text)
    if len(text) < 80:
        raise CVParseError(
            "We could not find enough text in this file. Scanned/image-only CVs are not supported — "
            "please upload a text-based PDF/DOCX or paste your CV."
        )
    return text


# --- Contact detection & redaction ------------------------------------------------------

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s|,;]+|(?:linkedin\.com|github\.com)/[^\s|,;]+", re.I)


def extract_contact(text: str) -> Contact:
    email = EMAIL_RE.search(text)
    phone = None
    for m in PHONE_RE.finditer(text[:1500]):
        digits = re.sub(r"\D", "", m.group())
        if 8 <= len(digits) <= 15 and not re.fullmatch(r"(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}", m.group().strip()):
            phone = m
            break
    links = list(dict.fromkeys(u.rstrip(".)") for u in URL_RE.findall(text[:3000])))[:5]
    return Contact(email=email.group() if email else "", phone=phone.group().strip() if phone else "", links=links)


def redact_for_ai(text: str, contact: Contact) -> str:
    """Remove direct identifiers before text is sent to the AI provider; they are re-attached locally."""
    if contact.email:
        text = text.replace(contact.email, "[EMAIL]")
    if contact.phone:
        text = text.replace(contact.phone, "[PHONE]")
    text = EMAIL_RE.sub("[EMAIL]", text)
    return text


# --- Heuristic structuring (offline fallback; no AI) ---------------------------------------

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "about", "about me", "objective", "career objective"],
    "experience": ["experience", "work experience", "professional experience", "employment", "employment history", "work history", "career history"],
    "education": ["education", "academic background", "qualifications", "education and training"],
    "skills": ["skills", "technical skills", "core skills", "key skills", "competencies", "core competencies", "tools", "technologies"],
    "projects": ["projects", "selected projects", "personal projects", "key projects"],
    "certifications": ["certifications", "certificates", "licenses", "licenses and certifications", "courses"],
    "languages": ["languages"],
}
BULLET_RE = re.compile(r"^\s*(?:[•●▪◦\-*–·]|\d+[.)])\s+")
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_TOKEN = rf"(?:{MONTH}\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
DATE_RANGE_RE = re.compile(rf"({DATE_TOKEN})\s*(?:-|–|—|to)\s*({DATE_TOKEN}|present|current|now)", re.I)


def _heading_key(line: str):
    norm = re.sub(r"[^a-z ]", "", line.lower()).strip()
    if not norm or len(line) > 40:
        return None
    for key, aliases in SECTION_ALIASES.items():
        if norm in aliases:
            return key
    return None


def _split_sections(text):
    sections = {"_header": []}
    current = "_header"
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key = _heading_key(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _split_title_employer(line):
    for sep in [" at ", " — ", " – ", " - ", " | ", ", "]:
        if sep in line:
            a, b = line.split(sep, 1)
            return a.strip(), b.strip()
    return line.strip(), ""


def _parse_entries(lines):
    """Group lines into entries: a non-bullet line starts an entry; bullets attach to the current entry."""
    entries = []
    current = None
    for line in lines:
        if BULLET_RE.match(line):
            if current is None:
                current = {"head": [], "bullets": []}
                entries.append(current)
            current["bullets"].append(BULLET_RE.sub("", line).strip())
        else:
            if current is None or current["bullets"]:
                current = {"head": [], "bullets": []}
                entries.append(current)
            current["head"].append(line)
    return entries


def heuristic_structure(text: str) -> StructuredCV:
    contact = extract_contact(text)
    sections = _split_sections(text)
    header = [ln for ln in sections.get("_header", []) if not EMAIL_RE.search(ln) and not URL_RE.search(ln)]
    full_name = header[0] if header and len(header[0]) < 60 else ""
    headline = header[1] if len(header) > 1 and len(header[1]) < 120 else ""

    experience = []
    for i, entry in enumerate(_parse_entries(sections.get("experience", []))):
        head = " ".join(entry["head"])
        start = end = ""
        m = DATE_RANGE_RE.search(head)
        if m:
            start, end = m.group(1), m.group(2)
            head = (head[: m.start()] + head[m.end() :]).strip(" ,|()-–")
        title, employer = _split_title_employer(head)
        bullets = entry["bullets"] or entry["head"][1:]
        experience.append(ExperienceItem(id=f"exp{i + 1}", employer=employer, title=title, start_date=start, end_date=end, bullets=bullets))

    education = []
    for i, entry in enumerate(_parse_entries(sections.get("education", []))):
        head = " ".join(entry["head"])
        start = end = ""
        m = DATE_RANGE_RE.search(head)
        if m:
            start, end = m.group(1), m.group(2)
            head = (head[: m.start()] + head[m.end() :]).strip(" ,|()-–")
        qual, inst = _split_title_employer(head)
        education.append(
            EducationItem(
                id=f"edu{i + 1}",
                institution=inst or qual,
                qualification=qual if inst else "",
                start_date=start,
                end_date=end,
                details=entry["bullets"],
            )
        )

    projects = []
    for i, entry in enumerate(_parse_entries(sections.get("projects", []))):
        name = entry["head"][0] if entry["head"] else f"Project {i + 1}"
        desc = " ".join(entry["head"][1:])
        projects.append(ProjectItem(id=f"proj{i + 1}", name=name, description=desc, bullets=entry["bullets"]))

    certifications = [
        CertificationItem(id=f"cert{i + 1}", name=BULLET_RE.sub("", ln).strip()) for i, ln in enumerate(sections.get("certifications", []))
    ]

    skills = []
    for ln in sections.get("skills", []):
        ln = BULLET_RE.sub("", ln)
        if ":" in ln:
            ln = ln.split(":", 1)[1]
        skills.extend(s.strip() for s in re.split(r"[,;|•]", ln) if 1 < len(s.strip()) < 50)
    languages = []
    for ln in sections.get("languages", []):
        languages.extend(s.strip() for s in re.split(r"[,;|•]", BULLET_RE.sub("", ln)) if s.strip())

    known = set(SECTION_ALIASES) | {"_header"}
    other = [OtherSection(title=k.title(), items=v) for k, v in sections.items() if k not in known]

    return StructuredCV(
        full_name=full_name,
        headline=headline,
        contact=contact,
        summary=" ".join(sections.get("summary", []))[:1500],
        skills=list(dict.fromkeys(skills)),
        experience=experience,
        education=education,
        projects=projects,
        certifications=certifications,
        languages=languages,
        other_sections=other,
    )
