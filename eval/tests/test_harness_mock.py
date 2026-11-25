"""The harness computes real values for every dataset-backed gate (mock provider, tiny synthetic dataset)."""
import argparse
import json
from pathlib import Path

import pytest

from cv_docs import CV_LINES, docx

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "contracts/fixtures"


@pytest.fixture()
def dataset(tmp_path):
    d = tmp_path
    req = json.loads((FIX / "ai/cv_match/request.json").read_text())["payload"]
    (d / "cv-48/rubrics").mkdir(parents=True)
    (d / "cv-48/rubrics/java.json").write_text(json.dumps({"job": req["job"], "requirements": req["requirements"]}))
    gold = {"role": "JAVA_BACKEND", "knownName": "Asha Verma", "currentMonth": "2026-12",
            "profile": {"employmentHistory": [
                {"employer": "Acme Payments", "title": "Senior Software Engineer", "startDate": "2022-04", "endDate": "PRESENT"},
                {"employer": "Globex Fintech", "title": "Software Engineer", "startDate": "2018-07", "endDate": "2022-03"}],
                "skills": ["Java", "Spring Boot", "Kafka", "PostgreSQL", "Docker"], "totalExperienceMonths": 102},
            "redaction": {"mustRemove": {"EMAIL": ["asha.verma@example.com"], "PHONE": ["+91 98765 43210"], "GOV_ID": [],
                                         "NAME_TOKENS": ["Asha", "Verma"]},
                          "mustKeep": ["Kafka", "Acme Payments", "PostgreSQL"]},
            "matches": {"java": {"R1": "MATCHED", "R2": "MATCHED", "R3": "MATCHED", "R4": "MISSING"}}}
    (d / "cv-48/cvs/cv01").mkdir(parents=True)
    (d / "cv-48/cvs/cv01/document.docx").write_bytes(docx(CV_LINES))
    (d / "cv-48/cvs/cv01/gold.json").write_text(json.dumps(gold))
    tr = json.loads((FIX / "speech/transcription/expected_output.json").read_text())
    aa = json.loads((FIX / "ai/answer_assessment/request.json").read_text())["payload"]
    (d / "answers-160").mkdir()
    (d / "answers-160/a01.json").write_text(json.dumps({
        "role": "JAVA_BACKEND", "turnKind": "MAIN", "question": aa["question"],
        "segments": [s["text"] for s in tr["segments"]],
        "gold": {"TECHNICAL_SKILL": [4, 4], "PRACTICAL_EXPERIENCE": [4, 3]}}))
    return d


def args_for(task, root):
    return argparse.Namespace(task=task, offline=False, release=False, provider="mock", dataset_root=str(root),
                              model_slot="standard", prompt_version="1.0.0", no_report=True,
                              stt_model="small.en", stt_device="cpu", stt_compute="int8")


def values(task, root):
    import run
    rows, _ = run.evaluate(args_for(task, root))
    return {g.metric: (res, v) for _, g, res, v in rows}


def test_cv_parse_and_redaction_gates_compute(dataset):
    v = values("CV_PARSE", dataset)
    assert v["employer/title/start/end F1"] == ("PASS", 1.0)
    assert v["skills recall"][0] == "PASS" and v["total experience within ±3 months"] == ("PASS", 1.0)
    assert v["first-pass schema+semantic validity"] == ("PASS", 1.0)
    r = values("REDACTION", dataset)
    assert {k: x[0] for k, x in r.items()} == {k: "PASS" for k in r}


def test_cv_match_gates_compute(dataset):
    v = values("CV_MATCH", dataset)
    assert v["requirement status agreement with gold"] == ("PASS", 1.0)
    assert v["must-have false MISSING"] == ("PASS", 0.0)


def test_assessment_gates_compute(dataset):
    v = values("ANSWER_ASSESSMENT", dataset)
    assert v["within ±1 of gold median"] == ("PASS", 1.0) and v["null-vs-scored agreement"] == ("PASS", 1.0)


def test_missing_human_ratings_are_not_run(dataset):
    v = values("INTERVIEW_PLAN", dataset)
    assert v["human rating 'relevant and clear'"][0] == "NOT_RUN"
