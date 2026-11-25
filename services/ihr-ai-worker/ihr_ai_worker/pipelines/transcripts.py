"""Transcript loading for FOLLOWUP / ANSWER_ASSESSMENT: redaction (§5.2 transcript steps) and "[i] text" lines."""
from __future__ import annotations

import json

from ihr_worker_common.storage import read_verified

from ..privacy import injection
from ..privacy.redaction import redact_transcript


def load_segments(storage, ref: dict, hints: dict | None) -> tuple[dict, list[str]]:
    """Return (transcript JSON, redacted segment texts in index order)."""
    transcript = json.loads(read_verified(storage, ref))
    names = ((hints or {}).get("fullName"),)
    texts = [redact_transcript(s["text"], names=names).text for s in sorted(transcript["segments"],
                                                                             key=lambda s: s["index"])]
    return transcript, texts


def segment_lines(texts: list[str]) -> str:
    return "\n".join(f"[{i}] {injection.escape_tags(t)}" for i, t in enumerate(texts))


def name_tokens(hints: dict | None) -> tuple[str, ...]:
    full = (hints or {}).get("fullName") or ""
    return tuple(t for t in full.replace(".", " ").split() if len(t) >= 3)
