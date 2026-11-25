"""ClamAV INSTREAM client (§6.1 step 2). CLAMAV_HOST / CLAMAV_PORT (§3.3)."""
from __future__ import annotations

import os
import socket
import struct

from ihr_worker_common.errors import ProcessingError

CHUNK = 64 * 1024


class ClamAvScanner:
    def __init__(self, host: str | None = None, port: int | None = None, timeout_s: float = 30.0) -> None:
        self.host = host or os.environ.get("CLAMAV_HOST", "ihr-clamav")
        self.port = int(port or os.environ.get("CLAMAV_PORT", "3310"))
        self.timeout_s = timeout_s

    def scan(self, data: bytes) -> None:
        """Raise INPUT_MALWARE_DETECTED if infected; INTERNAL_ERROR (retryable) if the scanner is unavailable."""
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as s:
                s.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), CHUNK):
                    chunk = data[i:i + CHUNK]
                    s.sendall(struct.pack("!L", len(chunk)) + chunk)
                s.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = s.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError:
            raise ProcessingError("INTERNAL_ERROR", "Malware scanner is unavailable.") from None
        text = reply.rstrip(b"\0").decode("utf-8", "replace")
        if text.endswith("FOUND"):
            raise ProcessingError("INPUT_MALWARE_DETECTED", "The document failed the malware scan.")
        if not text.endswith("OK"):
            raise ProcessingError("INTERNAL_ERROR", "Malware scanner returned an error.")
