"""Question policy (A-21, §9.1): applies to plan questions, follow-ups, HR edits and clarification questions."""
from __future__ import annotations

import re

from . import lexicon

MAX_LEN, FOLLOWUP_MAX_LEN, MIN_LEN = 300, 200, 20
PLACEHOLDERS = re.compile(r"\[(CANDIDATE|EMAIL|PHONE)\]")
ACCUSATORY = re.compile(r"(?i)\b(did you really|are you sure you|is it true that|prove that|fabricat|lied|honest(ly)? tell)\b")
DOC_REQUEST = re.compile(r"(?i)\b(send|share|upload)\b.*\b(id|aadhaar|pan|passport|payslip|salary slip)\b")


def violations(question: str, *, followup: bool = False, name_tokens: tuple[str, ...] = (),
               competency_codes: list[str] | None = None, allowed_codes: set[str] | None = None) -> list[str]:
    """Return the failed rule names (empty list = passes)."""
    out = []
    n = len(question.strip())
    if n > (FOLLOWUP_MAX_LEN if followup else MAX_LEN) or n < MIN_LEN:
        out.append("LENGTH")
    if question.count("?") > 2:
        out.append("MULTIPLE_QUESTIONS")
    if PLACEHOLDERS.search(question) or any(
            len(t) >= 3 and re.search(r"(?i)(?<!\w)" + re.escape(t) + r"(?!\w)", question) for t in name_tokens):
        out.append("PERSONAL_DATA")
    if lexicon.load().protected_hits(question):
        out.append("PROTECTED_TOPIC")
    if ACCUSATORY.search(question):
        out.append("ACCUSATORY")
    if DOC_REQUEST.search(question):
        out.append("DOCUMENT_REQUEST")
    if competency_codes is not None and allowed_codes is not None and not set(competency_codes) <= allowed_codes:
        out.append("OFF_COMPETENCY")
    return out


def passes(question: str, **kw) -> bool:
    return not violations(question, **kw)
