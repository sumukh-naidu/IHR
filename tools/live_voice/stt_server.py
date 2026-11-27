"""Nemotron 3.5 ASR streaming 0.6B as a tiny local HTTP service (dev/test only; not part of any image).

Runs in its own venv with NVIDIA NeMo:   <nemo-venv>/bin/python tools/live_voice/stt_server.py
POST /transcribe   body = raw 16-bit mono PCM at 16 kHz   ->   {"text": ..., "seconds": ..., "audioSeconds": ...}
GET  /health

Cache-aware model: transcribing a segment with att_context_size [56, R] gives the same text as streaming it in
(R+1)*80 ms chunks, so the latency setting is chosen with STT_CHUNK_MS (80, 320, 560 or 1120; default 320).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.disable(logging.WARNING)
import torch  # noqa: E402
import nemo.collections.asr as nemo_asr  # noqa: E402
from nemo.collections.asr.data import audio_to_text_lhotse_prompt_index as _pi  # noqa: E402

MODEL = os.environ.get("STT_MODEL", "nvidia/nemotron-3.5-asr-streaming-0.6b")
LANG = os.environ.get("STT_LANG", "en-US")          # en-IN is not a supported locale; en-US / en-GB are
CHUNK_MS = int(os.environ.get("STT_CHUNK_MS", "320"))
PORT = int(os.environ.get("STT_PORT", "8091"))
_RIGHT = {80: 0, 160: 1, 320: 3, 560: 6, 1120: 13}

# Plain file paths carry no language, so NeMo's prompt dataset would pick a random/None prompt: force LANG.
_pi.LhotseSpeechToTextBpeDatasetWithPromptIndex._get_prompt_index_for_cut = lambda self, cut: self.prompt_dict[LANG]
torch.set_num_threads(int(os.environ.get("STT_THREADS", "6")))
model = nemo_asr.models.ASRModel.from_pretrained(model_name=MODEL).eval()
model.encoder.set_default_att_context_size([56, _RIGHT[CHUNK_MS]])
lock = threading.Lock()
TAG = re.compile(r"<[^>]*>")


def transcribe(pcm: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
        with lock, torch.inference_mode():
            out = model.transcribe([f.name], batch_size=1, verbose=False)
    return TAG.sub("", out[0].text if hasattr(out[0], "text") else str(out[0])).strip()


class H(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        self._json(200, {"ok": True, "model": MODEL, "chunkMs": CHUNK_MS, "lang": LANG})

    def do_POST(self):  # noqa: N802
        if self.path != "/transcribe":
            return self._json(404, {"error": "not found"})
        pcm = self.rfile.read(int(self.headers.get("content-length") or 0))
        t0 = time.monotonic()
        try:
            text = transcribe(pcm) if len(pcm) >= 3200 else ""
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": str(e)})
        self._json(200, {"text": text, "seconds": round(time.monotonic() - t0, 3), "audioSeconds": len(pcm) / 32000})

    def log_message(self, *a):  # quiet
        pass


if __name__ == "__main__":
    transcribe(b"\x00\x00" * 16000)   # warm-up
    print(f"Nemotron STT ready on http://127.0.0.1:{PORT}  (chunk {CHUNK_MS} ms, {LANG})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
