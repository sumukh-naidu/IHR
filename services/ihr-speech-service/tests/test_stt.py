"""faster-whisper STT (§11.1, A-26, A-27) with a fake model."""
import io
import json
import wave
from types import SimpleNamespace as NS

import numpy as np
import pytest

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage
from speech import stt
from speech.stt import (TranscriptionHandler, WhisperConfig, WhisperEngine, initial_prompt, post_process, quality,
                        transcribe_params)


def seg(start, end, text, logprob=-0.1, nsp=0.01, cr=1.2, words=None):
    return NS(start=start, end=end, text=text, avg_logprob=logprob, no_speech_prob=nsp, compression_ratio=cr,
              words=words if words is not None else [NS(word=" " + w, start=start, end=end, probability=0.9)
                                                       for w in text.split()[:2]])


def wav(seconds, amp=0.2, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    x = (np.clip(amp * np.sin(2 * np.pi * 300 * t), -1, 1) * 32767).round().astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(x.tobytes())
    return buf.getvalue()


class FakeModel:
    def __init__(self, segments, fail=None):
        self.segments, self.fail, self.calls = segments, list(fail or []), []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if self.fail:
            raise self.fail.pop(0)
        return iter(self.segments), NS(language="en")


def test_initial_prompt_truncates_at_comma():
    assert initial_prompt(["Kafka", "Spring Boot"]) == "Interview answer. Terms: Kafka, Spring Boot"
    long = initial_prompt([f"term{i:03d}" for i in range(60)])
    assert len(long) <= 200 and not long.endswith(",") and long.split(", ")[-1].startswith("term")
    assert initial_prompt([]) == "Interview answer. Terms: "


def test_a26_params():
    p = transcribe_params("x")
    assert p["beam_size"] == 5 and p["vad_filter"] and p["temperature"] == [0.0, 0.2, 0.4]
    assert p["vad_parameters"] == {"min_silence_duration_ms": 500, "speech_pad_ms": 200}
    assert not p["condition_on_previous_text"] and p["word_timestamps"] and p["language"] == "en"


def test_post_process_drop_collapse_reindex():
    segs, ratios = post_process([
        seg(0.0, 2.0, "  We moved the batch to Kafka. "),
        seg(2.0, 3.0, "uh", logprob=-1.5, nsp=0.9),            # dropped: no_speech > 0.6 and logprob < -1
        seg(3.0, 4.0, "Thank you."), seg(4.0, 5.0, "Thank you."),  # collapsed
        seg(5.0, 5.5, "   "),                                      # empty after trim
    ])
    assert [s["text"] for s in segs] == ["We moved the batch to Kafka.", "Thank you."]
    assert [s["index"] for s in segs] == [0, 1] and segs[1]["endMs"] == 5000
    assert segs[0]["confidence"] == round(np.exp(-0.1), 4) and segs[0]["words"][0] == {"w": "We", "startMs": 0, "endMs": 2000, "p": 0.9}
    assert len(ratios) == 2


def test_quality_flags():
    good, r = post_process([seg(0, 5, "In my last project we tuned the consumers.")])
    assert quality(good, r, 0.0, False) == ([], round(float(np.exp(-0.1)), 4), True)
    short, r = post_process([seg(0, 1.0, "Yes.")])
    assert quality(short, r, 0.0, False)[0] == ["NO_SPEECH"] and quality(short, r, 0, False)[2] is False
    assert quality([], [], 0.0, False)[0] == ["NO_SPEECH"]
    low, r = post_process([seg(0, 5, "Something hard to hear here.", logprob=-0.9)])
    assert "LOW_CONFIDENCE" in quality(low, r, 0.0, False)[0]
    rep, r = post_process([seg(0, 9, "the cat sat down " * 3)])
    assert "POSSIBLE_HALLUCINATION" in quality(rep, r, 0.0, False)[0]
    comp, r = post_process([seg(0, 5, "Fine words here.", cr=2.6)])
    assert "POSSIBLE_HALLUCINATION" in quality(comp, r, 0.0, False)[0]
    flags = quality(good, [1.0], 0.02, True)[0]
    assert "CLIPPED_AUDIO" in flags and "TRUNCATED_AT_LIMIT" in flags


def engine(model, **cfg):
    e = WhisperEngine(WhisperConfig(model="small.en", device="cpu", compute_type="int8", **cfg), loader=lambda: model)
    e.model = model
    e.ready = True
    return e


def run(audio, model, max_s=400, hints=("Kafka",)):
    storage = InMemoryStorage()
    env = json.loads(open(contracts.contracts_dir() / "fixtures/speech/transcription/request.json").read())
    p = env["payload"]
    p["audio"] = storage.put_bytes(p["audio"]["bucket"], p["audio"]["key"], audio, "audio/wav")
    p["maxDurationSeconds"], p["vocabularyHints"] = max_s, list(hints)
    proc = Processor(TranscriptionHandler(engine(model) if not isinstance(model, WhisperEngine) else model),
                     storage, "ihr-speech-service", "t_stt")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    ev = json.loads(out.value)
    contracts.validate_payload(out.topic, ev["payload"])
    tr = None
    if out.topic.endswith("completed"):
        ref = ev["payload"]["output"]
        tr = json.loads(storage.get_bytes(ref["bucket"], ref["key"]))
    return out, ev["payload"], tr


def test_handler_completes():
    model = FakeModel([seg(0.4, 6.2, "In my last project we moved the settlement batch to Kafka.")])
    out, p, tr = run(wav(7), model)
    assert out.topic == "ihr.speech.transcription.completed"
    assert tr["speechDetected"] and tr["segments"][0]["text"].startswith("In my last project")
    assert p["run"]["provider"] == "faster-whisper" and p["run"]["model"] == "small.en"
    assert model.calls[0]["initial_prompt"] == "Interview answer. Terms: Kafka"
    assert 6900 <= tr["durationMs"] <= 7100 and p["segmentCount"] == 1


def test_truncated_at_limit():
    out, p, tr = run(wav(5), FakeModel([seg(0, 2, "Hello there, this is fine.")]), max_s=3)
    assert tr["durationMs"] == 3000 and "TRUNCATED_AT_LIMIT" in tr["qualityFlags"]


def test_clipped_audio_flag():
    out, p, tr = run(wav(4, amp=1.2), FakeModel([seg(0, 3, "Loud answer about Kafka.")]))
    assert "CLIPPED_AUDIO" in tr["qualityFlags"]


def test_undecodable_audio():
    out, p, _ = run(b"not audio at all" * 100, FakeModel([]))
    assert p["errorCode"] == "INPUT_UNSUPPORTED_FORMAT"


def test_oom_reload_once_then_ok():
    oom = RuntimeError("CUDA failed with error out of memory")
    model = FakeModel([seg(0, 3, "Recovered after reload.")], fail=[oom])
    e = WhisperEngine(WhisperConfig(device="cpu"), loader=lambda: model)
    e.model, e.ready = model, True
    out, p, tr = run(wav(4), e)
    assert out.topic.endswith("completed") and e.alive


def test_oom_twice_fails_liveness():
    oom = RuntimeError("CUDA failed with error out of memory")
    model = FakeModel([], fail=[oom, oom])
    e = WhisperEngine(WhisperConfig(device="cpu"), loader=lambda: model)
    e.model, e.ready = model, True
    out, p, _ = run(wav(4), e)
    assert p["errorCode"] == "INTERNAL_ERROR" and p["retryable"] and e.alive is False


def test_warmup_sets_ready():
    model = FakeModel([])
    e = WhisperEngine(WhisperConfig(device="cpu"), loader=lambda: model)
    assert not e.ready
    e.start()
    assert e.ready and len(model.calls) == 1
