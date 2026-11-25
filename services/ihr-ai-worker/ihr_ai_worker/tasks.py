"""Task registry: topics, schemas, prompt ids and output keys (A.4, A.5, A.6, §7)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable


def generated_bucket() -> str:
    return os.environ.get("S3_BUCKET_GENERATED", "ihr-generated-private")


@dataclass(frozen=True)
class TaskSpec:
    task: str                      # ProcessingJobType
    request_topic: str
    output_schema_version: str
    prompt_id: str
    fixture_dir: str
    role: str                      # WORKER_ROLE: standard | realtime
    output_key: Callable[[dict], str] | None  # None = inline output (followup)


TASKS: dict[str, TaskSpec] = {t.task: t for t in [
    TaskSpec("CV_PARSE", "ihr.cv.parse.requested", "cv_profile.v1", "cv_parse", "cv_parse", "standard",
             lambda p: f"cv/{p['documentId']}/profile/{p['jobId']}.json"),
    TaskSpec("CV_MATCH", "ihr.cv.match.requested", "cv_match.v1", "cv_match", "cv_match", "standard",
             lambda p: f"application/{p['applicationId']}/fit/{p['jobId']}.json"),
    TaskSpec("INTERVIEW_PLAN", "ihr.interview.plan.requested", "interview_plan.v1", "interview_plan",
             "interview_plan", "standard",
             lambda p: f"interview/{p['interviewId']}/plans/{p['jobId']}.json"),
    TaskSpec("FOLLOWUP", "ihr.interview.followup.requested", "followup_decision.v1", "followup_decision",
             "followup_decision", "realtime", None),
    TaskSpec("ANSWER_ASSESSMENT", "ihr.interview.answer-assessment.requested", "answer_assessment.v1",
             "answer_assessment", "answer_assessment", "standard",
             lambda p: f"interview/{p['interviewId']}/assessments/{p['turnId']}/{p['jobId']}.json"),
    TaskSpec("FINAL_REPORT", "ihr.interview.final-report.requested", "final_report.v1", "final_report",
             "final_report", "standard",
             lambda p: f"interview/{p['interviewId']}/reports/{p['jobId']}.json"),
]}


def cv_text_key(payload: dict) -> str:
    return f"cv/{payload['documentId']}/text/{payload['jobId']}.txt"


def tasks_for_role(role: str) -> list[TaskSpec]:
    return [t for t in TASKS.values() if t.role == role]
