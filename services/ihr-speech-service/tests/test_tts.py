"""TTS (§11.2, A-28): Azure primary, Polly failover, breakers, cache, lexicon, guard, audio validation."""
import io
import json

import httpx
import pytest
import respx

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage
from speech.audio import duration_ms
from speech.tts import (AzureTts, Breaker, PollyTts, SynthesisHandler, azure_ssml, normalise_for_speech,
                        polly_ssml)
from speech.tts_mock import TONE

MP3 = TONE.read_bytes()
AZURE_URL = "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1"


class FakePolly:
    def __init__(self, fail=False, audio=MP3):
        self.fail, self.audio, self.calls = fail, audio, []

    def synthesize_speech(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("EndpointConnectionError")
        return {"AudioStream": io.BytesIO(self.audio)}


def handler(polly=None):
    az = AzureTts(key="k", region="centralindia", voice="en-IN-NeerjaNeural", client=httpx.Client())
    return SynthesisHandler([az, PollyTts(voice="Kajal", client=polly or FakePolly())])


def run(h, text="Tell me about a project you worked on that is relevant to this role.", storage=None):
    storage = storage or InMemoryStorage()
    env = json.loads(open(contracts.contracts_dir() / "fixtures/speech/synthesis/request.json").read())
    env["payload"]["text"] = text
    proc = Processor(h, storage, "ihr-speech-service", "t_tts")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    ev = json.loads(out.value)
    contracts.validate_payload(out.topic, ev["payload"])
    return out, ev["payload"], storage


def test_tone_is_valid_audio():
    assert duration_ms(MP3) >= 300


def test_normalise_lexicon_and_escape():
    assert normalise_for_speech("Have you used  K8s,\nC# & CI/CD?") == "Have you used Kubernetes, C sharp and C I C D?"
    assert normalise_for_speech("ASP.NET vs. .NET <tags>") == "A S P dot net versus dot net &lt;tags&gt;"
    assert normalise_for_speech("Use AWS and SQL") == "Use AWS and SQL"          # other acronyms unchanged


def test_ssml():
    assert azure_ssml("Hi", "en-IN-NeerjaNeural") == (
        '<speak version="1.0" xml:lang="en-IN"><voice name="en-IN-NeerjaNeural"><prosody rate="-5%">Hi</prosody>'
        '</voice></speak>')
    assert polly_ssml("Hi") == '<speak><prosody rate="95%">Hi</prosody></speak>'


@respx.mock
def test_azure_primary_success_and_cache_hit():
    route = respx.post(AZURE_URL).mock(return_value=httpx.Response(200, content=MP3))
    h = handler()
    out, p, storage = run(h, "Have you deployed services on K8s?")
    assert out.topic == "ihr.speech.synthesis.completed" and p["provider"] == "azure-speech"
    assert p["voiceId"] == "en-IN-NeerjaNeural" and p["cacheHit"] is False and p["durationMs"] >= 300
    req = route.calls[0].request
    assert req.headers["X-Microsoft-OutputFormat"] == "audio-24khz-48kbitrate-mono-mp3"
    assert b"Kubernetes" in req.content and b"K8s" not in req.content
    out2, p2, _ = run(h, "Have you deployed services on K8s?", storage=storage)
    assert p2["cacheHit"] is True and route.call_count == 1


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 503])
def test_failover_to_polly(status):
    respx.post(AZURE_URL).mock(return_value=httpx.Response(status))
    polly = FakePolly()
    out, p, _ = run(handler(polly))
    assert p["provider"] == "aws-polly" and p["voiceId"] == "Kajal" and p["run"]["providerAttempts"] == 2
    kw = polly.calls[0]
    assert kw["Engine"] == "neural" and kw["LanguageCode"] == "en-IN" and kw["SampleRate"] == "24000"
    assert kw["TextType"] == "ssml" and kw["Text"].startswith("<speak><prosody rate=\"95%\">")


@respx.mock
def test_timeout_fails_over():
    respx.post(AZURE_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    _, p, _ = run(handler())
    assert p["provider"] == "aws-polly"


@respx.mock
def test_invalid_audio_from_azure_fails_over():
    respx.post(AZURE_URL).mock(return_value=httpx.Response(200, content=b"not audio"))
    _, p, _ = run(handler())
    assert p["provider"] == "aws-polly"


@respx.mock
def test_both_fail_provider_unavailable():
    respx.post(AZURE_URL).mock(return_value=httpx.Response(503))
    out, p, _ = run(handler(FakePolly(fail=True)))
    assert out.topic.endswith(".failed") and p["errorCode"] == "PROVIDER_UNAVAILABLE" and p["retryable"]


@respx.mock
def test_open_circuit_skips_azure():
    route = respx.post(AZURE_URL).mock(return_value=httpx.Response(500))
    h = handler()
    for i in range(5):
        run(h, f"Question number {i} about Kafka consumers and partitions.")
    assert route.call_count == 5
    _, p, _ = run(h, "Another question about Kafka consumers and partitions.")
    assert route.call_count == 5 and p["provider"] == "aws-polly" and p["run"]["providerAttempts"] == 1


def test_breaker_half_open():
    now = [0.0]
    b = Breaker(threshold=2, open_s=60, clock=lambda: now[0])
    b.failure(); b.failure()
    assert not b.allow()
    now[0] = 61
    assert b.allow()
    b.failure()
    assert not b.allow()


@pytest.mark.parametrize("text", ["Hello [CANDIDATE], tell me about Kafka.", "Mail me at a.b@example.com please.",
                                  "Call +91 98765 43210 after the interview."])
def test_guard_rejects_pii(text):
    _, p, _ = run(handler(), text)
    assert p["errorCode"] == "OUTPUT_POLICY_VIOLATION"


def test_too_long_text():
    from types import SimpleNamespace
    from ihr_worker_common.errors import ProcessingError
    # > 600 chars is already rejected by the request schema (dead-lettered); the handler guards it too (A-28)
    ctx = SimpleNamespace(payload={"text": "word " * 130, "voiceProfileKey": "en-default", "cacheKey": "x" * 64},
                          storage=InMemoryStorage(), job_id="j")
    with pytest.raises(ProcessingError) as e:
        handler().handle(ctx)
    assert e.value.code == "INPUT_TOO_LARGE"
