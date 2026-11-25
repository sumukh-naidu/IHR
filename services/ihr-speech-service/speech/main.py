"""ihr-speech-service entrypoint (A-29). WORKER_ROLE: stt (group ihr-speech-stt) or tts (group ihr-speech-tts)."""
from __future__ import annotations

import os
import sys

from ihr_worker_common import log as wlog
from ihr_worker_common.health import build_app, serve_in_background
from ihr_worker_common.processor import Processor
from ihr_worker_common.runner import KafkaRunner
from ihr_worker_common.storage import S3Storage

from .stt_mock import MockTranscriptionHandler
from .tts_mock import MockSynthesisHandler


def build_handler(role: str):
    if role == "stt":
        if os.environ.get("STT_PROVIDER", "mock") != "mock":
            raise SystemExit("STT_PROVIDER=faster-whisper is delivered in Sprint 3; use STT_PROVIDER=mock")
        return MockTranscriptionHandler()
    if role == "tts":
        if os.environ.get("TTS_PROVIDER_PRIMARY", "mock") != "mock":
            raise SystemExit("Azure/Polly TTS is delivered in Sprint 4; use TTS_PROVIDER_PRIMARY=mock")
        return MockSynthesisHandler()
    raise SystemExit("WORKER_ROLE must be stt or tts")


def main() -> int:
    role = os.environ.get("WORKER_ROLE", "stt")
    wlog.configure("ihr-speech-service")
    handler = build_handler(role)
    concurrency = int(os.environ.get("WHISPER_NUM_WORKERS", "2")) if role == "stt" else 8
    processor = Processor(handler, S3Storage(), "ihr-speech-service", "ihr_speech", max_workers=concurrency)
    serve_in_background(build_app(f"ihr-speech-{role}"), int(os.environ.get("HEALTH_PORT", "8080")))
    runner = KafkaRunner([processor], f"ihr-speech-{role}", concurrency)
    runner.install_signal_handlers()
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
