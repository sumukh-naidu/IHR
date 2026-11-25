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
