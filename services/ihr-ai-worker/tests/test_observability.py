"""§12.1 metrics exist with the spec labels; §12.2 alert rules cover every alert and reference real metrics."""
import re
from pathlib import Path

import yaml
from prometheus_client import REGISTRY

ROOT = Path(__file__).resolve().parents[3]

SPEC_METRICS = {
    "ihr_ai_jobs_total": {"task", "outcome", "error_code"},
    "ihr_ai_job_duration_seconds": {"task"},
    "ihr_ai_llm_latency_seconds": {"task", "model"},
    "ihr_ai_tokens_total": {"task", "model", "direction"},
    "ihr_ai_validation_total": {"task", "result"},
    "ihr_ai_policy_rejections_total": {"task", "rule"},
    "ihr_ai_fallback_questions_total": {"reason"},
    "ihr_ai_injection_flags_total": {"task"},
    "ihr_ai_redactions_total": {"type"},
    "ihr_stt_duration_seconds": {"model"},
    "ihr_stt_audio_seconds": {"model"},
    "ihr_stt_quality_flags_total": {"flag"},
    "ihr_provider_circuit_open": {"provider"},
}
SPEC_ALERTS = {"FollowupLatencyHigh", "SttLatencyHigh", "AiFailureRate", "RepairRateHigh", "ProviderCircuitOpen",
               "TokenBudget80", "TokenBudget100", "InjectionSpike", "GpuUnavailable"}


def _register_all():
    from ihr_worker_common.processor import Processor  # noqa: F401
    from ihr_worker_common.metrics import counter, histogram
    from ihr_worker_common.storage import InMemoryStorage
    from ihr_ai_worker.llm.kimi import KimiClient
    from ihr_ai_worker.llm.mock import MockLlmClient
    from ihr_ai_worker.pipelines import metrics as m
    from ihr_ai_worker.pipelines.cv_parse import CvParseHandler
    from ihr_ai_worker.prompts import load_registry
    from speech.stt import TranscriptionHandler, WhisperConfig, WhisperEngine
    Processor(CvParseHandler(MockLlmClient(), load_registry(ROOT / "docs/prompts/active.yaml"), scanner=object()),
              InMemoryStorage(), "ihr-ai-worker", "ihr_ai").shutdown()
    KimiClient(base_url="http://x/v1", api_key="k", model_standard="s", model_realtime="r", max_concurrency=1, rpm_limit=60)
    from ihr_ai_worker.llm.resilience import CircuitBreaker
    CircuitBreaker("kimi-standard")
    m.policy_rejection("T", "R"); m.injection_flag("T"); m.fallback_question("x")
    counter("ihr_ai_validation_total", "Validation outcome", ("task", "result"))
    TranscriptionHandler(WhisperEngine(WhisperConfig(device="cpu"), loader=lambda: None))


def test_spec_metrics_registered_with_labels():
    _register_all()
    names = {}
    for collector, metric_names in list(REGISTRY._collector_to_names.items()):
        for n in metric_names:
            names[n] = set(getattr(collector, "_labelnames", ()))
    for metric, labels in SPEC_METRICS.items():
        base = metric.removesuffix("_total")
        found = names.get(metric) if metric in names else names.get(base)
        assert found is not None, f"{metric} not registered"
        assert labels <= found, f"{metric}: labels {found} missing {labels - found}"


def test_alert_rules_cover_spec():
    rules = yaml.safe_load((ROOT / "deploy/monitoring/ai-alerts.rules.yml").read_text())["groups"][0]["rules"]
    assert {r["alert"] for r in rules} == SPEC_ALERTS
    used = set(re.findall(r"\b(ihr_[a-z_]+?)(?:_bucket)?\b", " ".join(r["expr"] for r in rules)))
    known = set(SPEC_METRICS) | {"ihr_speech_job_duration_seconds", "ihr_ai_token_budget_monthly"}
    assert used <= known, used - known
