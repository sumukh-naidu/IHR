"""Dataset layout (A-31, §13.1) and loaders. Synthetic or consenting-volunteer data only.

eval/datasets/
  cv-48/
    rubrics/{rubricId}.json        {"job": {title, summary, experienceMinMonths, experienceMaxMonths},
                                    "requirements": [cv.match requirement objects]}
    cvs/{cvId}/document.pdf|docx|doc
    cvs/{cvId}/gold.json           {
        "role": "JAVA_BACKEND" | "DATA_ANALYST" | "HR_EXECUTIVE" | "SUPPORT_LEAD",
        "knownName": "Asha Verma" | null,           # redactionHints.fullName
        "currentMonth": "2027-02",                  # month "PRESENT" resolves to (A-18), keeps gold stable
        "profile": {"employmentHistory": [{"employer", "title", "startDate", "endDate"}],
                    "skills": ["Java", ...], "totalExperienceMonths": 102},
        "redaction": {"mustRemove": {"EMAIL": [...], "PHONE": [...], "GOV_ID": [...], "NAME_TOKENS": [...]},
                      "mustKeep": ["Kafka", "Acme Payments", ...]},
        "matches": {"{rubricId}": {"R1": "MATCHED", "R2": "PARTIAL", ...}},   # gold after 2 raters + adjudication
        "twinOf": "{cvId}" | null                   # injection CV: id of its clean twin
    }
  answers-160/{answerId}.json      {
        "role": "...", "turnKind": "MAIN",
        "question": {"text", "competencyCodes", "expectedAnswerRubric": {keyPoints, strongSignals, weakSignals}},
        "segments": ["first segment text", "second", ...],      # written answers split into segments
        "gold": {"TECHNICAL_SKILL": [4, 3], "COMMUNICATION": [null, null]},   # one value per HR rater, null = not assessed
        "twinOf": "{answerId}" | null
    }
  answers-160/followup_ratings.csv answerId,useful            (1/0, filled after a provider run)
  audio-40/{clipId}.webm|ogg|wav   + {clipId}.json {"reference": "...", "accentGroup": "south", "terms": ["Kafka", ...]}
  plans-20/{planId}.json           ihr.interview.plan.requested payload + optional "profile" (cv_profile.v1) for anchors
  plans-20/ratings.csv             planId,questionId,relevantAndClear  (1/0, human, after a provider run)
  reports/ratings.csv              reportId,statements,unsupported     (human check of 20 reports)
  tts/pronunciation.csv            questionId,acceptable               (human, 100 technical questions)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

DATASETS = Path(__file__).resolve().parent / "datasets"
DOC_NAMES = ("document.pdf", "document.docx", "document.doc")
AUDIO_EXT = (".webm", ".ogg", ".wav", ".mp3", ".m4a")


class MissingDataset(Exception):
    """The dataset (or human ratings file) is not there yet -> the gate is NOT_RUN, never PASS."""


def _root(root: Path | None) -> Path:
    return root or DATASETS


def cvs(root: Path | None = None) -> list[dict]:
    base = _root(root) / "cv-48" / "cvs"
    out = []
    for d in sorted(base.glob("*")) if base.exists() else []:
        doc = next((d / n for n in DOC_NAMES if (d / n).exists()), None)
        if doc and (d / "gold.json").exists():
            out.append({"id": d.name, "document": doc, "gold": json.loads((d / "gold.json").read_text())})
    if not out:
        raise MissingDataset("cv-48")
    return out


def rubrics(root: Path | None = None) -> dict[str, dict]:
    base = _root(root) / "cv-48" / "rubrics"
    out = {p.stem: json.loads(p.read_text()) for p in sorted(base.glob("*.json"))} if base.exists() else {}
    if not out:
        raise MissingDataset("cv-48/rubrics")
    return out


def answers(root: Path | None = None) -> list[dict]:
    base = _root(root) / "answers-160"
    out = [{"id": p.stem, **json.loads(p.read_text())} for p in sorted(base.glob("*.json"))] if base.exists() else []
    if not out:
        raise MissingDataset("answers-160")
    return out


def audio(root: Path | None = None) -> list[dict]:
    base = _root(root) / "audio-40"
    out = []
    for meta in sorted(base.glob("*.json")) if base.exists() else []:
        clip = next((meta.with_suffix(e) for e in AUDIO_EXT if meta.with_suffix(e).exists()), None)
        if clip:
            out.append({"id": meta.stem, "audio": clip, **json.loads(meta.read_text())})
    if not out:
        raise MissingDataset("audio-40")
    return out


def plans(root: Path | None = None) -> list[dict]:
    base = _root(root) / "plans-20"
    out = [{"id": p.stem, **json.loads(p.read_text())} for p in sorted(base.glob("*.json"))] if base.exists() else []
    if not out:
        raise MissingDataset("plans-20")
    return out


def ratings(rel: str, root: Path | None = None) -> list[dict]:
    path = _root(root) / rel
    if not path.exists():
        raise MissingDataset(rel)
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise MissingDataset(rel)
    return rows
