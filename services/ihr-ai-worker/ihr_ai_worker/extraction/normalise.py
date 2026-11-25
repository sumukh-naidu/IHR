"""Text normalisation (§6.1 step 5) and lightweight checks (§6.2)."""
from __future__ import annotations

import re
from collections import Counter

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t ]{2,}")
_WORD = re.compile(r"[^\W\d_]+")


def normalise_pages(pages: list[str]) -> list[str]:
    """Join hyphenated breaks, collapse spaces, keep line breaks, drop header/footer lines on >= 50 % of pages."""
    cleaned = []
    for p in pages:
        p = p.replace("\r\n", "\n").replace("\r", "\n")
        p = _HYPHEN_BREAK.sub(r"\1\2", p)
        p = "\n".join(_SPACES.sub(" ", ln).strip() for ln in p.split("\n"))
        cleaned.append(p)
    if len(cleaned) >= 2:
        counts = Counter()
        for p in cleaned:
            counts.update({ln for ln in p.split("\n") if ln})
        repeated = {ln for ln, n in counts.items() if n >= len(cleaned) / 2}
        cleaned = ["\n".join(ln for ln in p.split("\n") if ln not in repeated) for p in cleaned]
    return [re.sub(r"\n{3,}", "\n\n", p).strip("\n") for p in cleaned]


def non_english_ratio(text: str) -> float:
    """Share of words not written in Latin script (lightweight detector, ASSUMPTIONS #20)."""
    words = _WORD.findall(text)
    if not words:
        return 0.0
    foreign = sum(1 for w in words if not all("a" <= ch.lower() <= "z" for ch in w))
    return foreign / len(words)
