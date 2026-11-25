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
from .extraction.clamav import ClamAvScanner
from .llm.kimi import build_llm_client
from .mock_handlers import MockTaskHandler
from .pipelines.cv_parse import CvParseHandler
from .prompts import load_registry
from .tasks import tasks_for_role

log = wlog.get_logger("ihr.main")


def real_handler(spec, cfg: AiWorkerConfig, llm, prompts):
    """Real task pipelines per the delivery plan (§15): CV_PARSE S2, CV_MATCH S3, INTERVIEW_PLAN S4,
    FOLLOWUP + ANSWER_ASSESSMENT S5, FINAL_REPORT S6. Returns None for tasks not built yet."""
    if spec.task == "CV_PARSE":
        return CvParseHandler(llm, prompts, scanner=ClamAvScanner(cfg.clamav_host, cfg.clamav_port),
                              ocr_max_pages=cfg.ocr_max_pages, soffice=cfg.libreoffice_bin,
                              debug_capture_enabled=cfg.ai_debug_capture)
    return None


def build_processors(cfg: AiWorkerConfig, storage) -> list[Processor]:
    processors = []
    llm = prompts = None
    if cfg.ai_provider != "mock":
        llm = build_llm_client(cfg)
        prompts = load_registry(cfg.prompts_active_file)
    for spec in tasks_for_role(cfg.worker_role):
        handler = real_handler(spec, cfg, llm, prompts) if llm is not None else None
        if handler is None:
            if cfg.ai_provider == "kimi":
                raise SystemExit(f"AI_PROVIDER=kimi: task {spec.task} is not implemented yet")
            if cfg.ai_provider == "local":
                log.warning("task_not_implemented_using_fixtures", task=spec.task)
            handler = MockTaskHandler(spec, failure_rate=cfg.mock_failure_rate)
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
