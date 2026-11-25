"""STT benchmark (§11.1 performance targets; Sprint 3).

Runs the real STT handler path (decode -> faster-whisper -> post-processing) over a folder of audio
files and reports compute time, real-time factor and the time normalised to 180 s of audio.
Target on one NVIDIA L4, large-v3-turbo float16: p95 <= 4 s compute for 180 s of audio.

  python tools/stt_benchmark.py eval/datasets/audio-40 --model large-v3-turbo --device cuda --compute float16 --gate
  python tools/stt_benchmark.py <dir> --model small.en --device cpu --compute int8      # dev Mac
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "libs/py-worker-common/src"), str(ROOT / "services/ihr-speech-service")]

from speech.audio import decode_16k_mono  # noqa: E402
from speech.stt import WhisperConfig, WhisperEngine, initial_prompt, post_process, quality  # noqa: E402

AUDIO_EXT = {".webm", ".ogg", ".wav", ".mp3", ".m4a", ".aiff", ".flac"}
TARGET_S, TARGET_AUDIO_S = 4.0, 180.0


def pct(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio_dir")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--model-dir", default="/models/whisper")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute", default="float16")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--hints", default="")
    ap.add_argument("--gate", action="store_true", help="exit 1 if p95 (normalised to 180 s) > 4 s")
    a = ap.parse_args()
    files = sorted(p for p in Path(a.audio_dir).rglob("*") if p.suffix.lower() in AUDIO_EXT)
    if not files:
        print(f"no audio files in {a.audio_dir}")
        return 2
    eng = WhisperEngine(WhisperConfig(model=a.model, model_dir=a.model_dir, device=a.device,
                                      compute_type=a.compute, num_workers=a.workers))
    t = time.time()
    eng.start()
    print(f"model {a.model} on {a.device}/{a.compute}: load + warm-up {time.time() - t:.1f}s")
    prompt = initial_prompt([h for h in a.hints.split(",") if h])
    rows = []
    for f in files:
        samples, dur = decode_16k_mono(f.read_bytes())
        for _ in range(a.repeat):
            t = time.time()
            raw, _ = eng.transcribe(samples, prompt)
            segs, ratios = post_process(raw)
            flags, conf, speech = quality(segs, ratios, 0.0, False)
            el = time.time() - t
            rows.append({"file": f.name, "audioS": dur / 1000, "computeS": el, "rtf": el / max(dur / 1000, 0.1),
                         "per180S": el * TARGET_AUDIO_S / max(dur / 1000, 0.1), "conf": conf, "flags": flags})
    for r in rows:
        print(f"{r['file'][:40]:40} audio {r['audioS']:6.1f}s compute {r['computeS']:6.2f}s RTF {r['rtf']:.3f} "
              f"conf {r['conf']:.2f} {','.join(r['flags'])}")
    per180 = [r["per180S"] for r in rows]
    summary = {"files": len(files), "runs": len(rows), "p50ComputePer180s": round(statistics.median(per180), 2),
               "p95ComputePer180s": round(pct(per180, 0.95), 2),
               "meanRtf": round(statistics.mean(r["rtf"] for r in rows), 3), "targetP95": TARGET_S}
    print(json.dumps(summary, indent=1))
    if a.gate and summary["p95ComputePer180s"] > TARGET_S:
        print("GATE FAILED: p95 > 4 s per 180 s of audio (consider BatchedInferencePipeline, batch size 8)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
