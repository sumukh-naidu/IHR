"""Debug capture of model calls (A-12).

Request messages (already redacted) and raw responses are written to
ihr-generated-private/ai-runs/{yyyy-mm-dd}/{jobId}/request.json and response.json.
Expiry after 30 days is a bucket lifecycle rule (Infra); access is break-glass only.
Nothing here is logged: logs and telemetry never contain prompt or response text.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ihr_worker_common.storage import Storage, put_json

from .tasks import generated_bucket


def capture_keys(job_id: str, when: datetime | None = None) -> tuple[str, str]:
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    prefix = f"ai-runs/{day}/{job_id}"
    return f"{prefix}/request.json", f"{prefix}/response.json"


def capture(storage: Storage, *, enabled: bool, job_id: str, task: str, prompt_id: str, prompt_version: str,
            calls: list[dict], when: datetime | None = None, outcome: str = "VALID",
            validation_errors: list[str] | None = None) -> None:
    """calls: one entry per provider call, {"messages": [...], "response": str, "finishReason": str}.

    Best effort: a capture failure must never fail the job.
    """
    if not enabled or not calls:
        return
    req_key, resp_key = capture_keys(job_id, when)
    header = {"jobId": job_id, "task": task, "promptId": prompt_id, "promptVersion": prompt_version}
    try:
        put_json(storage, generated_bucket(), req_key,
                 {**header, "calls": [{"messages": c["messages"]} for c in calls]})
        put_json(storage, generated_bucket(), resp_key,
                 {**header, "outcome": outcome, "validationErrors": [c.get("errors") or [] for c in calls],
                  "calls": [{"response": c["response"], "finishReason": c.get("finishReason")} for c in calls]})
    except Exception:  # noqa: BLE001 - debugging aid only
        pass
