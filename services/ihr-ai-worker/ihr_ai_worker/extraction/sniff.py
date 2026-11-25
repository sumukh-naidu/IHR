"""Type sniffing by magic bytes (§6.1 step 3)."""
from __future__ import annotations

import io
import zipfile

from ihr_worker_common.errors import ProcessingError

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXTENSIONS = {"pdf": ("pdf",), "docx": ("docx",), "doc": ("doc",)}


class Encrypted(Exception):
    """Encrypted PDF/DOCX -> INPUT_UNSUPPORTED_FORMAT with warning ENCRYPTED_DOCUMENT."""


def _utf16(name: str) -> bytes:
    return name.encode("utf-16-le")


def sniff(data: bytes) -> str:
    """Return pdf | docx | doc, raise Encrypted or ProcessingError(INPUT_UNSUPPORTED_FORMAT)."""
    if data[:1024].lstrip().startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
    elif data.startswith(OLE2):
        if _utf16("EncryptedPackage") in data:   # encrypted OOXML is wrapped in OLE2
            raise Encrypted()
        if _utf16("WordDocument") in data:
            return "doc"
    raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "Document format is not supported.")
