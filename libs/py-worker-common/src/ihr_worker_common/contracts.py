"""Loads JSON Schemas from contracts/ and validates envelopes, payloads and artefacts (A.1)."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def contracts_dir() -> Path:
    env = os.environ.get("IHR_CONTRACTS_DIR")
    if env:
        return Path(env)
    # repo layout: <root>/libs/py-worker-common/src/ihr_worker_common/contracts.py
    return Path(__file__).resolve().parents[4] / "contracts"


class ContractError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:5]))
        self.errors = errors


@lru_cache(maxsize=None)
def _validator(rel_path: str, definition: str | None = None) -> Draft202012Validator:
    schema = json.loads((contracts_dir() / rel_path).read_text(encoding="utf-8"))
    if definition:
        sub = schema["$defs"][definition]
        schema = {"$schema": schema["$schema"], **sub}
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _check(validator: Draft202012Validator, instance: object) -> None:
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        raise ContractError([f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors])


def validate_envelope(message: dict) -> None:
    _check(_validator("events/envelope.v1.json"), message)


def validate_payload(topic: str, payload: dict) -> None:
    path = f"events/{topic}.v1.json"
    if not (contracts_dir() / path).exists():
        raise ContractError([f"no schema for topic {topic}"])
    _check(_validator(path), payload)


def validate_artefact(schema_version: str, instance: object, model_part: bool = False) -> None:
    """Validate a stored artefact, or (model_part=True) the model's raw JSON (§7)."""
    path = f"ai-schemas/{schema_version}.json"
    schema = json.loads((contracts_dir() / path).read_text(encoding="utf-8"))
    definition = "ModelOutput" if model_part and "ModelOutput" in schema.get("$defs", {}) else None
    _check(_validator(path, definition), instance)


def artefact_errors(schema_version: str, instance: object, model_part: bool = False) -> list[str]:
    try:
        validate_artefact(schema_version, instance, model_part)
    except ContractError as e:
        return e.errors
    return []
