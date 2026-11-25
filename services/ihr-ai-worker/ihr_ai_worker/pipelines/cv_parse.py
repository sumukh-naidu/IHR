"""CV_PARSE (AI Specification v1.1 §7.1): §6.1 extraction -> redaction -> Kimi -> validation ->
deterministic enrichment -> write profile and text objects -> .completed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import time
from datetime import datetime
from typing import Callable

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now, worker_version
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified

from .. import debug_capture
from ..enrich.experience import experience, month_index
from ..enrich.skills import canonicalise
from ..evidence import quote_found
from ..extraction import extract as ex
from ..extraction.clamav import ClamAvScanner
from ..extraction.normalise import non_english_ratio, normalise_pages
from ..extraction.sniff import Encrypted, sniff
from ..llm.base import TASK_PARAMS, ContextLengthExceeded, LlmClient
from ..llm.structured import Validation, call_structured
from ..privacy import injection
from ..privacy.identity import extract_identity
from ..privacy.redaction import redact
from ..prompts import PromptRegistry
from . import metrics
from .common import capture_failure
from ..tasks import TASKS, cv_text_key, generated_bucket

log = get_logger("ihr.cv_parse")

SCHEMA = "cv_profile.v1"
MIN_TEXT_CHARS = 200            # §6.1 step 6
MAX_EXTRACTED_CHARS = 60_000    # A-16
MAX_MODEL_CHARS = 40_000        # A-16
SKILL_DROP_LIMIT = 0.20         # §7.1: drop failing skills if <= 20 % fail, otherwise repair
NON_ENGLISH_LIMIT = 0.30        # §6.2
CONTEXT_SHRINK = 0.75           # §4.4: shorten input by 25 % once on context length


@dataclass
class Prepared:
    doc: object
    warnings: set
    identity: dict
    red: object
    text: str
    truncated: bool


class CvParseHandler:
    task = "CV_PARSE"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, scanner: ClamAvScanner | None = None,
                 ocr: ex.OcrFn = ex.tesseract_ocr, ocr_max_pages: int = 10, soffice: str = "/usr/bin/soffice",
                 debug_capture_enabled: bool = True, clock: Callable[[], datetime] = utc_now) -> None:
        spec = TASKS["CV_PARSE"]
        self.spec = spec
        self.request_topic = spec.request_topic
        self.output_schema_version = spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(spec.prompt_id)
        self.scanner = scanner or ClamAvScanner()
        self.ocr = ocr
        self.ocr_max_pages = ocr_max_pages
        self.soffice = soffice
        self.capture = debug_capture_enabled
        self.clock = clock
        self._redactions = counter("ihr_ai_redactions_total", "Redactions by type", ("type",))
        self._warnings = counter("ihr_ai_extraction_warnings_total", "Extraction warnings", ("warning",))

    def output_location(self, payload: dict) -> tuple[str, str]:
        return generated_bucket(), self.spec.output_key(payload)

    # ---- §6.1 steps 1-7 -------------------------------------------------------------------------
    def _extract(self, payload: dict, data: bytes) -> tuple[ex.Extracted, set[str]]:
        warnings: set[str] = set()
        self.scanner.scan(data)
        try:
            kind = sniff(data)
            if kind != (payload.get("originalExtension") or "").lower().lstrip("."):
                warnings.add("EXTENSION_MISMATCH")
            if kind == "pdf":
                doc = ex.extract_pdf(data, self.ocr, self.ocr_max_pages)
            elif kind == "docx":
                doc = ex.extract_docx(data)
            else:
                doc = ex.extract_docx(ex.doc_to_docx(data, self.soffice))
        except Encrypted:
            # ENCRYPTED_DOCUMENT is reported via the error message; .failed carries no warnings (A.5).
            raise ProcessingError("INPUT_UNSUPPORTED_FORMAT", "The document is encrypted (ENCRYPTED_DOCUMENT).") from None
        return doc, warnings | doc.warnings

    def handle(self, ctx: JobContext) -> HandlerResult:
        try:
            return self._handle(ctx)
        except ProcessingError as e:
            capture_failure(self, ctx, e)
            raise

    def prepare_text(self, payload: dict, data: bytes) -> "Prepared":
        """§6.1 steps 2-7: scan, extract, normalise, identity (A-17), redaction (§5.2), caps (A-16), escaping.
        No model call (used by the handler and by the offline redaction gates)."""
        doc, warnings = self._extract(payload, data)

        pages = normalise_pages(doc.pages)
        full = "\n\n".join(p for p in pages if p)
        if len(full) > MAX_EXTRACTED_CHARS:
            full = full[:MAX_EXTRACTED_CHARS]
            warnings.add("TEXT_TRUNCATED")
        if len(full.strip()) < MIN_TEXT_CHARS:
            raise ProcessingError("INPUT_NO_TEXT", "Not enough text could be extracted from the document.")

        hints = payload.get("redactionHints") or {}
        identity = extract_identity(full, pages[0] if pages else "", hints.get("fullName"))
        if injection.detect(full) or (doc.hidden_text.strip() and len(doc.hidden_text.split()) >= 1
                                      and any(len(w) >= 3 for w in doc.hidden_text.split())):
            warnings.add(injection.WARNING)
        if non_english_ratio(full) > NON_ENGLISH_LIMIT:
            warnings.add("NON_ENGLISH_CONTENT")

        local_name = (identity.get("name") or {}).get("value")
        red = redact(full, names=(hints.get("fullName"), local_name), cv=True)
        text = red.text.strip()
        truncated = "TEXT_TRUNCATED" in warnings
        if len(text) > MAX_MODEL_CHARS:
            text = text[:MAX_MODEL_CHARS]
            warnings.add("TEXT_TRUNCATED")
            truncated = True
        text = injection.escape_tags(text)
        return Prepared(doc, warnings, identity, red, text, truncated)

    def _handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        payload = ctx.payload
        data = read_verified(ctx.storage, payload["document"])
        pre = self.prepare_text(payload, data)
        doc, warnings, identity, red, text, truncated = (pre.doc, pre.warnings, pre.identity, pre.red, pre.text,
                                                        pre.truncated)

        # ---- Kimi -----------------------------------------------------------------------------------
        current_month = self.clock().strftime("%Y-%m")
        try:
            result = self._call(ctx, text, doc, current_month)
        except ContextLengthExceeded:
            text = text[: int(len(text) * CONTEXT_SHRINK)]
            warnings.add("TEXT_TRUNCATED")
            truncated = True
            try:
                result = self._call(ctx, text, doc, current_month)
            except ContextLengthExceeded:
                raise ProcessingError("INPUT_TOO_LARGE", "The document text is too long for the model.") from None

        # ---- post-processing / enrichment -----------------------------------------------------------
        profile = copy.deepcopy(result.value)
        profile["skills"] = dedupe_skills([s for s in profile["skills"] if quote_found(s["quote"], text)])
        if self._title_inconsistent(profile):
            profile["currentTitle"] = None
            profile["currentEmployer"] = None
            warnings.add("DATES_AMBIGUOUS")
        total, recent, undated = experience(profile["employmentHistory"], current_month)
        if undated and "EMPLOYMENT_DATES" not in profile["missingOrAmbiguousFields"]:
            profile["missingOrAmbiguousFields"].append("EMPLOYMENT_DATES")
        if not profile["employmentHistory"]:
            warnings.add("NO_EMPLOYMENT_HISTORY")
        if not profile["skills"]:
            warnings.add("NO_SKILLS_FOUND")
        warnings |= set(profile["extractionWarnings"])

        ordered = sorted(warnings)
        if "PROMPT_INJECTION_SUSPECTED" in warnings:
            metrics.injection_flag(self.task)
        profile["identity"] = identity
        profile["derived"] = {
            "totalExperienceMonths": total,
            "skillsCanonical": canonicalise([s["name"] for s in profile["skills"]]),
            "mostRecentRoleMonths": recent,
        }
        profile["extraction"] = {
            "pageCount": doc.page_count, "ocrUsed": doc.ocr_pages > 0, "ocrPages": doc.ocr_pages,
            "textChars": len(text), "truncated": truncated, "redactionCounts": red.counts, "warnings": ordered,
        }
        contracts.validate_artefact(SCHEMA, profile)
        for k, n in red.counts.items():
            if n:
                self._redactions.labels(k).inc(n)
        for w in ordered:
            self._warnings.labels(w).inc()

        bucket = generated_bucket()
        text_ref = ctx.storage.put_bytes(bucket, cv_text_key(payload), text.encode("utf-8"),
                                         "text/plain; charset=utf-8")
        profile_ref = put_json(ctx.storage, bucket, self.spec.output_key(payload), profile)
        debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                              prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                              calls=[{"messages": m, "response": r.content, "finishReason": r.finish_reason}
                                     for m, r in zip(result.requests, result.results)])
        run = {
            "provider": self.llm.provider_name,
            "model": result.results[-1].model or self.llm.model_for(TASK_PARAMS[self.task].model_slot),
            "promptId": self.prompt.prompt_id,
            "promptVersion": self.prompt.version,
            "promptSha256": self.prompt.sha256,
            "providerRequestId": result.results[-1].provider_request_id,
            "latencyMs": int((time.monotonic() - t0) * 1000),
            "inputTokens": result.input_tokens,
            "outputTokens": result.output_tokens,
            "providerAttempts": result.provider_attempts,
            "repairAttempted": result.repaired,
            "validationStatus": "REPAIRED" if result.repaired else "VALID",
            "workerVersion": worker_version(),
        }
        extras = {"pageCount": doc.page_count, "ocrUsed": doc.ocr_pages > 0, "textChars": len(text),
                  "identity": identity, "warnings": ordered}
        log.info("cv_parse_done", job_id=ctx.job_id, pages=doc.page_count, ocr_pages=doc.ocr_pages,
                 skills=len(profile["skills"]), warnings=ordered, repaired=result.repaired)
        return HandlerResult(output={"cvProfile": profile_ref, "cvText": text_ref}, run=run, extras=extras)

    def _call(self, ctx: JobContext, text: str, doc: ex.Extracted, current_month: str):
        messages = [
            {"role": "system", "content": self.prompt.system()},
            {"role": "user", "content": self.prompt.user(pageCount=doc.page_count,
                                                         ocrUsed=str(doc.ocr_pages > 0).lower(),
                                                         currentMonth=current_month, redactedText=text)},
        ]
        return call_structured(self.llm, task=self.task, params=TASK_PARAMS[self.task], messages=messages,
                               deadline=ctx.deadline, validate=lambda v: validate_model_output(v, text))

    @staticmethod
    def _title_inconsistent(profile: dict) -> bool:
        if profile.get("currentTitle") is None and profile.get("currentEmployer") is None:
            return False
        present = [e for e in profile["employmentHistory"] if e.get("endDate") == "PRESENT"]
        if not present:
            return True
        norm = lambda s: " ".join((s or "").lower().split())  # noqa: E731
        title_ok = profile.get("currentTitle") is None or any(norm(e["title"]) == norm(profile["currentTitle"])
                                                               for e in present)
        emp_ok = profile.get("currentEmployer") is None or any(
            norm(e.get("employer")) == norm(profile["currentEmployer"]) for e in present)
        return not (title_ok and emp_ok)


def dedupe_skills(skills: list[dict]) -> list[dict]:
    """One entry per distinct skill name (§7.1 "every distinct skill ... with one quote"); first quote kept.
    Different spellings ("Kafka" / "Apache Kafka") stay separate here and are grouped in derived.skillsCanonical."""
    seen: set[str] = set()
    out = []
    for s in skills:
        key = " ".join(s["name"].lower().split())
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def validate_model_output(value: dict, text: str) -> Validation:
    """§7.1 validation: schema; dates valid with startDate <= endDate; skill quotes (A-23)."""
    errors = contracts.artefact_errors(SCHEMA, value, model_part=True)
    if errors:
        return Validation("schema", errors)
    sem: list[str] = []
    far_future = "9999-12"
    for i, e in enumerate(value["employmentHistory"]):
        s, end = e.get("startDate"), e.get("endDate")
        if s == "PRESENT":
            sem.append(f"/employmentHistory/{i}/startDate: PRESENT is only allowed for endDate")
            continue
        si, ei = month_index(s, far_future), month_index(end, far_future)
        if s is not None and si is None:
            sem.append(f"/employmentHistory/{i}/startDate: not a valid YYYY-MM date")
        if end is not None and ei is None:
            sem.append(f"/employmentHistory/{i}/endDate: not a valid YYYY-MM date or PRESENT")
        if si is not None and ei is not None and end != "PRESENT" and si > ei:
            sem.append(f"/employmentHistory/{i}: startDate is after endDate")
    skills = value["skills"]
    failing = [i for i, s in enumerate(skills) if not quote_found(s["quote"], text)]
    if skills and len(failing) / len(skills) > SKILL_DROP_LIMIT:
        sem.extend(f"/skills/{i}/quote: not found verbatim in <cv_text>" for i in failing)
    return Validation("semantic", sem) if sem else Validation.ok()
