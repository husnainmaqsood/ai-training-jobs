import logging
import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
KEY_RE = re.compile(r"\b(sk|pk|rk|whsec)_[A-Za-z0-9_-]{8,}")


def redact(text):
    text = KEY_RE.sub("[REDACTED_KEY]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return PHONE_RE.sub("[REDACTED_PHONE]", text)


class RedactSensitiveFilter(logging.Filter):
    """Strips emails, phone numbers and API keys from ordinary log lines.

    CV and job-description text must never be logged in the first place; this filter is a safety net.
    """

    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = redact(msg)
        record.args = ()
        return True
