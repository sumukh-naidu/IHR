"""ProcessingErrorCode (Appendix A.3)."""
from __future__ import annotations

ERROR_TABLE: dict[str, tuple[str, bool]] = {
    "DEADLINE_EXCEEDED": ("TRANSIENT", True),
    "PROVIDER_TIMEOUT": ("TRANSIENT", True),
    "PROVIDER_RATE_LIMITED": ("TRANSIENT", True),
    "PROVIDER_UNAVAILABLE": ("TRANSIENT", True),
    "STORAGE_UNAVAILABLE": ("TRANSIENT", True),
    "INPUT_NOT_FOUND": ("PERMANENT", False),
    "INPUT_CHECKSUM_MISMATCH": ("PERMANENT", False),
    "INPUT_UNSUPPORTED_FORMAT": ("PERMANENT", False),
    "INPUT_TOO_LARGE": ("PERMANENT", False),
    "INPUT_MALWARE_DETECTED": ("PERMANENT", False),
    "INPUT_NO_TEXT": ("PERMANENT", False),
    "OUTPUT_SCHEMA_INVALID": ("PERMANENT", False),
    "OUTPUT_SEMANTIC_INVALID": ("PERMANENT", False),
    "OUTPUT_POLICY_VIOLATION": ("PERMANENT", False),
    "MEDIA_CHUNKS_MISSING": ("PERMANENT", False),
    "MEDIA_CORRUPT": ("PERMANENT", False),
    "UNSUPPORTED_SCHEMA_VERSION": ("PERMANENT", False),
    "INTERNAL_ERROR": ("TRANSIENT", True),
}

MAX_MESSAGE_LEN = 300


class ProcessingError(Exception):
    """Raised by handlers; mapped to a `.failed` event (§3.2 step 8).

    `message` goes into the event and must never contain PII, transcript/CV text,
    tokens or URLs (A.3). Keep it a fixed, generic sentence.
    """

    def __init__(self, code: str, message: str = "", run: dict | None = None):
        if code not in ERROR_TABLE:
            raise ValueError(f"unknown ProcessingErrorCode {code}")
        super().__init__(code)
        self.code = code
        self.message = (message or code)[:MAX_MESSAGE_LEN]
        self.run = run

    @property
    def error_class(self) -> str:
        return ERROR_TABLE[self.code][0]

    @property
    def retryable(self) -> bool:
        return ERROR_TABLE[self.code][1]


def failed_payload(job_id: str, attempt: int, err: ProcessingError) -> dict:
    return {
        "jobId": job_id,
        "attempt": attempt,
        "errorCode": err.code,
        "errorClass": err.error_class,
        "retryable": err.retryable,
        "message": err.message,
        "run": err.run,
    }
