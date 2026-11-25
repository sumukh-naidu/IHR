"""Redaction pipeline (AI Specification v1.1 §5.2). Pure functions, applied in this order.

CV text gets all 10 steps; transcripts get steps 1-5, 9 and 10.
Returns the redacted text and per-type counts (no values) for extraction.redactionCounts.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import phonenumbers

COUNT_KEYS = ("EMAIL", "PHONE", "URL", "GOV_ID", "CANDIDATE", "DOB", "LINES_REMOVED")

# Step 1
_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿­]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Step 2
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Step 3
URL_RES = (
    re.compile(r"(?i)\b(?:https?://|www\.)\S+"),
    re.compile(r"(?i)\b(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|medium\.com)/\S*"),
)
# Step 4
PHONE_FALLBACK_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
# Step 5
AADHAAR_RE = re.compile(r"\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PASSPORT_RE = re.compile(r"\b[A-Z][1-9]\d\s?\d{4}[1-9]\b")
# Step 6
PERSONAL_LINE_RE = re.compile(
    r"(?i)^\s*(date of birth|d\.?o\.?b|born|age|gender|sex|marital status|religion|caste|category|"
    r"father'?s name|mother'?s name|spouse|husband|wife|nationality|citizenship|passport|height|weight|"
    r"blood group|languages known|hobbies|permanent address|present address|address)\s*[:\-–]")
# Step 7
PIN_RE = re.compile(r"\b[1-9]\d{5}\b")
ADDRESS_WORD_RE = re.compile(
    r"(?i)\b(road|rd|street|nagar|colony|sector|flat|house|apartment|near|dist|district|village|post|"
    r"taluk|tehsil|pin)\b")
# Step 8
DECLARATION_RE = re.compile(r"(?i)^\s*declaration\b")
DECLARATION_WINDOW = 15
# Step 10
DOB_RE = re.compile(r"(?i)\b(born on|birth ?date)\b[^\n]{0,30}")


@dataclass
class Redacted:
    text: str
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(COUNT_KEYS, 0))


def normalise_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    return _CONTROL.sub("", text)


def _sub(pattern: re.Pattern, repl: str, text: str) -> tuple[str, int]:
    return pattern.subn(repl, text)


def _digits(s: str) -> int:
    return sum(ch.isdigit() for ch in s)


def redact_phones(text: str) -> tuple[str, int]:
    spans: list[tuple[int, int]] = []
    for m in phonenumbers.PhoneNumberMatcher(text, "IN", leniency=phonenumbers.Leniency.VALID):
        spans.append((m.start, m.end))
    for m in PHONE_FALLBACK_RE.finditer(text):
        s = m.group(0)
        # Aadhaar-shaped numbers are left for step 5 ([GOV_ID]); ASSUMPTIONS #20.
        if _digits(s) >= 10 and not AADHAAR_RE.fullmatch(s.strip()):
            spans.append((m.start(), m.end()))
    spans = [sp for sp in spans if not AADHAAR_RE.fullmatch(text[sp[0]:sp[1]].strip())]
    if not spans:
        return text, 0
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, last = [], 0
    for s, e in merged:
        out.append(text[last:s])
        out.append("[PHONE]")
        last = e
    out.append(text[last:])
    return "".join(out), len(merged)


def _drop_lines(text: str, predicate) -> tuple[str, int]:
    lines = text.split("\n")
    kept = [ln for ln in lines if not predicate(ln)]
    return "\n".join(kept), len(lines) - len(kept)


def _is_address_line(line: str) -> bool:
    return bool(PIN_RE.search(line) and ADDRESS_WORD_RE.search(line))


def _drop_declaration(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    # trailing blank lines do not count towards the 15-line window
    end = len(lines)
    while end and not lines[end - 1].strip():
        end -= 1
    for i in range(max(0, end - DECLARATION_WINDOW), end):
        if DECLARATION_RE.match(lines[i]):
            removed = sum(1 for ln in lines[i:end] if ln.strip())
            return "\n".join(lines[:i]), removed
    return text, 0


def name_tokens(*names: str | None) -> tuple[list[str], list[str]]:
    phrases, tokens = [], []
    for name in names:
        if not name or not name.strip():
            continue
        phrase = " ".join(name.split())
        if phrase not in phrases:
            phrases.append(phrase)
        for tok in re.findall(r"[^\W\d_]+", phrase):
            if len(tok) >= 3 and tok.lower() not in (t.lower() for t in tokens):
                tokens.append(tok)
    return phrases, tokens


def redact_names(text: str, *names: str | None) -> tuple[str, int]:
    phrases, tokens = name_tokens(*names)
    total = 0
    for phrase in sorted(phrases, key=len, reverse=True):
        parts = [re.escape(p) for p in phrase.split()]
        pat = re.compile(r"(?i)(?<!\w)" + r"\s+".join(parts) + r"(?!\w)")
        text, n = pat.subn("[CANDIDATE]", text)
        total += n
    for tok in sorted(tokens, key=len, reverse=True):
        pat = re.compile(r"(?i)(?<!\w)" + re.escape(tok) + r"(?!\w)")
        text, n = pat.subn("[CANDIDATE]", text)
        total += n
    return text, total


def redact(text: str, *, names: tuple[str | None, ...] = (), cv: bool = True) -> Redacted:
    """§5.2 steps 1-10 (cv=True) or steps 1-5, 9, 10 (transcripts)."""
    r = Redacted(normalise_unicode(text))
    r.text, r.counts["EMAIL"] = _sub(EMAIL_RE, "[EMAIL]", r.text)
    for pat in URL_RES:
        r.text, n = _sub(pat, "[URL]", r.text)
        r.counts["URL"] += n
    r.text, r.counts["PHONE"] = redact_phones(r.text)
    for pat in (AADHAAR_RE, PAN_RE, PASSPORT_RE):
        r.text, n = _sub(pat, "[GOV_ID]", r.text)
        r.counts["GOV_ID"] += n
    if cv:
        r.text, n6 = _drop_lines(r.text, lambda ln: bool(PERSONAL_LINE_RE.match(ln)))
        r.text, n7 = _drop_lines(r.text, _is_address_line)
        r.text, n8 = _drop_declaration(r.text)
        r.counts["LINES_REMOVED"] = n6 + n7 + n8
    r.text, r.counts["CANDIDATE"] = redact_names(r.text, *names)
    r.text, r.counts["DOB"] = _sub(DOB_RE, "[DOB]", r.text)
    return r


def redact_transcript(text: str, *, names: tuple[str | None, ...] = ()) -> Redacted:
    return redact(text, names=names, cv=False)
