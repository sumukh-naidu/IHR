import hashlib
import io
import json
from pathlib import Path

import numpy as np

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage
from speech.stt_mock import MockTranscriptionHandler
from speech.tts_mock import MockSynthesisHandler

FIX = Path(__file__).resolve().parents[3] / "contracts/fixtures/speech"


def opus_audio(seconds: float) -> bytes:
    import av

    buf = io.BytesIO()
    c = av.open(buf, "w", format="webm")
    s = c.add_stream("libopus", rate=48000)
    s.layout = "mono"
    pcm = (0.1 * np.sin(np.arange(int(48000 * seconds)) / 10)).astype(np.float32).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(pcm, format="flt", layout="mono")
    frame.sample_rate = 48000
    for p in s.encode(frame):
        c.mux(p)
    for p in s.encode(None):
        c.mux(p)
    c.close()
    return buf.getvalue()


def transcribe(audio: bytes):
    env = json.loads((FIX / "transcription/request.json").read_text())
    st = InMemoryStorage()
    a = env["payload"]["audio"]
    st.put_bytes(a["bucket"], a["key"], audio, "audio/webm")
    a.update(sha256=hashlib.sha256(audio).hexdigest(), sizeBytes=len(audio))
    proc = Processor(MockTranscriptionHandler(), st, "ihr-speech-service", "t_speech")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    return st, json.loads(out.value)["payload"], out


def test_stt_mock_one_segment_per_5s():
    st, p, out = transcribe(opus_audio(12))
    assert out.topic == "ihr.speech.transcription.completed"
    t = json.loads(st.get_bytes(p["output"]["bucket"], p["output"]["key"]))
    contracts.validate_artefact("transcript.v1", t)
    assert len(t["segments"]) == 3 and t["segments"][0]["text"] == "This is a mock answer for testing."
    assert p["speechDetected"] and p["segmentCount"] == 3 and p["avgConfidence"] == 0.9


def test_stt_mock_tiny_file_is_no_speech():
    _, p, _ = transcribe(b"x" * 500)
    assert p["speechDetected"] is False and p["qualityFlags"] == ["NO_SPEECH"]


def test_stt_checksum_mismatch():
    env = json.loads((FIX / "transcription/request.json").read_text())
    st = InMemoryStorage()
    a = env["payload"]["audio"]
    st.put_bytes(a["bucket"], a["key"], b"different", "audio/webm")
    proc = Processor(MockTranscriptionHandler(), st, "ihr-speech-service", "t_speech")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    assert json.loads(out.value)["payload"]["errorCode"] == "INPUT_CHECKSUM_MISMATCH"


def synth(env, st):
    proc = Processor(MockSynthesisHandler(), st, "ihr-speech-service", "t_speech")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    return out, json.loads(out.value)["payload"]


def test_tts_mock_duration_and_cache_hit():
    env = json.loads((FIX / "synthesis/request.json").read_text())
    st = InMemoryStorage()
    _, p1 = synth(env, st)
    words = len(env["payload"]["text"].split())
    assert p1["durationMs"] == 350 * words and p1["cacheHit"] is False
    assert p1["output"]["key"] == f"tts/en-default/{env['payload']['cacheKey']}.mp3"
    env["eventId"] = "0192f000-0000-7000-8000-000000088888"
    _, p2 = synth(env, st)
    assert p2["cacheHit"] is True and p2["output"] == p1["output"]


def test_tts_rejects_placeholders():
    env = json.loads((FIX / "synthesis/request.json").read_text())
    env["payload"]["text"] = "Hello [CANDIDATE], let us begin."
    out, p = synth(env, InMemoryStorage())
    assert out.topic.endswith(".failed") and p["errorCode"] == "OUTPUT_POLICY_VIOLATION"
