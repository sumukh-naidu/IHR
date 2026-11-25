"""Manual test UI for CV_PARSE, CV_MATCH and STT (dev only; not part of any image).

Runs the real pipelines against a local OpenAI-compatible server (llama-server) or the mock LLM,
and real faster-whisper (small.en, CPU int8 = the spec's LOCAL/DEV setting) for speech-to-text.
ClamAV is skipped (no scanner on a dev Mac); OCR needs Tesseract installed.

    .venv/bin/python tools/cv_parse_ui/server.py     ->  http://127.0.0.1:8765
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "libs/py-worker-common/src"), str(ROOT / "services/ihr-ai-worker"),
                str(ROOT / "services/ihr-speech-service")]
os.environ.setdefault("IHR_CONTRACTS_DIR", str(ROOT / "contracts"))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from ihr_worker_common.processor import Processor  # noqa: E402
from ihr_worker_common.storage import InMemoryStorage  # noqa: E402
from ihr_ai_worker.llm import base  # noqa: E402
from ihr_ai_worker.llm.kimi import KimiClient  # noqa: E402
from ihr_ai_worker.llm.mock import MockLlmClient  # noqa: E402
from ihr_ai_worker.pipelines.cv_match import CvMatchHandler  # noqa: E402
from ihr_ai_worker.pipelines.cv_parse import CvParseHandler  # noqa: E402
from ihr_ai_worker.pipelines.interview_plan import InterviewPlanHandler  # noqa: E402
from ihr_ai_worker.pipelines import followup as followup_mod  # noqa: E402
from ihr_ai_worker.pipelines.answer_assessment import AnswerAssessmentHandler  # noqa: E402
from ihr_ai_worker.pipelines.followup import FollowupHandler  # noqa: E402
from ihr_ai_worker.pipelines.final_report import FinalReportHandler  # noqa: E402
from speech.tts import SynthesisHandler, build_providers  # noqa: E402
from speech.tts_mock import MockSynthesisHandler  # noqa: E402
from speech.stt import TranscriptionHandler, WhisperConfig, WhisperEngine  # noqa: E402
from speech.stt_mock import MockTranscriptionHandler  # noqa: E402
from ihr_ai_worker.prompts import load_registry  # noqa: E402

LOCAL_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8081/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6-35b-a3b")
# local inference is slower than Kimi; widen only the per-call timeout for this tool
for _t in ("CV_PARSE", "CV_MATCH", "INTERVIEW_PLAN", "FOLLOWUP", "ANSWER_ASSESSMENT", "FINAL_REPORT"):
    base.TASK_PARAMS[_t] = dataclasses.replace(base.TASK_PARAMS[_t], timeout_s=600)

REGISTRY = load_registry(ROOT / "docs/prompts/active.yaml")
REQUEST = json.loads((ROOT / "contracts/fixtures/ai/cv_parse/request.json").read_text())
MATCH_REQUEST = json.loads((ROOT / "contracts/fixtures/ai/cv_match/request.json").read_text())
PLAN_REQUEST = json.loads((ROOT / "contracts/fixtures/ai/interview_plan/request.json").read_text())
TTS_REQUEST = json.loads((ROOT / "contracts/fixtures/speech/synthesis/request.json").read_text())
TTS_STORAGE = InMemoryStorage()      # shared so repeated texts show the cache hit (§11.2 step 1)
_TTS_REAL: SynthesisHandler | None = None
FU_REQUEST = json.loads((ROOT / "contracts/fixtures/ai/followup_decision/request.json").read_text())
AA_REQUEST = json.loads((ROOT / "contracts/fixtures/ai/answer_assessment/request.json").read_text())
REPORT_REQUEST = json.loads((ROOT / "contracts/fixtures/ai/final_report/request.json").read_text())
EVALS: "OrderedDict[str, dict]" = OrderedDict()          # evalId -> one assessed turn (per browser tab)
TRANSCRIPTS: "OrderedDict[str, dict]" = OrderedDict()   # transcriptId -> storage, ref, quality (per browser tab)
STT_REQUEST = json.loads((ROOT / "contracts/fixtures/speech/transcription/request.json").read_text())
WHISPER_MODEL = os.environ.get("UI_WHISPER_MODEL", "small.en")
SOFFICE = os.environ.get("LIBREOFFICE_BIN", "/Applications/LibreOffice.app/Contents/MacOS/soffice")
PARSES: "OrderedDict[str, dict]" = OrderedDict()   # parseId -> storage + output refs (per browser tab)
MAX_PARSES = 20
_ENGINE: WhisperEngine | None = None
LOCAL = KimiClient(base_url=LOCAL_URL, api_key="local", model_standard=LOCAL_MODEL, model_realtime=LOCAL_MODEL,
                   max_concurrency=1, rpm_limit=60, provider_name="local")


class SkipScan:
    def scan(self, data: bytes) -> None:
        pass


app = FastAPI(title="CV_PARSE test UI")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


@app.post("/api/parse")
async def parse(request: Request, ext: str = "pdf", name: str = "", provider: str = "local") -> JSONResponse:
    data = await request.body()
    ext = ext.lower().lstrip(".")
    if ext not in ("pdf", "docx", "doc"):
        return JSONResponse({"error": "Only .pdf, .docx and .doc are accepted (request schema)."}, 400)
    storage = InMemoryStorage()
    env = json.loads(json.dumps(REQUEST))
    p = env["payload"]
    storage.put_bytes(p["document"]["bucket"], p["document"]["key"], data, "application/octet-stream")
    p["document"].update(sha256=hashlib.sha256(data).hexdigest(), sizeBytes=len(data))
    p["originalExtension"] = ext
    p["redactionHints"] = {"fullName": name.strip() or None, "email": None, "phone": None}

    llm = MockLlmClient() if provider == "mock" else LOCAL
    handler = CvParseHandler(llm, REGISTRY, scanner=SkipScan(), soffice=SOFFICE, debug_capture_enabled=True)
    proc = Processor(handler, storage, "ihr-ai-worker", "ui")
    t0 = time.monotonic()
    try:
        outs = await _run(proc, env)
    finally:
        proc.shutdown()
    elapsed = round(time.monotonic() - t0, 1)
    out = outs[0]
    payload = json.loads(out.value)["payload"]
    result = {"topic": out.topic, "seconds": elapsed, "payload": payload}
    if out.topic.endswith(".completed"):
        parse_id = uuid.uuid4().hex
        PARSES[parse_id] = {"storage": storage, "output": payload["output"], "file": request.query_params.get("fname", "")}
        while len(PARSES) > MAX_PARSES:
            PARSES.popitem(last=False)
        result["parseId"] = parse_id
        def get(ref):
            return storage.get_bytes(ref["bucket"], ref["key"])
        result["profile"] = json.loads(get(payload["output"]["cvProfile"]))
        result["redactedText"] = get(payload["output"]["cvText"]).decode("utf-8")
    captured = {k: json.loads(v) for (_, k), (v, _) in storage.objects.items() if k.startswith("ai-runs/")}
    result["modelCalls"] = captured
    return JSONResponse(result)


def _event(proc, env):
    try:
        [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    finally:
        proc.shutdown()
    return out, json.loads(out.value)["payload"]


@app.post("/api/match")
async def match(request: Request) -> JSONResponse:
    import anyio
    body = await request.json()
    parsed = PARSES.get(body.get("parseId") or "")
    if not parsed:
        return JSONResponse({"error": "Parse a CV in step 1 first (or parse it again: the server was restarted)."}, 400)
    env = json.loads(json.dumps(MATCH_REQUEST))
    p = env["payload"]
    p["cvProfile"], p["cvText"] = parsed["output"]["cvProfile"], parsed["output"]["cvText"]
    p["job"], p["requirements"] = body["job"], body["requirements"]
    try:
        from ihr_worker_common import contracts
        contracts.validate_payload("ihr.cv.match.requested", p)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Request is not valid: {e}"}, 400)
    storage = parsed["storage"]
    llm = MockLlmClient() if body.get("provider") == "mock" else LOCAL
    proc = Processor(CvMatchHandler(llm, REGISTRY), storage, "ihr-ai-worker", "ui")
    t0 = time.monotonic()
    out, payload = await anyio.to_thread.run_sync(lambda: _event(proc, env))
    result = {"topic": out.topic, "seconds": round(time.monotonic() - t0, 1), "payload": payload,
              "cvFile": parsed.get("file", "")}
    if out.topic.endswith(".completed"):
        result["fit"] = json.loads(storage.get_bytes(payload["output"]["bucket"], payload["output"]["key"]))
    result["modelCalls"] = {k: json.loads(v) for (_, k), (v, _) in storage.objects.items()
                            if k.startswith("ai-runs/") and env["payload"]["jobId"] in k}
    return JSONResponse(result)


def _engine() -> WhisperEngine:
    global _ENGINE
    if _ENGINE is None:
        eng = WhisperEngine(WhisperConfig(model=WHISPER_MODEL, model_dir="/nonexistent", device="cpu",
                                          compute_type="int8", num_workers=2))
        eng.start()
        _ENGINE = eng
    return _ENGINE


@app.post("/api/transcribe")
async def transcribe(request: Request, hints: str = "", provider: str = "whisper", maxSeconds: int = 400,
                     mime: str = "audio/webm") -> JSONResponse:
    import anyio
    data = await request.body()
    storage = InMemoryStorage()
    env = json.loads(json.dumps(STT_REQUEST))
    p = env["payload"]
    p["audio"] = storage.put_bytes(p["audio"]["bucket"], p["audio"]["key"], data, mime or "application/octet-stream")
    p["vocabularyHints"] = [h.strip() for h in hints.split(",") if h.strip()][:40]
    p["maxDurationSeconds"] = max(1, maxSeconds)
    t0 = time.monotonic()
    if provider == "mock":
        handler = MockTranscriptionHandler()
    else:
        handler = TranscriptionHandler(await anyio.to_thread.run_sync(_engine))
    load_s = round(time.monotonic() - t0, 1)
    proc = Processor(handler, storage, "ihr-speech-service", "ui")
    out, payload = await anyio.to_thread.run_sync(lambda: _event(proc, env))
    result = {"topic": out.topic, "seconds": round(time.monotonic() - t0, 1), "modelLoadSeconds": load_s,
              "payload": payload}
    if out.topic.endswith(".completed"):
        tr = json.loads(storage.get_bytes(payload["output"]["bucket"], payload["output"]["key"]))
        result["transcript"] = tr
        tid = uuid.uuid4().hex
        TRANSCRIPTS[tid] = {"storage": storage, "ref": payload["output"],
                            "quality": {"avgConfidence": tr["avgConfidence"], "speechDetected": tr["speechDetected"],
                                        "qualityFlags": tr["qualityFlags"]}}
        while len(TRANSCRIPTS) > MAX_PARSES:
            TRANSCRIPTS.popitem(last=False)
        result["transcriptId"] = tid
    return JSONResponse(result)


@app.post("/api/evaluate")
async def evaluate(request: Request) -> JSONResponse:
    """Runs FOLLOWUP and/or ANSWER_ASSESSMENT on a transcript produced in the Speech-to-text tab."""
    import anyio
    body = await request.json()
    t = TRANSCRIPTS.get(body.get("transcriptId") or "")
    if not t:
        return JSONResponse({"error": "Transcribe an answer in step 3 first (or again: the server was restarted)."}, 400)
    q = body["question"]
    keep = ("code", "name", "description", "anchors", "weight", "mandatory")       # assessment request fields
    comps = [{k: c[k] for k in keep} for c in PLAN_REQUEST["payload"]["competencies"] if c["code"] in q["competencyCodes"]]
    hints = {"fullName": body.get("candidateName") or None, "email": None, "phone": None}
    llm = MockLlmClient() if body.get("provider") == "mock" else LOCAL
    if body.get("provider") != "mock":
        followup_mod.DEADLINE_S = int(body.get("followupDeadline", 600))   # local stand-in is slower than Kimi
    storage, out = t["storage"], {}
    if body.get("runFollowup", True) and body.get("turnKind", "MAIN") in ("MAIN",):
        env = json.loads(json.dumps(FU_REQUEST))
        env["eventId"], env["payload"]["jobId"] = str(uuid.uuid4()), str(uuid.uuid4())
        p = env["payload"]
        p.update(transcript=t["ref"], question=q, redactionHints=hints)
        proc = Processor(FollowupHandler(llm, REGISTRY), storage, "ihr-ai-worker", "ui")
        t0 = time.monotonic()
        o, pay = await anyio.to_thread.run_sync(lambda: _event(proc, env))
        out["followup"] = {"topic": o.topic, "seconds": round(time.monotonic() - t0, 1), "payload": pay,
                           "calls": _calls(storage, p["jobId"])}
    if body.get("runAssessment", True):
        env = json.loads(json.dumps(AA_REQUEST))
        env["eventId"], env["payload"]["jobId"] = str(uuid.uuid4()), str(uuid.uuid4())
        p = env["payload"]
        p.update(transcript=t["ref"], question=q, competencies=comps, turnKind=body.get("turnKind", "MAIN"),
                 transcriptQuality=t["quality"], redactionHints=hints, priorContext=[])
        proc = Processor(AnswerAssessmentHandler(llm, REGISTRY), storage, "ihr-ai-worker", "ui")
        t0 = time.monotonic()
        o, pay = await anyio.to_thread.run_sync(lambda: _event(proc, env))
        res = {"topic": o.topic, "seconds": round(time.monotonic() - t0, 1), "payload": pay,
               "calls": _calls(storage, p["jobId"])}
        if o.topic.endswith(".completed"):
            res["assessment"] = json.loads(storage.get_bytes(pay["output"]["bucket"], pay["output"]["key"]))
            eid = uuid.uuid4().hex
            EVALS[eid] = {"storage": storage, "transcriptRef": t["ref"], "assessmentRef": pay["output"],
                          "question": q, "kind": p["turnKind"]}
            while len(EVALS) > 50:
                EVALS.popitem(last=False)
            res["evalId"] = eid
        out["assessment"] = res
    return JSONResponse(out)


def computed_scores_stand_in(comps: list[dict], assessments: list[dict]) -> dict:
    """Stand-in for core's §8.3 formulas (core owns this in production; the AI never computes these)."""
    by: dict[str, list[int]] = {}
    for a in assessments:
        for sc in a["scores"]:
            if sc["score"] is not None:
                by.setdefault(sc["competencyCode"], []).append(sc["score"])
    out = [{"code": c["code"], "score": round(sum(by[c["code"]]) / len(by[c["code"]]), 1) if by.get(c["code"]) else None,
            "assessedTurnCount": len(by.get(c["code"], []))} for c in comps]
    assessed = [c for c in comps if by.get(c["code"])]
    w = sum(c["weight"] for c in assessed)
    overall = round(sum(c["weight"] * (sum(by[c["code"]]) / len(by[c["code"]]) - 1) / 4 for c in assessed) / w * 100, 1) if w else None
    complete = w >= 70 and all(by.get(c["code"]) for c in comps if c["mandatory"])
    if overall is None or not assessed:
        level = "INSUFFICIENT_EVIDENCE"
    else:
        level = "STRONG_EVIDENCE" if overall >= 80 else "MODERATE_EVIDENCE" if overall >= 60 else "LIMITED_EVIDENCE"
    return {"competencies": out, "overallScore": overall, "completionState": "COMPLETE" if complete else "INCOMPLETE",
            "evidenceLevel": level}


@app.post("/api/report")
async def report(request: Request) -> JSONResponse:
    import anyio
    body = await request.json()
    evals = [EVALS[e] for e in body.get("evalIds", []) if e in EVALS]
    if not evals:
        return JSONResponse({"error": "Evaluate at least one answer in step 6 first (or again: the server was restarted)."}, 400)
    storage = InMemoryStorage()        # one interview: copy each turn's transcript + assessment into it
    env = json.loads(json.dumps(REPORT_REQUEST))
    p = env["payload"]
    keep = ("code", "name", "description", "weight", "mandatory", "anchors", "fallbackQuestions")
    comps = [{k: c[k] for k in keep} for c in PLAN_REQUEST["payload"]["competencies"]]
    turns, assessments = [], []
    for n, e in enumerate(evals, 1):
        tid = str(uuid.uuid4())
        tr_bytes = e["storage"].get_bytes(e["transcriptRef"]["bucket"], e["transcriptRef"]["key"])
        aa_bytes = e["storage"].get_bytes(e["assessmentRef"]["bucket"], e["assessmentRef"]["key"])
        assessments.append(json.loads(aa_bytes))
        tref = storage.put_bytes("ihr-generated-private", f"interview/ui/transcripts/{tid}.json", tr_bytes, "application/json")
        aref = storage.put_bytes("ihr-generated-private", f"interview/ui/assessments/{tid}.json", aa_bytes, "application/json")
        turns.append({"turnId": tid, "turnNumber": n, "kind": e["kind"], "questionText": e["question"]["text"],
                      "competencyCodes": e["question"]["competencyCodes"], "transcript": tref, "assessment": aref,
                      "transcriptStatus": "SUCCEEDED", "assessmentStatus": "SUCCEEDED"})
    computed = computed_scores_stand_in(comps, assessments)
    p.update(competencies=comps, turns=turns, computedScores=computed,
             proctoringSummary={k: int(v) for k, v in (body.get("proctoring") or {}).items() if int(v or 0) > 0},
             redactionHints={"fullName": body.get("candidateName") or None, "email": None, "phone": None})
    try:
        from ihr_worker_common import contracts
        contracts.validate_payload("ihr.interview.final-report.requested", p)
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"error": f"Request is not valid: {ex}"}, 400)
    llm = MockLlmClient() if body.get("provider") == "mock" else LOCAL
    proc = Processor(FinalReportHandler(llm, REGISTRY), storage, "ihr-ai-worker", "ui")
    t0 = time.monotonic()
    o, pay = await anyio.to_thread.run_sync(lambda: _event(proc, env))
    res = {"topic": o.topic, "seconds": round(time.monotonic() - t0, 1), "payload": pay, "computedScores": computed,
           "turns": [{"label": f"T{t['turnNumber']}", "kind": t["kind"], "question": t["questionText"]} for t in turns],
           "calls": _calls(storage, p["jobId"])}
    if o.topic.endswith(".completed"):
        res["report"] = json.loads(storage.get_bytes(pay["output"]["bucket"], pay["output"]["key"]))
    return JSONResponse(res)


def _calls(storage, job_id: str) -> dict:
    return {k: json.loads(v) for (_, k), (v, _) in storage.objects.items() if k.startswith("ai-runs/") and job_id in k}


@app.get("/api/plan/defaults")
def plan_defaults() -> dict:
    return {"competencies": PLAN_REQUEST["payload"]["competencies"]}


@app.post("/api/plan")
async def plan(request: Request) -> JSONResponse:
    import anyio
    body = await request.json()
    env = json.loads(json.dumps(PLAN_REQUEST))
    p = env["payload"]
    p["job"] = body["job"]
    p["mainQuestionCount"] = int(body.get("mainQuestionCount", 6))
    p["maxAnswerSeconds"] = int(body.get("maxAnswerSeconds", 180))
    p["competencies"] = body.get("competencies") or p["competencies"]
    storage, cv_file = InMemoryStorage(), None
    parsed = PARSES.get(body.get("parseId") or "") if body.get("useCv", True) else None
    if parsed:
        storage, cv_file = parsed["storage"], parsed.get("file")
        p["cvProfile"] = parsed["output"]["cvProfile"]
    else:
        p["cvProfile"] = None
    try:
        from ihr_worker_common import contracts
        contracts.validate_payload("ihr.interview.plan.requested", p)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"Request is not valid: {e}"}, 400)
    llm = MockLlmClient() if body.get("provider") == "mock" else LOCAL
    proc = Processor(InterviewPlanHandler(llm, REGISTRY), storage, "ihr-ai-worker", "ui")
    t0 = time.monotonic()
    out, payload = await anyio.to_thread.run_sync(lambda: _event(proc, env))
    result = {"topic": out.topic, "seconds": round(time.monotonic() - t0, 1), "payload": payload, "cvFile": cv_file}
    if out.topic.endswith(".completed"):
        result["plan"] = json.loads(storage.get_bytes(payload["output"]["bucket"], payload["output"]["key"]))
    result["modelCalls"] = {k: json.loads(v) for (_, k), (v, _) in storage.objects.items()
                            if k.startswith("ai-runs/") and p["jobId"] in k}
    return JSONResponse(result)


def _tts_real() -> SynthesisHandler:
    global _TTS_REAL
    if _TTS_REAL is None:
        _TTS_REAL = SynthesisHandler(build_providers("azure", "polly"))
    return _TTS_REAL


@app.post("/api/tts")
async def tts(request: Request) -> JSONResponse:
    import anyio
    import base64
    body = await request.json()
    text = body.get("text", "")
    provider = body.get("provider", "mock")
    if provider == "real" and not (os.environ.get("AZURE_SPEECH_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")):
        return JSONResponse({"error": "No Azure or AWS credentials set. Start the server with AZURE_SPEECH_KEY "
                                      "and/or AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, or use the mock."}, 400)
    env = json.loads(json.dumps(TTS_REQUEST))
    p = env["payload"]
    p["text"] = text
    p["jobId"] = str(uuid.uuid4())
    p["purpose"] = body.get("purpose", "QUESTION")
    version = os.environ.get("TTS_PROFILE_VERSION", "1")
    # cacheKey is computed by core (A.6); same inputs -> same key -> cache hit
    p["cacheKey"] = hashlib.sha256(f"{provider}|{p['voiceProfileKey']}|{version}|{' '.join(text.split())}".encode()).hexdigest()
    env["eventId"] = str(uuid.uuid4())
    handler = MockSynthesisHandler() if provider == "mock" else _tts_real()
    proc = Processor(handler, TTS_STORAGE, "ihr-speech-service", "ui")
    t0 = time.monotonic()
    out, payload = await anyio.to_thread.run_sync(lambda: _event(proc, env))
    result = {"topic": out.topic, "seconds": round(time.monotonic() - t0, 2), "payload": payload}
    if out.topic.endswith(".completed"):
        ref = payload["output"]
        result["audioB64"] = base64.b64encode(TTS_STORAGE.get_bytes(ref["bucket"], ref["key"])).decode()
    from speech.tts import normalise_for_speech
    result["spokenText"] = normalise_for_speech(text)      # what the voice is asked to say (lexicon applied)
    return JSONResponse(result)


async def _run(proc, env):
    import anyio
    return await anyio.to_thread.run_sync(lambda: proc.process(json.dumps(env).encode(), env["entityId"], {}))


@app.get("/api/health")
def health() -> dict:
    import httpx
    try:
        ok = httpx.get(LOCAL_URL.removesuffix("/v1") + "/health", timeout=2).json().get("status") == "ok"
    except Exception:
        ok = False
    return {"localModel": ok, "localUrl": LOCAL_URL, "model": LOCAL_MODEL, "whisper": WHISPER_MODEL,
            "whisperLoaded": _ENGINE is not None}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("UI_PORT", "8765")), log_level="warning")
