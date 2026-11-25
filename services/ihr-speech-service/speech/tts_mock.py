"""TTS_PROVIDER=mock (§11.3, A.8) with the real cache and text guard of §11.2.

Returns the bundled MP3 tone; durationMs = 350 ms x word count (§11.3).
Cache: object tts/{voiceProfileKey}/{cacheKey}.mp3 plus its .meta.json -> cacheHit=true.
Text containing [CANDIDATE]/[EMAIL]/[PHONE] or an email/phone pattern is rejected
with OUTPUT_POLICY_VIOLATION (§11.2).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import object_ref
from ihr_worker_common import contracts

TONE = Path(__file__).parent / "assets" / "mock_tone.mp3"
MS_PER_WORD = 350

_PLACEHOLDERS = re.compile(r"\[(CANDIDATE|EMAIL|PHONE)\]")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")


def generated_bucket() -> str:
    return os.environ.get("S3_BUCKET_GENERATED", "ihr-generated-private")


def tts_key(p: dict) -> str:
    return f"tts/{p['voiceProfileKey']}/{p['cacheKey']}.mp3"


def check_text(text: str) -> None:
    phone = _PHONE.search(text)
    if _PLACEHOLDERS.search(text) or _EMAIL.search(text) or (
            phone and sum(ch.isdigit() for ch in phone.group()) >= 10):
        raise ProcessingError("OUTPUT_POLICY_VIOLATION", "Synthesis text contains personal data or placeholders.")


def _run_meta() -> dict:
    return {"provider": "mock", "model": "mock-tone", "promptId": None, "promptVersion": None,
            "promptSha256": None, "providerRequestId": None, "latencyMs": 0, "inputTokens": None,
            "outputTokens": None, "providerAttempts": 1, "repairAttempted": False,
            "validationStatus": "VALID", "workerVersion": worker_version()}


class MockSynthesisHandler:
    task = "SYNTHESIS"
    request_topic = "ihr.speech.synthesis.requested"
    output_schema_version = "tts_audio.v1"

    def output_location(self, payload: dict) -> None:
        # The TTS cache (§11.2 step 1) replaces the generic replay so cacheHit is reported correctly.
        return None

    def handle(self, ctx: JobContext) -> HandlerResult:
        p = ctx.payload
        bucket, key = generated_bucket(), tts_key(p)
        meta_key = key + ".meta.json"
        if ctx.storage.exists(bucket, key) and ctx.storage.exists(bucket, meta_key):
            meta = json.loads(ctx.storage.get_bytes(bucket, meta_key))
            audio = ctx.storage.get_bytes(bucket, key)
            extras = {**meta["extras"], "cacheHit": True}
            return HandlerResult(output=object_ref(bucket, key, audio, "audio/mpeg"), run=_run_meta(), extras=extras)

        check_text(p["text"])
        audio = TONE.read_bytes()
        extras = {"durationMs": MS_PER_WORD * len(p["text"].split()), "provider": "mock",
                  "voiceId": "mock-tone", "cacheHit": False}
        contracts.validate_artefact("tts_audio.v1", extras)
        ref = ctx.storage.put_bytes(bucket, key, audio, "audio/mpeg")
        ctx.storage.put_bytes(bucket, meta_key, json.dumps({"extras": extras}).encode(), "application/json")
        return HandlerResult(output=ref, run=_run_meta(), extras=extras)
