"""ihr-ai-worker entrypoint. WORKER_ROLE selects subscriptions (A-02):
standard -> cv.parse, cv.match, interview.plan, answer-assessment, final-report
realtime -> interview.followup
"""
from __future__ import annotations

import sys

from ihr_worker_common import log as wlog
from ihr_worker_common.health import build_app, serve_in_background
from ihr_worker_common.processor import Processor
from ihr_worker_common.runner import KafkaRunner
from ihr_worker_common.storage import S3Storage

from .config import AiWorkerConfig
from .mock_handlers import MockTaskHandler
from .tasks import tasks_for_role


def build_processors(cfg: AiWorkerConfig, storage) -> list[Processor]:
    processors = []
    for spec in tasks_for_role(cfg.worker_role):
        if cfg.ai_provider == "mock":
            handler = MockTaskHandler(spec, failure_rate=cfg.mock_failure_rate)
        else:
            # Real task pipelines arrive per the delivery plan (§15): CV_PARSE S2, CV_MATCH S3,
            # INTERVIEW_PLAN S4, FOLLOWUP + ANSWER_ASSESSMENT S5, FINAL_REPORT S6.
            raise SystemExit(f"AI_PROVIDER={cfg.ai_provider}: task {spec.task} is not implemented yet; use AI_PROVIDER=mock")
        processors.append(Processor(handler, storage, "ihr-ai-worker", "ihr_ai",
                                    max_workers=cfg.kimi_max_concurrency))
    return processors


def main() -> int:
    cfg = AiWorkerConfig.from_env()
    wlog.configure("ihr-ai-worker")
    storage = S3Storage()
    processors = build_processors(cfg, storage)
    serve_in_background(build_app("ihr-ai-worker"), cfg.health_port)
    runner = KafkaRunner(processors, cfg.consumer_group, cfg.kimi_max_concurrency)
    runner.install_signal_handlers()
    runner.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
