"""Prompt-injection defences (§10, A-24): tag escaping and the heuristic detector."""
from __future__ import annotations

import re

# Every tag name used by docs/prompts/*; "<" of these tags in source text becomes "‹".
PROMPT_TAGS = ("answer_transcript", "competencies", "cv_profile", "cv_text", "document_meta",
               "expected_answer", "expected_answer_key_points", "interview_facts", "job_context",
               "overall", "prior_context", "question", "requirements", "slots", "transcript_quality", "turns")
_TAG_RE = re.compile(r"<(\s*/?\s*)(" + "|".join(sorted(PROMPT_TAGS, key=len, reverse=True)) + r")\b", re.I)

INJECTION_PATTERNS = tuple(re.compile(p) for p in (
    r"(?i)ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"(?i)(system|developer) prompt",
    r"(?i)you are (now )?(an? )?(ai|assistant|chatgpt|kimi)",
    r"(?i)(give|assign|rate) (this|the) candidate",
    r"(?i)score (of )?(5|10|100)",
    r"(?i)(mark|classify) (me|this cv) as",
))

WARNING = "PROMPT_INJECTION_SUSPECTED"


def escape_tags(text: str) -> str:
    return _TAG_RE.sub(lambda m: "‹" + m.group(1) + m.group(2), text)


def detect(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)
