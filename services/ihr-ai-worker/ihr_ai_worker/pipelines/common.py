"""Helpers shared by the task pipelines."""
from __future__ import annotations

import time

from ihr_worker_common.events import worker_version


def run_meta(llm, prompt, result, slot: str, t0: float) -> dict:
    return {
        "provider": llm.provider_name,
        "model": result.results[-1].model or llm.model_for(slot),
        "promptId": prompt.prompt_id,
        "promptVersion": prompt.version,
        "promptSha256": prompt.sha256,
        "providerRequestId": result.results[-1].provider_request_id,
        "latencyMs": int((time.monotonic() - t0) * 1000),
        "inputTokens": result.input_tokens,
        "outputTokens": result.output_tokens,
        "providerAttempts": result.provider_attempts,
        "repairAttempted": result.repaired,
        "validationStatus": "REPAIRED" if result.repaired else "VALID",
        "workerVersion": worker_version(),
    }


def capture_calls(result) -> list[dict]:
    return [{"messages": m, "response": r.content, "finishReason": r.finish_reason}
            for m, r in zip(result.requests, result.results)]
