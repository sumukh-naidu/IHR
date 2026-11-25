from datetime import datetime, timezone
from pathlib import Path

import pytest

from ihr_ai_worker.debug_capture import capture, capture_keys
from ihr_ai_worker.llm.base import TASK_PARAMS
from ihr_ai_worker.prompts.loader import PromptError, fill, load_registry
from ihr_ai_worker.tasks import TASKS
from ihr_worker_common.storage import InMemoryStorage

ACTIVE = Path(__file__).resolve().parents[3] / "docs" / "prompts" / "active.yaml"


@pytest.fixture(scope="module")
def registry():
    return load_registry(ACTIVE)


def test_every_task_has_an_active_prompt(registry):
    for spec in TASKS.values():
        p = registry.get(spec.prompt_id)
        assert p.task == spec.task
        assert p.output_schema == spec.output_schema_version
        assert p.model_slot == spec.role


def test_front_matter_matches_task_params(registry):
    for spec in TASKS.values():
        p, params = registry.get(spec.prompt_id), TASK_PARAMS[spec.task]
        assert p.temperature == params.temperature
        assert p.max_tokens == params.max_tokens


def test_system_message_layout(registry):
    p = registry.get("cv_match")
    text = p.system()
    assert text.startswith("You are a component of IHR")
    assert "Task: compare the candidate's CV evidence" in text
    assert "Output schema:\n{" in text and "Example output:\n{" in text
    assert len(p.sha256) == 64


def test_variables_are_filled_and_required(registry):
    p = registry.get("interview_plan")
    with pytest.raises(PromptError):
        p.system()
    text = p.system(maxAnswerMinutes=3, allowedTypes="SCENARIO, PAST_EXPERIENCE", maxAnswerSeconds=180)
    assert "at most 3 minutes" in text and "{" in text  # schema braces survive


def test_fill_leaves_no_placeholders():
    assert fill("a {x} b", {"x": 1}) == "a 1 b"


def test_sha_is_stable(registry):
    assert registry.get("cv_parse").sha256 == load_registry(ACTIVE).get("cv_parse").sha256


def test_warmup_variant_present(registry):
    assert "Do not score" in registry.get("answer_assessment").variants["System (WARMUP and CLOSING only)"]


def test_debug_capture_keys_and_content():
    st = InMemoryStorage()
    when = datetime(2026, 12, 2, tzinfo=timezone.utc)
    capture(st, enabled=True, job_id="j1", task="CV_MATCH", prompt_id="cv_match", prompt_version="1.0.0",
            calls=[{"messages": [{"role": "user", "content": "x"}], "response": "{}", "finishReason": "stop"}],
            when=when)
    req, resp = capture_keys("j1", when)
    assert req == "ai-runs/2026-12-02/j1/request.json"
    assert st.exists("ihr-generated-private", req) and st.exists("ihr-generated-private", resp)


def test_debug_capture_disabled_writes_nothing():
    st = InMemoryStorage()
    capture(st, enabled=False, job_id="j1", task="CV_MATCH", prompt_id="cv_match", prompt_version="1.0.0",
            calls=[{"messages": [], "response": "{}"}])
    assert not st.exists("ihr-generated-private", capture_keys("j1")[0])
