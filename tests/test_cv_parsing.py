import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from cvs.parsing import CVParseError, detect_type, extract_contact, extract_text, heuristic_structure, read_upload, redact_for_ai
from tests.conftest import FIXTURES


def upload(name, data):
    return SimpleUploadedFile(name, data)


def test_docx_and_pdf_extraction():
    for name in ["sample_cv.docx", "sample_cv.pdf"]:
        ftype, data = read_upload(upload(name, (FIXTURES / name).read_bytes()))
        text = extract_text(ftype, data)
        assert "Example Analytics Ltd" in text and "University of the Punjab" in text


def test_rejects_wrong_types_and_mismatched_extension():
    with pytest.raises(CVParseError):
        detect_type("cv.exe", b"MZ\x90\x00")
    with pytest.raises(CVParseError):
        detect_type("cv.docx", (FIXTURES / "sample_cv.pdf").read_bytes())
    with pytest.raises(CVParseError):
        detect_type("cv.pdf", b"PK\x03\x04junk")


def test_rejects_oversized_upload(settings):
    settings.CV_MAX_UPLOAD_BYTES = 1000
    with pytest.raises(CVParseError, match="too large"):
        read_upload(upload("cv.pdf", b"%PDF-" + b"0" * 2000))


def test_rejects_non_docx_zip_and_zip_bombs():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(CVParseError):
        extract_text("docx", buf.getvalue())
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "0" * (50 * 1024 * 1024))
    with pytest.raises(CVParseError, match="too large"):
        extract_text("docx", bomb.getvalue())


def test_corrupt_pdf():
    with pytest.raises(CVParseError):
        extract_text("pdf", b"%PDF-1.4 garbage")


def test_contact_extraction_and_redaction(sample_cv_text):
    c = extract_contact(sample_cv_text)
    assert c.email == "ayesha.khan@example.com" and c.phone.startswith("+92")
    red = redact_for_ai(sample_cv_text, c)
    assert "ayesha.khan@example.com" not in red and "+92 300" not in red and "[EMAIL]" in red


def test_heuristic_structure(sample_cv_text):
    cv = heuristic_structure(sample_cv_text)
    assert cv.full_name == "Ayesha Khan"
    assert [(e.title, e.employer, e.start_date, e.end_date) for e in cv.experience] == [
        ("Software Engineer", "Example Analytics Ltd", "Jan 2022", "Present"),
        ("Data Annotator", "Freelance", "Jun 2020", "Dec 2021"),
    ]
    assert len(cv.experience[0].bullets) == 3 and "Python" in cv.skills


@pytest.mark.django_db
def test_upload_view_stores_original_and_text(client_user, user, plans):
    data = (FIXTURES / "sample_cv.docx").read_bytes()
    resp = client_user.post("/cv/upload/", {"title": "Mine", "file": upload("sample_cv.docx", data), "make_primary": "on"})
    assert resp.status_code == 302
    cv = user.master_cvs.get()
    assert cv.original_file and cv.file_type == "docx" and "Example Analytics" in cv.raw_text
    dl = client_user.get(f"/cv/master/{cv.public_id}/download/")
    assert dl.status_code == 200 and b"".join(dl.streaming_content) == data


@pytest.mark.django_db
def test_upload_limit_enforced(client_user, user, plans, sample_cv_text):
    client_user.post("/cv/upload/", {"title": "1", "text": sample_cv_text})
    client_user.post("/cv/upload/", {"title": "2", "text": sample_cv_text})
    assert user.master_cvs.count() == 1  # free plan stores one master CV


@pytest.mark.django_db
def test_editing_master_text_keeps_original_file(client_user, user, plans):
    data = (FIXTURES / "sample_cv.docx").read_bytes()
    client_user.post("/cv/upload/", {"title": "Mine", "file": upload("sample_cv.docx", data)})
    cv = user.master_cvs.get()
    original_name = cv.original_file.name
    client_user.post(cv.get_absolute_url(), {"title": "Mine", "raw_text": cv.raw_text + "\nVolunteering\nTaught kids to code"})
    cv.refresh_from_db()
    assert cv.original_file.name == original_name and "Taught kids" in cv.raw_text
