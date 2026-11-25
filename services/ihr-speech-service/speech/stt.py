"""Speech-to-text with faster-whisper (AI Specification v1.1 §11.1, A-26, A-27).

Shared WhisperModel instance; concurrency = WHISPER_NUM_WORKERS. Warm-up transcription of a bundled
3-second clip runs before readiness. CUDA OOM -> reload model once and retry the job; second failure ->
INTERNAL_ERROR (retryable) and liveness fails so Kubernetes restarts the pod.
"""
from __future__ import annotations

import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter, histogram
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified

from .audio import TARGET_RATE, clipping_ratio, decode_16k_mono
from .stt_mock import MAX_AUDIO_BYTES, generated_bucket, transcript_key

log = get_logger("ihr.stt")

WARMUP_CLIP = Path(__file__).parent / "assets" / "warmup_3s.wav"
PROMPT_PREFIX = "Interview answer. Terms: "
PROMPT_MAX = 200
# §11.1 step 6
DROP_NO_SPEECH_PROB, DROP_LOGPROB = 0.6, -1.0
# A-27
NO_SPEECH_MIN_S = 1.5
LOW_CONFIDENCE = 0.55
HALLUCINATION_COMPRESSION = 2.4
NGRAM, NGRAM_REPEATS = 4, 3
CLIPPED_SHARE = 0.01


@dataclass(frozen=True)
class WhisperConfig:
    model: str = "large-v3-turbo"
    model_dir: str = "/models/whisper"
    device: str = "cuda"
    compute_type: str = "float16"
    num_workers: int = 2
    max_audio_seconds: int = 400

    @classmethod
    def from_env(cls) -> "WhisperConfig":
        return cls(
            model=os.environ.get("WHISPER_MODEL", "large-v3-turbo"),
            model_dir=os.environ.get("WHISPER_MODEL_DIR", "/models/whisper"),
            device=os.environ.get("WHISPER_DEVICE", "cuda"),
            compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "float16"),
            num_workers=int(os.environ.get("WHISPER_NUM_WORKERS", "2")),
            max_audio_seconds=int(os.environ.get("STT_MAX_AUDIO_SECONDS", "400")),
        )

    def model_path(self) -> str:
        local = Path(self.model_dir) / self.model
        return str(local) if local.exists() else self.model


def initial_prompt(hints: list[str] | None) -> str:
    """§11.1 step 4: prefix + comma-joined hints, truncated to 200 chars at a comma."""
    text = PROMPT_PREFIX + ", ".join(h.strip() for h in (hints or []) if h and h.strip())
    if len(text) <= PROMPT_MAX:
        return text
    cut = text[:PROMPT_MAX]
    return cut[:cut.rfind(",")] if "," in cut[len(PROMPT_PREFIX):] else cut


def transcribe_params(prompt: str) -> dict:
    """A-26 parameters."""
    return dict(language="en", task="transcribe", beam_size=5, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200}, word_timestamps=True,
                condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4], compression_ratio_threshold=2.4,
                log_prob_threshold=-1.0, no_speech_threshold=0.6, initial_prompt=prompt)


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def post_process(raw_segments) -> tuple[list[dict], list[float]]:
    """§11.1 step 6. Returns (segments, compression ratios of kept segments)."""
    kept, ratios = [], []
    for s in raw_segments:
        if s.no_speech_prob > DROP_NO_SPEECH_PROB and s.avg_logprob < DROP_LOGPROB:
            continue
        text = s.text.strip()
        if not text:
            continue
        if kept and kept[-1]["text"] == text:          # collapse exact repeated adjacent segments
            kept[-1]["endMs"] = max(kept[-1]["endMs"], _ms(s.end))
            continue
        words = [{"w": w.word.strip(), "startMs": _ms(w.start), "endMs": _ms(w.end), "p": round(_clamp(w.probability), 4)}
                 for w in (s.words or []) if w.word.strip()]
        kept.append({"index": 0, "startMs": _ms(s.start), "endMs": max(_ms(s.end), _ms(s.start)), "text": text,
                     "confidence": round(_clamp(math.exp(s.avg_logprob)), 4),
                     "noSpeechProb": round(_clamp(s.no_speech_prob), 4), "words": words})
        ratios.append(float(getattr(s, "compression_ratio", 0.0) or 0.0))
    for i, seg in enumerate(kept):
        seg["index"] = i
    return kept, ratios


def _repeated_ngram(text: str) -> bool:
    words = re.findall(r"[\w']+", text.lower())
    counts: dict[tuple, int] = {}
    for i in range(len(words) - NGRAM + 1):
        g = tuple(words[i:i + NGRAM])
        counts[g] = counts.get(g, 0) + 1
        if counts[g] >= NGRAM_REPEATS:
            return True
    return False


def quality(segments: list[dict], ratios: list[float], clipped: float, truncated: bool) -> tuple[list[str], float, bool]:
    """A-27 flags, duration-weighted avgConfidence, speechDetected."""
    flags = []
    durations = [max(0, s["endMs"] - s["startMs"]) for s in segments]
    speech_s = sum(durations) / 1000
    total = sum(durations)
    avg = sum(s["confidence"] * d for s, d in zip(segments, durations)) / total if total else 0.0
    no_speech = not segments or speech_s < NO_SPEECH_MIN_S
    if no_speech:
        flags.append("NO_SPEECH")
    elif avg < LOW_CONFIDENCE:
        flags.append("LOW_CONFIDENCE")
    if any(r > HALLUCINATION_COMPRESSION for r in ratios) or _repeated_ngram(" ".join(s["text"] for s in segments)):
        flags.append("POSSIBLE_HALLUCINATION")
    if clipped > CLIPPED_SHARE:
        flags.append("CLIPPED_AUDIO")
    if truncated:
        flags.append("TRUNCATED_AT_LIMIT")
    return flags, round(_clamp(avg), 4), not no_speech


class WhisperEngine:
    """Owns the shared model; reload-once on CUDA OOM (§11.1 failure handling)."""

    def __init__(self, cfg: WhisperConfig, loader=None) -> None:
        self.cfg = cfg
        self._loader = loader or self._load
        self._lock = threading.Lock()
        self.model = None
        self.ready = False
        self.alive = True

    def _load(self):
        from faster_whisper import WhisperModel
        return WhisperModel(self.cfg.model_path(), device=self.cfg.device, compute_type=self.cfg.compute_type,
                            num_workers=self.cfg.num_workers)

    def start(self) -> None:
        self.model = self._loader()
        if WARMUP_CLIP.exists():
            samples, _ = decode_16k_mono(WARMUP_CLIP.read_bytes())
            segs, _ = self.model.transcribe(samples, **transcribe_params(PROMPT_PREFIX.rstrip(": ")))
            list(segs)
        self.ready = True
        log.info("whisper_ready", model=self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type)

    def transcribe(self, samples, prompt: str):
        try:
            return self._run(samples, prompt)
        except Exception as e:  # noqa: BLE001
            if not _is_oom(e):
                raise
            log.warning("whisper_oom_reloading")
            with self._lock:
                self.model = self._loader()
            try:
                return self._run(samples, prompt)
            except Exception as e2:  # noqa: BLE001
                if _is_oom(e2):
                    self.alive = False
                    raise ProcessingError("INTERNAL_ERROR", "Speech model ran out of memory.") from None
                raise

    def _run(self, samples, prompt):
        segments, info = self.model.transcribe(samples, **transcribe_params(prompt))
        return list(segments), info   # segments is a generator; force decoding inside the OOM guard


def _is_oom(e: Exception) -> bool:
    text = str(e).lower()
    return "out of memory" in text or "cuda_error_out_of_memory" in text or "cublas_status_alloc_failed" in text


class TranscriptionHandler:
    task = "TRANSCRIPTION"
    request_topic = "ihr.speech.transcription.requested"
    output_schema_version = "transcript.v1"

    def __init__(self, engine: WhisperEngine) -> None:
        self.engine = engine
        self._dur = histogram("ihr_stt_duration_seconds", "STT processing time", ())
        self._audio = histogram("ihr_stt_audio_seconds", "Audio duration", ())
        self._flags = counter("ihr_stt_quality_flags_total", "STT quality flags", ("flag",))

    def output_location(self, payload: dict) -> tuple[str, str]:
        return generated_bucket(), transcript_key(payload)

    def handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        p = ctx.payload
        if p["audio"]["sizeBytes"] > MAX_AUDIO_BYTES:
            raise ProcessingError("INPUT_TOO_LARGE", "Audio exceeds 10 MB.")
        audio = read_verified(ctx.storage, p["audio"])
        if len(audio) > MAX_AUDIO_BYTES:
            raise ProcessingError("INPUT_TOO_LARGE", "Audio exceeds 10 MB.")
        stats: dict = {}
        samples, duration = decode_16k_mono(audio, stats=stats)
        limit_s = min(p["maxDurationSeconds"], self.engine.cfg.max_audio_seconds)
        truncated = duration > limit_s * 1000
        if truncated:
            samples = samples[: limit_s * TARGET_RATE]
            duration = limit_s * 1000
        clipped = stats.get("clipped", clipping_ratio(samples))

        raw, _info = self.engine.transcribe(samples, initial_prompt(p.get("vocabularyHints")))
        segments, ratios = post_process(raw)
        flags, avg, speech = quality(segments, ratios, clipped, truncated)
        transcript = {"schemaVersion": "transcript.v1", "language": "en", "model": self.engine.cfg.model,
                      "durationMs": duration, "speechDetected": speech, "avgConfidence": avg,
                      "qualityFlags": flags, "segments": segments}
        contracts.validate_artefact("transcript.v1", transcript)
        ref = put_json(ctx.storage, generated_bucket(), transcript_key(p), transcript)

        elapsed = time.monotonic() - t0
        self._dur.observe(elapsed)
        self._audio.observe(duration / 1000)
        for f in flags:
            self._flags.labels(f).inc()
        run = {"provider": "faster-whisper", "model": self.engine.cfg.model, "promptId": None, "promptVersion": None,
               "promptSha256": None, "providerRequestId": None, "latencyMs": int(elapsed * 1000), "inputTokens": None,
               "outputTokens": None, "providerAttempts": 1, "repairAttempted": False, "validationStatus": "VALID",
               "workerVersion": worker_version()}
        extras = {"durationMs": duration, "segmentCount": len(segments), "avgConfidence": avg,
                  "speechDetected": speech, "qualityFlags": flags}
        log.info("stt_done", job_id=ctx.job_id, audio_s=duration / 1000, rtf=round(elapsed / max(duration / 1000, 0.1), 3),
                 segments=len(segments), flags=flags)
        return HandlerResult(output=ref, run=run, extras=extras)
