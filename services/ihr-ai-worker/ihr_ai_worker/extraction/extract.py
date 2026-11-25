"""Document text extraction (A-15, A-16, §6.1 step 4).

PDF: pdfplumber; page with < 100 chars -> scanned -> pypdfium2 @300 dpi -> Tesseract 5 (eng, --oem 1 --psm 3).
DOCX: python-docx paragraphs, tables, headers/footers, text boxes.
DOC: LibreOffice headless -> DOCX in a separate process (60 s timeout), then the DOCX path.
"""
from __future__ import annotations

import io
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from ihr_worker_common.errors import ProcessingError

from .sniff import Encrypted

MAX_PAGES = 30
SCANNED_PAGE_CHARS = 100
OCR_DPI = 300
LOW_OCR_CONFIDENCE = 70
DOCX_CHARS_PER_PAGE = 3000     # page estimate when docProps/app.xml has no <Pages> (ASSUMPTIONS #20)
DOC_TIMEOUT_S = 60
HIDDEN_MIN_SIZE = 1.5          # pt


@dataclass
class Extracted:
    pages: list[str]
    page_count: int
    ocr_pages: int = 0
    warnings: set[str] = field(default_factory=set)
    hidden_text: str = ""


# OCR is injectable so tests (and environments without Tesseract) can stub it.
OcrFn = Callable[[object], tuple[str, float | None]]   # PIL image -> (text, mean word confidence)


def tesseract_ocr(image) -> tuple[str, float | None]:
    import pytesseract
    try:
        data = pytesseract.image_to_data(image, lang="eng", config="--oem 1 --psm 3",
                                         output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(image, lang="eng", config="--oem 1 --psm 3")
    except pytesseract.TesseractNotFoundError:
        raise ProcessingError("INTERNAL_ERROR", "OCR engine is not available.") from None
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    return text, (sum(confs) / len(confs) if confs else None)


def _is_white(color) -> bool:
    if color is None:
        return False
    vals = color if isinstance(color, (list, tuple)) else (color,)
    try:
        vals = [float(v) for v in vals]
    except (TypeError, ValueError):
        return False
    if len(vals) == 4:   # CMYK white
        return all(v == 0 for v in vals)
    return bool(vals) and all(v >= 0.98 for v in vals)


def _multi_column(page) -> bool:
    """x-position clustering of line starts: two clear columns of left edges."""
    words = page.extract_words() or []
    if len(words) < 40 or not page.width:
        return False
    lines: dict[int, float] = {}
    for w in words:
        key = round(w["top"] / 3)
        lines[key] = min(lines.get(key, w["x0"]), w["x0"])
    starts = list(lines.values())
    left = sum(1 for x in starts if x < page.width * 0.25)
    right = sum(1 for x in starts if x > page.width * 0.45)
    return len(starts) >= 10 and left >= 0.3 * len(starts) and right >= 0.3 * len(starts)


def extract_pdf(data: bytes, ocr: OcrFn, ocr_max_pages: int) -> Extracted:
    import pdfplumber

    if re.search(rb"/Encrypt\s", data):
        raise Encrypted()
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception:
        raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The PDF could not be read.") from None
    with pdf:
        n = len(pdf.pages)
        if n > MAX_PAGES:
            raise ProcessingError("INPUT_TOO_LARGE", "The document has more pages than allowed.")
        out = Extracted(pages=[], page_count=n)
        scanned: list[int] = []
        hidden: list[str] = []
        for i, page in enumerate(pdf.pages):
            text = page.extract_text(layout=False) or ""
            out.pages.append(text)
            if len(text.strip()) < SCANNED_PAGE_CHARS:
                scanned.append(i)
            hidden.append("".join(c["text"] for c in page.chars
                                  if _is_white(c.get("non_stroking_color")) or c.get("size", 99) <= HIDDEN_MIN_SIZE))
            if "MULTI_COLUMN_LAYOUT" not in out.warnings and _multi_column(page):
                out.warnings.add("MULTI_COLUMN_LAYOUT")
        out.hidden_text = " ".join(h for h in hidden if h.strip())
    if scanned:
        out.ocr_pages = _ocr_pages(data, scanned[:ocr_max_pages], out, ocr)
        if len(scanned) > ocr_max_pages:
            out.warnings.add("OCR_PARTIAL")
    return out


def _ocr_pages(data: bytes, indexes: list[int], out: Extracted, ocr: OcrFn) -> int:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    confs: list[float] = []
    try:
        for i in indexes:
            image = doc[i].render(scale=OCR_DPI / 72).to_pil()
            text, conf = ocr(image)
            out.pages[i] = text
            if conf is not None:
                confs.append(conf)
    finally:
        doc.close()
    if indexes:
        out.warnings.add("OCR_USED")
    if confs and sum(confs) / len(confs) < LOW_OCR_CONFIDENCE:
        out.warnings.add("LOW_OCR_CONFIDENCE")
    return len(indexes)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _para_text(p_el) -> str:
    return "".join(t.text or "" for t in p_el.iter(_W + "t"))


def extract_docx(data: bytes) -> Extracted:
    import zipfile

    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        d = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError):
        raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The DOCX file could not be read.") from None
    parts: list[str] = []
    seen_hf: set[str] = set()
    for section in d.sections:
        for hf in (section.header, section.first_page_header):
            t = "\n".join(p.text for p in hf.paragraphs if p.text.strip())
            if t and t not in seen_hf:
                seen_hf.add(t)
                parts.append(t)
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == _W + "p":
            parts.append(_para_text(child))
            for box in child.iter(_W + "txbxContent"):
                parts.extend(_para_text(p) for p in box.iter(_W + "p"))
        elif tag == _W + "tbl":
            for row in child.iter(_W + "tr"):
                cells = [" ".join(_para_text(p) for p in tc.iter(_W + "p")).strip() for tc in row.iter(_W + "tc")]
                parts.append(" | ".join(c for c in cells if c))
    for section in d.sections:
        for hf in (section.footer, section.first_page_footer):
            t = "\n".join(p.text for p in hf.paragraphs if p.text.strip())
            if t and t not in seen_hf:
                seen_hf.add(t)
                parts.append(t)
    text = "\n".join(parts)
    pages = _docx_page_count(data, text)
    if pages > MAX_PAGES:
        raise ProcessingError("INPUT_TOO_LARGE", "The document has more pages than allowed.")
    return Extracted(pages=[text], page_count=pages)


def _docx_page_count(data: bytes, text: str) -> int:
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            app = z.read("docProps/app.xml").decode("utf-8", "replace")
        m = re.search(r"<Pages>(\d+)</Pages>", app)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    except (KeyError, zipfile.BadZipFile):
        pass
    return max(1, math.ceil(len(text) / DOCX_CHARS_PER_PAGE))


def doc_to_docx(data: bytes, soffice: str) -> bytes:
    """LibreOffice headless in a separate process with its own profile; network isolation is enforced by the
    pod network policy (§3.3). 60 s timeout."""
    if not shutil.which(soffice) and not os.path.exists(soffice):
        raise ProcessingError("INTERNAL_ERROR", "DOC converter is not available.")
    with tempfile.TemporaryDirectory(prefix="ihr-doc-") as tmp:
        src = os.path.join(tmp, "input.doc")
        with open(src, "wb") as f:
            f.write(data)
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "LANG": "C.UTF-8"}
        cmd = [soffice, "--headless", "--norestore", "--nologo", "--nolockcheck",
               f"-env:UserInstallation=file://{tmp}/profile", "--convert-to", "docx", "--outdir", tmp, src]
        try:
            subprocess.run(cmd, env=env, cwd=tmp, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=DOC_TIMEOUT_S, check=True)
        except subprocess.TimeoutExpired:
            raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The DOC file could not be converted in time.") from None
        except (subprocess.CalledProcessError, OSError):
            raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The DOC file could not be converted.") from None
        out = os.path.join(tmp, "input.docx")
        if not os.path.exists(out):
            raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The DOC file could not be converted.")
        with open(out, "rb") as f:
            return f.read()
