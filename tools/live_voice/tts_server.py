"""Qwen3-TTS-12Hz-0.6B-Base as a tiny local HTTP service (dev/test only; not part of any image).

Runs in its own venv with qwen-tts:   <qwentts-venv>/bin/python tools/live_voice/tts_server.py
POST /synthesize   {"text": "..."}   ->   raw 16-bit mono PCM; sample rate in the X-Sample-Rate header
GET  /health

The Base model clones a voice from a short reference clip. TTS_REF_WAV / TTS_REF_TEXT select it; by default a
synthetic Indian-English macOS voice ("Rishi") is generated with `say`. For anything beyond testing, use only a
recording you have consent and rights for.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel

MODEL = os.environ.get("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
# Default CPU: on a 24 GB Mac the local LLM (llama-server, Metal) holds nearly all GPU memory and MPS runs out.
# Use TTS_DEVICE=mps when the LLM is remote (Kimi) or the Mac has room (about 3x faster).
DEVICE = os.environ.get("TTS_DEVICE", "cpu")
PORT = int(os.environ.get("TTS_PORT", "8092"))
REF_TEXT = os.environ.get("TTS_REF_TEXT", "Hello, thank you for joining this interview today. Let us begin.")
REF = Path(os.environ.get("TTS_REF_WAV", str(Path.home() / ".cache/ihr-live-voice/ref_rishi.wav")))
if not REF.exists():
    REF.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["say", "-v", "Rishi", "-o", str(REF), "--data-format=LEI16@16000", REF_TEXT], check=True)

# fp32 on CPU (bf16 halves memory but PyTorch bf16 on a Mac CPU is ~10x slower); TTS_DTYPE=bf16 to try it.
DTYPE = torch.bfloat16 if os.environ.get("TTS_DTYPE", "fp32") == "bf16" else torch.float32
torch.set_num_threads(int(os.environ.get("TTS_THREADS", "4")))     # leave CPU cores for STT and the LLM
model = Qwen3TTSModel.from_pretrained(MODEL, device_map=DEVICE, attn_implementation="sdpa", dtype=DTYPE)
prompt = model.create_voice_clone_prompt(ref_audio=str(REF), ref_text=REF_TEXT)
lock = threading.Lock()
CACHE: "dict[str, tuple[bytes, int]]" = {}      # fixed lines (greeting, planned questions) are synthesised once


def synth(text: str) -> tuple[bytes, int]:
    if text in CACHE:
        return CACHE[text]
    with lock:
        if text in CACHE:
            return CACHE[text]
        wavs, sr = model.generate_voice_clone(text=text, language="English", voice_clone_prompt=prompt)
    x = np.clip(np.asarray(wavs[0], dtype=np.float32), -1, 1)
    out = (x * 32767).astype(np.int16).tobytes(), int(sr)
    if len(CACHE) < 500:
        CACHE[text] = out
    return out


class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        b = json.dumps({"ok": True, "model": MODEL, "device": DEVICE, "dtype": str(DTYPE), "cached": len(CACHE)}).encode()
        self.send_response(200); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        text = (body.get("text") or "").strip()
        if self.path != "/synthesize" or not text:
            self.send_response(400); self.end_headers(); return
        t0 = time.monotonic()
        try:
            pcm, sr = synth(text[:1000])
        except Exception as e:  # noqa: BLE001
            b = json.dumps({"error": str(e)}).encode()
            self.send_response(500); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
            return
        self.send_response(200); self.send_header("content-type", "audio/L16"); self.send_header("X-Sample-Rate", str(sr))
        self.send_header("X-Seconds", f"{time.monotonic() - t0:.2f}"); self.send_header("content-length", str(len(pcm)))
        self.end_headers(); self.wfile.write(pcm)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    synth("Warm up.")
    print(f"Qwen3-TTS ready on http://127.0.0.1:{PORT}  ({DEVICE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
