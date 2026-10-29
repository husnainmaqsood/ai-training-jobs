"""Best-guess category for a listing the admin didn't categorise, so it shows up in category pages and charts."""

import re

from .models import Category

# (category slug, pattern) — first match wins, so the more specific fields come first.
RULES = [
    ("ai-red-teaming-safety", r"red[- ]?team|adversarial|jailbreak|\bsafety\b|trust (?:&|and) safety"),
    ("speech-audio-data", r"\bspeech|\baudio|\bvoice|transcri|\basr\b|phonetic"),
    ("search-ads-evaluation", r"\bsearch\b|relevance|\bads?\b|advertis|\bquality rater"),
    (
        "ai-coding-software-evaluation",
        r"\bcod(?:e|er|ing)\b|software|engineer|developer|programm|python|javascript|typescript|java\b|c\+\+|\bsql\b|full[- ]stack|back[- ]?end|front[- ]?end",
    ),
    ("data-annotation-labelling", r"annotat|label+ing|\blabel|tagging|data entry"),
    ("writing-language", r"\bwrit(?:er|ing)\b|\beditor|copywrit|translat|linguist|language|localis|localiz|bilingual|native speaker"),
    (
        "domain-expert-ai-training",
        r"\bmath|physic|chemist|biolog|medic|doctor|nurs|clinical|\blaw\b|legal|lawyer|attorney|financ|account|econom|"
        r"fact[- ]?check|\bexpert|\bphd\b|scien|tutor|teacher",
    ),
    ("llm-evaluation-rlhf", r"rlhf|\bllm|evaluat|\bprompt|response|\brater|\btrainer|reviewer|\bai\b"),
]
_COMPILED = [(slug, re.compile(pattern, re.IGNORECASE)) for slug, pattern in RULES]


def infer_category_slug(title, description=""):
    """Slug of the best-matching category, judged on the title first and the description second; '' if nothing fits."""
    for text in (title or "", description or ""):
        for slug, pattern in _COMPILED:
            if pattern.search(text):
                return slug
    return ""


def infer_category(title, description=""):
    slug = infer_category_slug(title, description)
    return Category.objects.filter(slug=slug).first() if slug else None
