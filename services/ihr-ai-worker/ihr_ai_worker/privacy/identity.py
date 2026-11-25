"""Local identity extraction (A-17): never sent to Kimi; run on the unredacted text."""
from __future__ import annotations

import math
import re

import phonenumbers

from .redaction import EMAIL_RE

NAME_CONFIDENCE = 0.6
TOP_FRACTION = 0.15
_WORD = re.compile(r"^[A-Z][a-zA-Z'.-]*$")


def _field(value: str | None, confidence: float, source: str) -> dict | None:
    if value is None:
        return None
    return {"value": value, "confidence": confidence, "source": source}


def extract_email(text: str) -> dict | None:
    m = EMAIL_RE.search(text)
    return _field(m.group(0), 1.0, "LOCAL_REGEX") if m else None


def extract_phone(text: str) -> dict | None:
    for m in phonenumbers.PhoneNumberMatcher(text, "IN", leniency=phonenumbers.Leniency.VALID):
        return _field(phonenumbers.format_number(m.number, phonenumbers.PhoneNumberFormat.E164), 1.0,
                      "LOCAL_REGEX")
    return None


def _heading_like(line: str) -> bool:
    words = line.split()
    return 2 <= len(words) <= 4 and all(_WORD.match(w) for w in words)


def _norm(s: str) -> str:
    """Case-, punctuation- and spacing-insensitive form: "RAHUL K. MENON" == "Rahul K Menon"."""
    return " ".join(re.findall(r"[^\W_]+", s.lower()))


def extract_name(page1: str, known_name: str | None) -> dict | None:
    lines = [ln.strip() for ln in page1.split("\n") if ln.strip()]
    if not lines:
        return None
    if known_name and known_name.strip():
        target = _norm(known_name)
        for ln in lines:
            n = _norm(ln)
            if n == target or (len(ln.split()) <= 6 and f" {target} " in f" {n} "):
                return _field(" ".join(ln.split()), NAME_CONFIDENCE, "LOCAL_HEADER")
        return None
    top = lines[: max(1, math.ceil(len(lines) * TOP_FRACTION))]
    for ln in top:
        if _heading_like(ln):
            return _field(" ".join(ln.split()), NAME_CONFIDENCE, "LOCAL_HEADER")
    return None


def extract_identity(text: str, page1: str, known_name: str | None) -> dict:
    return {"name": extract_name(page1, known_name), "email": extract_email(text), "phone": extract_phone(text)}
