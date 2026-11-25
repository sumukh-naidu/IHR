"""Text-to-speech (AI Specification v1.1 §11.2, A-28, S-13).

Primary Azure AI Speech (Central India, en-IN-NeerjaNeural), failover Amazon Polly (ap-south-1, Kajal, neural)
after 1 failed attempt or an open circuit. Content-addressed cache tts/{voiceProfileKey}/{cacheKey}.mp3 (+ .meta.json).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter, gauge, histogram
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import object_ref

from .audio import duration_ms
from .tts_mock import check_text, generated_bucket, tts_key

log = get_logger("ihr.tts")

MAX_CHARS = 600
TIMEOUT_S = 6.0
MIN_AUDIO_MS = 300
AZURE_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
LEXICON = Path(__file__).parent / "lexicon.yaml"


class ProviderFailed(Exception):
    """Timeout, 429, 5xx, network error or invalid audio: try the next provider."""


class Breaker:
    """5 consecutive failures -> open 60 s (§11.2)."""

    def __init__(self, threshold: int = 5, open_s: float = 60.0, clock=time.monotonic, name: str | None = None) -> None:
        self.threshold, self.open_s, self.clock = threshold, open_s, clock
        self._gauge = gauge("ihr_provider_circuit_open", "Circuit state (0/1)", ("provider",)).labels(name) if name else None
        self.failures, self.opened_at = 0, None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.opened_at is None:
                return True
            if self.clock() - self.opened_at >= self.open_s:   # half-open: allow a trial call
                self.opened_at = None
                self.failures = self.threshold - 1
                self._set(0)
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self.failures, self.opened_at = 0, None
            self._set(0)

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = self.clock()
                self._set(1)

    def _set(self, v: int) -> None:
        if self._gauge is not None:
            self._gauge.set(v)


@lru_cache(maxsize=1)
def _lexicon() -> list[tuple[re.Pattern, str]]:
    subs = yaml.safe_load(LEXICON.read_text(encoding="utf-8"))["substitutions"]
    out = []
    for term in sorted(subs, key=len, reverse=True):
        out.append((re.compile(r"(?<![\w.#+/])" + re.escape(term) + r"(?![\w#+/])"), subs[term]))
    return out


def normalise_for_speech(text: str) -> str:
    """§11.2 step 2: collapse whitespace, apply the speech lexicon, escape XML for SSML."""
    text = " ".join(text.split())
    for pat, repl in _lexicon():
        text = pat.sub(repl, text)
    return escape(text)


def azure_ssml(text: str, voice: str) -> str:
    return (f'<speak version="1.0" xml:lang="en-IN"><voice name="{voice}">'
            f'<prosody rate="-5%">{text}</prosody></voice></speak>')


def polly_ssml(text: str) -> str:
    return f'<speak><prosody rate="95%">{text}</prosody></speak>'


class AzureTts:
    name = "azure-speech"

    def __init__(self, key: str | None = None, region: str | None = None, voice: str | None = None,
                 client=None) -> None:
        import httpx
        self.key = key or os.environ.get("AZURE_SPEECH_KEY", "")
        self.region = region or os.environ.get("AZURE_SPEECH_REGION", "centralindia")
        self.voice = voice or os.environ.get("AZURE_SPEECH_VOICE", "en-IN-NeerjaNeural")
        self.client = client or httpx.Client(timeout=TIMEOUT_S)

    @property
    def voice_id(self) -> str:
        return self.voice

    def synthesize(self, speech_text: str) -> bytes:
        import httpx
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {"Ocp-Apim-Subscription-Key": self.key, "Content-Type": "application/ssml+xml",
                   "X-Microsoft-OutputFormat": AZURE_FORMAT, "User-Agent": "ihr-speech-service"}
        try:
            r = self.client.post(url, content=azure_ssml(speech_text, self.voice).encode("utf-8"), headers=headers,
                                 timeout=TIMEOUT_S)
        except httpx.HTTPError as e:
            raise ProviderFailed(type(e).__name__) from None
        if r.status_code != 200:
            # 429 / 5xx are the documented failover cases; 4xx config errors also fail over (and alert via metrics)
            raise ProviderFailed(f"http {r.status_code}")
        return r.content


class PollyTts:
    name = "aws-polly"

    def __init__(self, region: str | None = None, voice: str | None = None, client=None) -> None:
        self.region = region or os.environ.get("AWS_REGION", "ap-south-1")
        self.voice = voice or os.environ.get("POLLY_VOICE", "Kajal")
        self._client = client

    @property
    def voice_id(self) -> str:
        return self.voice

    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client("polly", region_name=self.region, config=Config(
                connect_timeout=TIMEOUT_S, read_timeout=TIMEOUT_S, retries={"max_attempts": 1}))
        return self._client

    def synthesize(self, speech_text: str) -> bytes:
        try:
            resp = self.client().synthesize_speech(Engine="neural", VoiceId=self.voice, LanguageCode="en-IN",
                                                   OutputFormat="mp3", SampleRate="24000", TextType="ssml",
                                                   Text=polly_ssml(speech_text))
            return resp["AudioStream"].read()
        except Exception as e:  # noqa: BLE001 - botocore raises many types; all mean "try next / fail"
            raise ProviderFailed(type(e).__name__) from None


def validate_audio(data: bytes) -> int:
    """§11.2 step 5: non-empty, decodable, duration >= 300 ms."""
    if not data:
        raise ProviderFailed("empty audio")
    try:
        ms = duration_ms(data)
    except ProcessingError:
        raise ProviderFailed("undecodable audio") from None
    if ms < MIN_AUDIO_MS:
        raise ProviderFailed("audio too short")
    return ms


class SynthesisHandler:
    task = "SYNTHESIS"
    request_topic = "ihr.speech.synthesis.requested"
    output_schema_version = "tts_audio.v1"

    def __init__(self, providers: list) -> None:
        self.providers = providers
        self.breakers = {p.name: Breaker(name=p.name) for p in providers}
        self._req = counter("ihr_tts_requests_total", "TTS requests", ("provider", "outcome"))
        self._lat = histogram("ihr_tts_latency_seconds", "TTS provider latency", ("provider",))
        self._hits = counter("ihr_tts_cache_hits_total", "TTS cache hits", ())

    def output_location(self, payload: dict) -> None:
        return None   # the content-addressed cache (§11.2 step 1) replaces the generic replay

    def _run(self, provider: str, voice: str, t0: float) -> dict:
        return {"provider": provider, "model": voice, "promptId": None, "promptVersion": None, "promptSha256": None,
                "providerRequestId": None, "latencyMs": int((time.monotonic() - t0) * 1000), "inputTokens": None,
                "outputTokens": None, "providerAttempts": 1, "repairAttempted": False, "validationStatus": "VALID",
                "workerVersion": worker_version()}

    def handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        p = ctx.payload
        bucket, key = generated_bucket(), tts_key(p)
        meta_key = key + ".meta.json"
        if ctx.storage.exists(bucket, key) and ctx.storage.exists(bucket, meta_key):
            meta = json.loads(ctx.storage.get_bytes(bucket, meta_key))
            audio = ctx.storage.get_bytes(bucket, key)
            self._hits.inc()
            extras = {**meta["extras"], "cacheHit": True}
            return HandlerResult(output=object_ref(bucket, key, audio, "audio/mpeg"),
                                 run=self._run(extras["provider"], extras["voiceId"], t0), extras=extras)

        check_text(p["text"])
        if len(p["text"]) > MAX_CHARS:
            raise ProcessingError("INPUT_TOO_LARGE", "Synthesis text exceeds 600 characters.")
        speech_text = normalise_for_speech(p["text"])

        attempts = 0
        for prov in self.providers:
            br = self.breakers[prov.name]
            if not br.allow():
                self._req.labels(prov.name, "circuit_open").inc()
                continue
            attempts += 1
            t1 = time.monotonic()
            try:
                audio = prov.synthesize(speech_text)
                ms = validate_audio(audio)
            except ProviderFailed as e:
                br.failure()
                self._req.labels(prov.name, "failure").inc()
                log.warning("tts_provider_failed", provider=prov.name, reason=str(e), job_id=ctx.job_id)
                continue
            br.success()
            self._lat.labels(prov.name).observe(time.monotonic() - t1)
            self._req.labels(prov.name, "success").inc()
            extras = {"durationMs": ms, "provider": prov.name, "voiceId": prov.voice_id, "cacheHit": False}
            contracts.validate_artefact("tts_audio.v1", extras)
            ref = ctx.storage.put_bytes(bucket, key, audio, "audio/mpeg")
            ctx.storage.put_bytes(bucket, meta_key, json.dumps({"extras": extras}).encode(), "application/json")
            run = self._run(prov.name, prov.voice_id, t0)
            run["providerAttempts"] = attempts
            return HandlerResult(output=ref, run=run, extras=extras)
        raise ProcessingError("PROVIDER_UNAVAILABLE", "All speech synthesis providers failed.")


def build_providers(primary: str, secondary: str | None) -> list:
    table = {"azure": AzureTts, "polly": PollyTts}
    names = [primary] + ([secondary] if secondary and secondary != primary else [])
    unknown = [n for n in names if n not in table]
    if unknown:
        raise SystemExit(f"unknown TTS provider(s): {unknown}")
    return [table[n]() for n in names]
