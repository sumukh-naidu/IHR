"""Evidence quote verification (A-23), shared by every task."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

MIN_LEN, MAX_LEN = 8, 300
FUZZY_THRESHOLD = 90
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
                         "«": '"', "»": '"', "‹": "'", "›": "'",
                         "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
                         "―": "-", "−": "-"})
_WS = re.compile(r"\s+")


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).translate(_QUOTES).lower()
    return _WS.sub(" ", s).strip()


def _alnum(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum())


def quote_found(quote: str, source: str) -> bool:
    if not isinstance(quote, str) or not (MIN_LEN <= len(quote) <= MAX_LEN):
        return False
    q, src = normalise(quote), normalise(source)
    if not q or not src:
        return False
    if q in src:
        return True
    al = fuzz.partial_ratio_alignment(q, src, score_cutoff=FUZZY_THRESHOLD)
    if al is None:
        return False
    return _alnum(q) == _alnum(src[al.dest_start:al.dest_end]) or _alnum(q) in _alnum(
        src[max(0, al.dest_start - 3):al.dest_end + 3])


def quote_in_segments(quote: str, segments: list[str], index: int) -> bool:
    """Transcripts: inside the cited segment, ±1 neighbour (quotes may span a boundary)."""
    if not (0 <= index < len(segments)):
        return False
    windows = [[index], [index - 1], [index + 1], [index - 1, index], [index, index + 1]]
    for w in windows:
        if all(0 <= i < len(segments) for i in w) and quote_found(quote, " ".join(segments[i] for i in w)):
            return True
    return False
