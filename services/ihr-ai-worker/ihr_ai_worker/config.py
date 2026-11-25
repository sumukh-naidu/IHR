"""Environment configuration (AI Specification v1.1 §3.3)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AiWorkerConfig:
    worker_role: str                 # standard | realtime (A-02)
    ai_provider: str                 # kimi | local | mock (local = dev stand-in, ASSUMPTIONS #19)
    kimi_api_base_url: str
    kimi_api_key: str | None
    kimi_model_standard: str
    kimi_model_realtime: str
    kimi_max_concurrency: int        # 8 standard, 4 realtime (A-09)
    kimi_rpm_limit: int
    kimi_monthly_token_budget: int
    ai_debug_capture: bool
    prompts_active_file: str
    mock_failure_rate: float
    health_port: int
    clamav_host: str = "ihr-clamav"
    clamav_port: int = 3310
    ocr_max_pages: int = 10
    libreoffice_bin: str = "/usr/bin/soffice"

    @property
    def consumer_group(self) -> str:
        return "ihr-ai-worker-realtime" if self.worker_role == "realtime" else "ihr-ai-worker"

    @classmethod
    def from_env(cls) -> "AiWorkerConfig":
        role = os.environ.get("WORKER_ROLE", "standard")
        if role not in ("standard", "realtime"):
            raise ValueError("WORKER_ROLE must be standard or realtime")
        provider = os.environ.get("AI_PROVIDER", "mock")
        if provider not in ("kimi", "local", "mock"):
            raise ValueError("AI_PROVIDER must be kimi, local or mock")
        key = os.environ.get("KIMI_API_KEY")
        if provider == "kimi" and not key:
            raise ValueError("KIMI_API_KEY is required when AI_PROVIDER=kimi")
        if provider == "local":
            # Local OpenAI-compatible server (llama-server) for integration testing only.
            base_url = os.environ.get("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8081/v1")
            key = os.environ.get("LOCAL_LLM_API_KEY", "local")
            local_model = os.environ.get("LOCAL_LLM_MODEL", "qwen3.6-35b-a3b")
            model_std = os.environ.get("LOCAL_LLM_MODEL_STANDARD", local_model)
            model_rt = os.environ.get("LOCAL_LLM_MODEL_REALTIME", local_model)
        else:
            base_url = os.environ.get("KIMI_API_BASE_URL", "https://api.moonshot.ai/v1")
            model_std = os.environ.get("KIMI_MODEL_STANDARD", "kimi-k2-0905-preview")
            model_rt = os.environ.get("KIMI_MODEL_REALTIME", "kimi-k2-turbo-preview")
        return cls(
            worker_role=role,
            ai_provider=provider,
            kimi_api_base_url=base_url,
            kimi_api_key=key,
            kimi_model_standard=model_std,
            kimi_model_realtime=model_rt,
            kimi_max_concurrency=int(os.environ.get("KIMI_MAX_CONCURRENCY", "4" if role == "realtime" else "8")),
            kimi_rpm_limit=int(os.environ.get("KIMI_RPM_LIMIT", "60")),
            kimi_monthly_token_budget=int(os.environ.get("KIMI_MONTHLY_TOKEN_BUDGET", "200000000")),
            ai_debug_capture=os.environ.get("AI_DEBUG_CAPTURE", "true").lower() == "true",
            prompts_active_file=os.environ.get("PROMPTS_ACTIVE_FILE", "/app/prompts/active.yaml"),
            mock_failure_rate=float(os.environ.get("MOCK_FAILURE_RATE", "0")),
            health_port=int(os.environ.get("HEALTH_PORT", "8080")),
            clamav_host=os.environ.get("CLAMAV_HOST", "ihr-clamav"),
            clamav_port=int(os.environ.get("CLAMAV_PORT", "3310")),
            ocr_max_pages=int(os.environ.get("OCR_MAX_PAGES", "10")),
            libreoffice_bin=os.environ.get("LIBREOFFICE_BIN", "/usr/bin/soffice"),
        )
