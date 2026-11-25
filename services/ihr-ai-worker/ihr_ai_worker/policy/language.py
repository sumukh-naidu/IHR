"""Output language policy (A-22, §9.2): hiring recommendations, integrity judgements, protected attributes."""
from __future__ import annotations

import re

from . import lexicon

HIRING = (
    re.compile(r"(?i)\b(should|must|recommend(ed)?( to)?|strongly recommend)\b.{0,20}\b(hire[ds]?|hiring|reject(s|ed|ion)?|shortlist(s|ed|ing)?|progress(ed)?|advance[ds]?|select(s|ed)?)\b"),
    # ^ §9.2 regex plus inflections, so A-22's own example "should be hired" is caught (ASSUMPTIONS #23)
    re.compile(r"(?i)\b(strong|clear|definite) (hire|no[- ]hire)\b"),
    re.compile(r"(?i)\b(not suitable|unsuitable|best candidate|ideal candidate|top candidate)\b"),
)
INTEGRITY = re.compile(r"(?i)\b(cheat(ing|ed)?|dishonest|lying|lied|fabricat\w*|plagiar\w*|used (chatgpt|ai|an ai)|external help)\b")


def violations(text: str) -> list[str]:
    out = []
    if any(p.search(text) for p in HIRING):
        out.append("HIRING_RECOMMENDATION")
    if INTEGRITY.search(text):
        out.append("INTEGRITY_JUDGEMENT")
    lex = lexicon.load()
    if lex.protected_hits(text) or lex.language_extra.search(text):
        out.append("PROTECTED_ATTRIBUTE")
    return out


def check_fields(fields: dict[str, str]) -> list[str]:
    """fields: JSON path -> text. Returns repair-ready errors naming the rule (no source text)."""
    return [f"{path}: language policy {rule}" for path, text in fields.items() for rule in violations(text or "")]
