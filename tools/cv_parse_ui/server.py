"""Manual test UI for CV_PARSE (dev only; not part of the worker image).

Runs the real CV_PARSE pipeline (extraction -> redaction -> model -> validation -> enrichment)
against a local OpenAI-compatible server (llama-server) or the mock LLM.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "libs/py-worker-common/src"), str(ROOT / "services/ihr-ai-worker")]
os.environ.setdefault("IHR_CONTRACTS_DIR", str(ROOT / "contracts"))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from ihr_worker_common.processor import Processor  # noqa: E402
from ihr_worker_common.storage import InMemoryStorage  # noqa: E402
from ihr_ai_worker.llm import base  # noqa: E402
from ihr_ai_worker.llm.kimi import KimiClient  # noqa: E402
from ihr_ai_worker.llm.mock import MockLlmClient  # noqa: E402
from ihr_ai_worker.pipelines.cv_parse import CvParseHandler  # noqa: E402
from ihr_ai_worker.prompts import load_registry  # noqa: E402

LOCAL_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8081/v1")
LOCAL_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6-35b-a3b")
# local inference is slower than Kimi; widen only the per-call timeout for this tool
base.TASK_PARAMS["CV_PARSE"] = dataclasses.replace(base.TASK_PARAMS["CV_PARSE"], timeout_s=600)

REGISTRY = load_registry(ROOT / "docs/prompts/active.yaml")
REQUEST = json.loads((ROOT / "contracts/fixtures/ai/cv_parse/request.json").read_text())
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
    handler = CvParseHandler(llm, REGISTRY, scanner=SkipScan(), debug_capture_enabled=True)
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
        def get(ref):
            return storage.get_bytes(ref["bucket"], ref["key"])
        result["profile"] = json.loads(get(payload["output"]["cvProfile"]))
        result["redactedText"] = get(payload["output"]["cvText"]).decode("utf-8")
    captured = {k: json.loads(v) for (_, k), (v, _) in storage.objects.items() if k.startswith("ai-runs/")}
    result["modelCalls"] = captured
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
    return {"localModel": ok, "localUrl": LOCAL_URL, "model": LOCAL_MODEL}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("UI_PORT", "8765")), log_level="warning")
