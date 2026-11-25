"""Audio helpers using PyAV (A-26: decode to 16 kHz mono)."""
from __future__ import annotations

import io

from ihr_worker_common.errors import ProcessingError


def duration_ms(data: bytes) -> int:
    """Decoded duration in ms; no audio stream / undecodable -> INPUT_UNSUPPORTED_FORMAT."""
    import av

    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "No audio stream found.")
            stream = container.streams.audio[0]
            samples = 0
            rate = None
            for frame in container.decode(stream):
                samples += frame.samples
                rate = frame.sample_rate
            if not rate:
                raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "Audio contains no decodable frames.")
            return int(samples * 1000 / rate)
    except ProcessingError:
        raise
    except Exception:
        raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "Audio could not be decoded.") from None


TARGET_RATE = 16_000
FULL_SCALE = 0.999


def _full_scale_count(frame) -> tuple[int, int]:
    """Samples at full scale in the original (pre-resample) frame: (clipped, total)."""
    import numpy as np
    arr = frame.to_ndarray()
    if np.issubdtype(arr.dtype, np.integer):
        info = np.iinfo(arr.dtype)
        clipped = np.count_nonzero((arr >= info.max - 1) | (arr <= info.min + 1))
    else:
        clipped = np.count_nonzero(np.abs(arr) >= FULL_SCALE)
    return int(clipped), int(arr.size)


def decode_16k_mono(data: bytes, max_seconds: float | None = None, stats: dict | None = None):
    """PyAV decode -> 16 kHz mono float32 numpy array (A-26). Returns (samples, duration_ms).
    If `stats` is given, stats["clipped"] is the share of original samples at full scale (A-27 CLIPPED_AUDIO)."""
    import av
    import numpy as np

    try:
        with av.open(io.BytesIO(data)) as container:
            if not container.streams.audio:
                raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "No audio stream found.")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=TARGET_RATE)
            chunks = []
            clipped = total = 0
            for frame in container.decode(stream):
                c, n = _full_scale_count(frame)
                clipped += c
                total += n
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray().reshape(-1))
            for out in resampler.resample(None):
                chunks.append(out.to_ndarray().reshape(-1))
    except ProcessingError:
        raise
    except Exception:
        raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "Audio could not be decoded.") from None
    if not chunks:
        raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "Audio contains no decodable frames.")
    samples = np.concatenate(chunks).astype("float32")
    if stats is not None:
        stats["clipped"] = clipped / total if total else 0.0
    return samples, int(len(samples) * 1000 / TARGET_RATE)


def clipping_ratio(samples) -> float:
    import numpy as np
    return float(np.mean(np.abs(samples) >= FULL_SCALE)) if len(samples) else 0.0
