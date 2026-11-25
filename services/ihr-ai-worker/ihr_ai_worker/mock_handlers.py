"""AI_PROVIDER=mock (Appendix A.8).

Returns deterministic fixtures from contracts/fixtures/ai/ selected by task, still runs
schema validation and writes real objects/events. Simulated latency: 300 ms (followup),
1 s (others). MOCK_FAILURE_RATE (0-1) injects TRANSIENT failures.

Note: from Sprint 2 onwards each real task pipeline also runs in mock mode through
MockLlmClient (kimi_response.json -> full validation path, §7.7). These handlers are
the Sprint 1 stand-ins that let Platform integrate on day one.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json

from .tasks import TaskSpec, cv_text_key, generated_bucket

LATENCY_S = {"FOLLOWUP": 0.3}
DEFAULT_LATENCY_S = 1.0


def mock_run_meta(spec: TaskSpec, latency_ms: int) -> dict:
    return {
        "provider": "mock",
        "model": f"mock-{spec.role}",
        "promptId": spec.prompt_id,
        "promptVersion": None,
        "promptSha256": None,
        "providerRequestId": None,
        "latencyMs": latency_ms,
        "inputTokens": None,
        "outputTokens": None,
        "providerAttempts": 1,
        "repairAttempted": False,
        "validationStatus": "VALID",
        "workerVersion": worker_version(),
    }


class MockTaskHandler:
    def __init__(self, spec: TaskSpec, failure_rate: float = 0.0, latency_s: float | None = None,
                 rng: random.Random | None = None, fixtures_root: Path | None = None) -> None:
        self.spec = spec
        self.task = spec.task
        self.request_topic = spec.request_topic
        self.output_schema_version = spec.output_schema_version
        self.failure_rate = failure_rate
        self.latency_s = LATENCY_S.get(spec.task, DEFAULT_LATENCY_S) if latency_s is None else latency_s
        self.rng = rng or random.Random()
        self.dir = (fixtures_root or contracts.contracts_dir() / "fixtures" / "ai") / spec.fixture_dir

    def output_location(self, payload: dict) -> tuple[str, str] | None:
        if self.spec.output_key is None:
            return None
        return generated_bucket(), self.spec.output_key(payload)

    def _fixture(self, name: str):
        text = (self.dir / name).read_text(encoding="utf-8")
        return json.loads(text) if name.endswith(".json") else text

    def handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        if self.latency_s:
            time.sleep(self.latency_s)
        if self.failure_rate and self.rng.random() < self.failure_rate:
            raise ProcessingError("PROVIDER_UNAVAILABLE", "Injected mock failure (MOCK_FAILURE_RATE).")

        artefact = self._fixture("expected_output.json")
        if self.task == "FINAL_REPORT":
            # §7.6: code attaches computedScores unchanged from the request.
            artefact["computedScores"] = ctx.payload["computedScores"]
        contracts.validate_artefact(self.output_schema_version, artefact)

        run = mock_run_meta(self.spec, int((time.monotonic() - t0) * 1000))
        extras_path = self.dir / "completed_extras.json"
        extras = json.loads(extras_path.read_text(encoding="utf-8")) if extras_path.exists() else {}

        if self.spec.output_key is None:  # FOLLOWUP: inline output (A.5)
            return HandlerResult(output=artefact, run=run)

        bucket = generated_bucket()
        if self.task == "CV_PARSE":
            text = self._fixture("cv_text.txt")
            text_ref = ctx.storage.put_bytes(bucket, cv_text_key(ctx.payload), text.encode("utf-8"),
                                             "text/plain; charset=utf-8")
            profile_ref = put_json(ctx.storage, bucket, self.spec.output_key(ctx.payload), artefact)
            return HandlerResult(output={"cvProfile": profile_ref, "cvText": text_ref}, run=run, extras=extras)

        out_ref = put_json(ctx.storage, bucket, self.spec.output_key(ctx.payload), artefact)
        return HandlerResult(output=out_ref, run=run, extras=extras)
