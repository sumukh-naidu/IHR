"""STT_PROVIDER=mock (§11.3, A.8).

One segment per 5 s of audio, text "This is a mock answer for testing.", confidence 0.9;
audio file < 1 KB -> speechDetected=false (no-speech path).
"""
from __future__ import annotations

import os

from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified
from ihr_worker_common import contracts

from .audio import duration_ms

MOCK_TEXT = "This is a mock answer for testing."
SEGMENT_MS = 5000
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # §11.1 step 2
NO_SPEECH_BYTES = 1024


def generated_bucket() -> str:
    return os.environ.get("S3_BUCKET_GENERATED", "ihr-generated-private")


def transcript_key(p: dict) -> str:
    return f"interview/{p['interviewId']}/transcripts/{p['turnId']}/{p['jobId']}.json"


class MockTranscriptionHandler:
    task = "TRANSCRIPTION"
    request_topic = "ihr.speech.transcription.requested"
    output_schema_version = "transcript.v1"

    def output_location(self, payload: dict) -> tuple[str, str]:
        return generated_bucket(), transcript_key(payload)

    def handle(self, ctx: JobContext) -> HandlerResult:
        p = ctx.payload
        if p["audio"]["sizeBytes"] > MAX_AUDIO_BYTES:
            raise ProcessingError("INPUT_TOO_LARGE", "Audio exceeds 10 MB.")
        audio = read_verified(ctx.storage, p["audio"])
        if len(audio) > MAX_AUDIO_BYTES:
            raise ProcessingError("INPUT_TOO_LARGE", "Audio exceeds 10 MB.")

        if len(audio) < NO_SPEECH_BYTES:
            total_ms, segments, speech, flags, avg = 0, [], False, ["NO_SPEECH"], 0.0
        else:
            total_ms = duration_ms(audio)
            limit_ms = p["maxDurationSeconds"] * 1000
            flags = []
            if total_ms > limit_ms:
                total_ms = limit_ms
                flags.append("TRUNCATED_AT_LIMIT")
            segments = []
            for i, start in enumerate(range(0, max(total_ms, 1), SEGMENT_MS)):
                end = min(start + SEGMENT_MS, total_ms)
                segments.append({"index": i, "startMs": start, "endMs": end, "text": MOCK_TEXT,
                                 "confidence": 0.9, "noSpeechProb": 0.0, "words": []})
            speech, avg = bool(segments), 0.9

        transcript = {
            "schemaVersion": "transcript.v1",
            "language": "en",
            "model": "mock",
            "durationMs": total_ms,
            "speechDetected": speech,
            "avgConfidence": avg,
            "qualityFlags": flags,
            "segments": segments,
        }
        contracts.validate_artefact("transcript.v1", transcript)
        ref = put_json(ctx.storage, generated_bucket(), transcript_key(p), transcript)
        run = {
            "provider": "mock", "model": "mock", "promptId": None, "promptVersion": None,
            "promptSha256": None, "providerRequestId": None, "latencyMs": 0, "inputTokens": None,
            "outputTokens": None, "providerAttempts": 1, "repairAttempted": False,
            "validationStatus": "VALID", "workerVersion": worker_version(),
        }
        extras = {"durationMs": total_ms, "segmentCount": len(segments), "avgConfidence": avg,
                  "speechDetected": speech, "qualityFlags": flags}
        return HandlerResult(output=ref, run=run, extras=extras)
