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
    errs = result.errors or [[] for _ in result.results]
    return [{"messages": m, "response": r.content, "finishReason": r.finish_reason, "errors": e}
            for m, r, e in zip(result.requests, result.results, errs)]


def capture_failure(handler, ctx, err) -> None:
    """A-12: also keep the model calls and validation errors of a job that failed validation."""
    from .. import debug_capture
    result = getattr(err, "calls", None)
    if result is None:
        return
    debug_capture.capture(ctx.storage, enabled=handler.capture, job_id=ctx.job_id, task=handler.task,
                          prompt_id=handler.prompt.prompt_id, prompt_version=handler.prompt.version,
                          calls=capture_calls(result), outcome=err.code)
