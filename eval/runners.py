"""Runs the real worker code paths over the datasets (§13.2) and returns per-item results.

Provider: kimi (release gates count only on Kimi), local (llama-server stand-in) or mock.
Each run goes through Processor + the task handler exactly as in production, with InMemoryStorage.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs/py-worker-common/src", "services/ihr-ai-worker", "services/ihr-speech-service"):
    if str(ROOT / p) not in sys.path:
        sys.path.insert(0, str(ROOT / p))
os.environ.setdefault("IHR_CONTRACTS_DIR", str(ROOT / "contracts"))

from ihr_worker_common.processor import Processor  # noqa: E402
from ihr_worker_common.storage import InMemoryStorage  # noqa: E402

FIX = ROOT / "contracts" / "fixtures"


def _fixture(rel: str) -> dict:
    return json.loads((FIX / rel / "request.json").read_text())


class Skip:
    def scan(self, data: bytes) -> None:   # ClamAV is not part of the quality evaluation
        pass


class Harness:
    def __init__(self, provider: str = "mock", soffice: str | None = None) -> None:
        from ihr_ai_worker.prompts import load_registry
        self.provider = provider
        self.registry = load_registry(ROOT / "docs" / "prompts" / "active.yaml")
        self.soffice = soffice or os.environ.get("LIBREOFFICE_BIN", "/usr/bin/soffice")
        self.llm = self._llm(provider)

    @staticmethod
    def _llm(provider: str):
        from ihr_ai_worker.llm.kimi import KimiClient
        from ihr_ai_worker.llm.mock import MockLlmClient
        if provider == "mock":
            return MockLlmClient()
        if provider == "local":
            model = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6-35b-a3b")
            return KimiClient(base_url=os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8081/v1"),
                              api_key="local", model_standard=model, model_realtime=model, max_concurrency=1,
                              rpm_limit=600, provider_name="local")
        return KimiClient(base_url=os.environ.get("KIMI_API_BASE_URL", "https://api.moonshot.ai/v1"),
                          api_key=os.environ["KIMI_API_KEY"],
                          model_standard=os.environ.get("KIMI_MODEL_STANDARD", "kimi-k2-0905-preview"),
                          model_realtime=os.environ.get("KIMI_MODEL_REALTIME", "kimi-k2-turbo-preview"),
                          max_concurrency=4, rpm_limit=int(os.environ.get("KIMI_RPM_LIMIT", "60")))

    # ---- helpers ------------------------------------------------------------------------------------
    @staticmethod
    def _process(handler, storage, env) -> tuple[str, dict, float]:
        env["eventId"] = str(uuid.uuid4())
        env["payload"]["jobId"] = str(uuid.uuid4())
        proc = Processor(handler, storage, "eval", "eval")
        t0 = time.monotonic()
        try:
            [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
        finally:
            proc.shutdown()
        return out.topic, json.loads(out.value)["payload"], time.monotonic() - t0

    @staticmethod
    def _get(storage, ref) -> dict:
        return json.loads(storage.get_bytes(ref["bucket"], ref["key"]))

    @staticmethod
    def _status(topic: str, payload: dict) -> dict:
        ok = topic.endswith(".completed")
        run = payload.get("run") or {}
        return {"ok": ok, "error": None if ok else payload.get("errorCode"),
                "firstPass": ok and not run.get("repairAttempted", False),
                "repaired": bool(run.get("repairAttempted"))}

    # ---- CV ----------------------------------------------------------------------------------------
    def cv_handler(self, current_month: str | None):
        from ihr_ai_worker.pipelines.cv_parse import CvParseHandler
        clock = (lambda: datetime.strptime(current_month + "-15", "%Y-%m-%d").replace(tzinfo=timezone.utc)) \
            if current_month else None
        kw = {"clock": clock} if clock else {}
        return CvParseHandler(self.llm, self.registry, scanner=Skip(), soffice=self.soffice,
                              debug_capture_enabled=False, **kw)

    def _cv_env(self, storage, data: bytes, ext: str, known_name: str | None) -> dict:
        env = _fixture("ai/cv_parse")
        p = env["payload"]
        p["document"] = storage.put_bytes(p["document"]["bucket"], p["document"]["key"] + uuid.uuid4().hex, data,
                                          "application/octet-stream")
        p["originalExtension"] = ext
        p["redactionHints"] = {"fullName": known_name, "email": None, "phone": None}
        return env

    def redact_cv(self, cv: dict) -> str:
        """Offline: the text that would be sent to the model (no provider call)."""
        data = cv["document"].read_bytes()
        handler = self.cv_handler(cv["gold"].get("currentMonth"))
        payload = {"originalExtension": cv["document"].suffix.lstrip("."),
                   "redactionHints": {"fullName": cv["gold"].get("knownName"), "email": None, "phone": None}}
        return handler.prepare_text(payload, data).text

    def extracted_text(self, cv: dict) -> str:
        """Normalised extracted text before identity extraction and redaction (bias variants start here)."""
        from ihr_ai_worker.extraction.normalise import normalise_pages
        handler = self.cv_handler(cv["gold"].get("currentMonth"))
        doc, _ = handler._extract({"originalExtension": cv["document"].suffix.lstrip(".")}, cv["document"].read_bytes())
        return "\n\n".join(p for p in normalise_pages(doc.pages) if p)

    def parse_cv(self, cv: dict, data: bytes | None = None) -> dict:
        storage = InMemoryStorage()
        data = data if data is not None else cv["document"].read_bytes()
        ext = "docx" if data[:2] == b"PK" else cv["document"].suffix.lstrip(".")
        env = self._cv_env(storage, data, ext, cv["gold"].get("knownName"))
        topic, pay, secs = self._process(self.cv_handler(cv["gold"].get("currentMonth")), storage, env)
        res = {**self._status(topic, pay), "seconds": secs, "storage": storage}
        if res["ok"]:
            res["profile"] = self._get(storage, pay["output"]["cvProfile"])
            res["output"] = pay["output"]
            res["warnings"] = pay["warnings"]
        return res

    def match_cv(self, parsed: dict, rubric: dict, current_month: str | None = None) -> dict:
        from ihr_ai_worker.pipelines.cv_match import CvMatchHandler
        storage = parsed["storage"]
        env = _fixture("ai/cv_match")
        p = env["payload"]
        p["cvProfile"], p["cvText"] = parsed["output"]["cvProfile"], parsed["output"]["cvText"]
        p["job"], p["requirements"] = rubric["job"], rubric["requirements"]
        kw = {}
        if current_month:
            kw["clock"] = lambda: datetime.strptime(current_month + "-15", "%Y-%m-%d").replace(tzinfo=timezone.utc)
        topic, pay, secs = self._process(CvMatchHandler(self.llm, self.registry, debug_capture_enabled=False, **kw),
                                         storage, env)
        res = {**self._status(topic, pay), "seconds": secs}
        if res["ok"]:
            res["fit"] = self._get(storage, pay["output"])
        return res

    # ---- answers -----------------------------------------------------------------------------------
    def _transcript(self, storage, segments: list[str]) -> dict:
        t = {"schemaVersion": "transcript.v1", "language": "en", "model": "written", "durationMs": 0,
             "speechDetected": bool(segments), "avgConfidence": 1.0, "qualityFlags": [],
             "segments": [{"index": i, "startMs": 0, "endMs": 0, "text": s, "confidence": 1.0, "noSpeechProb": 0.0,
                           "words": []} for i, s in enumerate(segments)]}
        data = json.dumps(t).encode()
        return storage.put_bytes("ihr-generated-private", f"eval/t/{hashlib.sha256(data).hexdigest()}.json", data,
                                 "application/json")

    def assess(self, ans: dict, segments: list[str] | None = None) -> dict:
        from ihr_ai_worker.pipelines.answer_assessment import AnswerAssessmentHandler
        storage = InMemoryStorage()
        env = _fixture("ai/answer_assessment")
        p = env["payload"]
        comps = {c["code"]: c for c in _fixture("ai/interview_plan")["payload"]["competencies"]}
        keep = ("code", "name", "description", "anchors", "weight", "mandatory")
        segs = segments if segments is not None else ans["segments"]
        p.update(transcript=self._transcript(storage, segs), question=ans["question"], turnKind=ans.get("turnKind", "MAIN"),
                 competencies=[{k: comps[c][k] for k in keep} for c in ans["question"]["competencyCodes"]],
                 transcriptQuality={"avgConfidence": 1.0, "speechDetected": bool(segs), "qualityFlags": []},
                 priorContext=[], redactionHints={"fullName": ans.get("knownName"), "email": None, "phone": None})
        topic, pay, secs = self._process(AnswerAssessmentHandler(self.llm, self.registry, debug_capture_enabled=False),
                                         storage, env)
        res = {**self._status(topic, pay), "seconds": secs}
        if res["ok"]:
            res["assessment"] = self._get(storage, pay["output"])
        return res

    def followup(self, ans: dict) -> dict:
        from ihr_ai_worker.pipelines.followup import FollowupHandler
        storage = InMemoryStorage()
        env = _fixture("ai/followup_decision")
        p = env["payload"]
        p.update(transcript=self._transcript(storage, ans["segments"]), question=ans["question"],
                 redactionHints={"fullName": ans.get("knownName"), "email": None, "phone": None})
        topic, pay, secs = self._process(FollowupHandler(self.llm, self.registry, debug_capture_enabled=False),
                                         storage, env)
        return {**self._status(topic, pay), "seconds": secs, "decision": pay.get("output")}

    def plan(self, item: dict) -> dict:
        from ihr_ai_worker.pipelines.interview_plan import InterviewPlanHandler
        from ihr_worker_common.storage import put_json
        storage = InMemoryStorage()
        env = _fixture("ai/interview_plan")
        body = {k: v for k, v in item.items() if k not in ("id", "profile")}
        env["payload"].update(body)
        if item.get("profile"):
            env["payload"]["cvProfile"] = put_json(storage, "ihr-generated-private", f"eval/p/{item['id']}.json",
                                                   item["profile"])
        else:
            env["payload"]["cvProfile"] = None
        topic, pay, secs = self._process(InterviewPlanHandler(self.llm, self.registry, debug_capture_enabled=False),
                                         storage, env)
        res = {**self._status(topic, pay), "seconds": secs, "request": env["payload"]}
        if res["ok"]:
            res["plan"] = self._get(storage, pay["output"])
        return res

    # ---- speech ------------------------------------------------------------------------------------
    def stt_engine(self, model: str, device: str, compute: str):
        from speech.stt import WhisperConfig, WhisperEngine
        eng = WhisperEngine(WhisperConfig(model=model, device=device, compute_type=compute,
                                          model_dir=os.environ.get("WHISPER_MODEL_DIR", "/models/whisper")))
        eng.start()
        return eng

    def transcribe(self, engine, clip: dict) -> dict:
        from speech.stt import TranscriptionHandler
        storage = InMemoryStorage()
        env = _fixture("speech/transcription")
        p = env["payload"]
        p["audio"] = storage.put_bytes("ihr-interview-raw", f"eval/{clip['id']}", clip["audio"].read_bytes(), "audio/webm")
        p["vocabularyHints"] = clip.get("terms", [])[:40]
        topic, pay, secs = self._process(TranscriptionHandler(engine), storage, env)
        res = {"ok": topic.endswith(".completed"), "seconds": secs}
        if res["ok"]:
            t = self._get(storage, pay["output"])
            res.update(text=" ".join(s["text"] for s in t["segments"]), audioSeconds=t["durationMs"] / 1000,
                       computeSeconds=pay["run"]["latencyMs"] / 1000)
        return res
