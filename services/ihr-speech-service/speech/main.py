"""ihr-speech-service entrypoint (A-29). WORKER_ROLE: stt (group ihr-speech-stt) or tts (group ihr-speech-tts)."""
from __future__ import annotations

import os
import sys

from ihr_worker_common import log as wlog
from ihr_worker_common.health import build_app, serve_in_background
from ihr_worker_common.processor import Processor
from ihr_worker_common.runner import KafkaRunner
from ihr_worker_common.storage import S3Storage

from .stt import TranscriptionHandler, WhisperConfig, WhisperEngine
from .stt_mock import MockTranscriptionHandler
from .tts import SynthesisHandler, build_providers
from .tts_mock import MockSynthesisHandler


ENGINE: WhisperEngine | None = None


def build_handler(role: str):
    global ENGINE
    if role == "stt":
        provider = os.environ.get("STT_PROVIDER", "mock")
        if provider == "mock":
            return MockTranscriptionHandler()
        if provider != "faster-whisper":
            raise SystemExit("STT_PROVIDER must be faster-whisper or mock")
        ENGINE = WhisperEngine(WhisperConfig.from_env())
        return TranscriptionHandler(ENGINE)
    if role == "tts":
        primary = os.environ.get("TTS_PROVIDER_PRIMARY", "mock")
        if primary == "mock":
            return MockSynthesisHandler()
        return SynthesisHandler(build_providers(primary, os.environ.get("TTS_PROVIDER_SECONDARY", "polly")))
    raise SystemExit("WORKER_ROLE must be stt or tts")


def main() -> int:
    role = os.environ.get("WORKER_ROLE", "stt")
    wlog.configure("ihr-speech-service")
    handler = build_handler(role)
    concurrency = int(os.environ.get("WHISPER_NUM_WORKERS", "2")) if role == "stt" else 8
    processor = Processor(handler, S3Storage(), "ihr-speech-service", "ihr_speech", max_workers=concurrency)
    if ENGINE is not None:   # health first so liveness answers while the model loads; ready only after warm-up
        serve_in_background(build_app(f"ihr-speech-{role}", ready=lambda: ENGINE.ready, alive=lambda: ENGINE.alive),
                            int(os.environ.get("HEALTH_PORT", "8080")))
        ENGINE.start()
    else:
        serve_in_background(build_app(f"ihr-speech-{role}"), int(os.environ.get("HEALTH_PORT", "8080")))
    runner = KafkaRunner([processor], f"ihr-speech-{role}", concurrency)
    runner.install_signal_handlers()
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
