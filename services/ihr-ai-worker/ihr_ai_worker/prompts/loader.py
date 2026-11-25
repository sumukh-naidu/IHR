"""Prompt loader (A-11, §4.2).

- docs/prompts/{promptId}/v{MAJOR.MINOR.PATCH}.md with YAML front-matter
  (promptId, version, task, outputSchema, modelSlot, temperature, maxTokens, changelog).
- docs/prompts/active.yaml lists the active version of every prompt; only those are loaded.
- System message (§4.2) = base_rules + task instructions + "Output schema:" + compact JSON
  Schema of the model output + one short example output.
- promptSha256 = sha256 of the assembled system template (before per-request variables
  such as {maxAnswerMinutes} are filled), so it identifies the prompt, not the request.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ihr_worker_common import contracts

BASE_RULES_ID = "base_rules"
_SECTION = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_VAR = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")
REQUIRED_META = ("promptId", "version", "task", "outputSchema", "modelSlot", "temperature", "maxTokens", "changelog")


class PromptError(Exception):
    pass


def _compact(obj: object) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


_OPEN, _CLOSE = "\x00", "\x01"   # stand-ins for literal braces in embedded JSON


def _protect(text: str) -> str:
    return text.replace("{", _OPEN).replace("}", _CLOSE)


def _restore(text: str) -> str:
    return text.replace(_OPEN, "{").replace(_CLOSE, "}")


def fill(template: str, variables: dict[str, object]) -> str:
    """Replace {name} placeholders; every placeholder must have a value."""
    missing = sorted({m for m in _VAR.findall(template) if m not in variables})
    if missing:
        raise PromptError(f"missing template variables: {', '.join(missing)}")
    return _restore(_VAR.sub(lambda m: str(variables[m.group(1)]), template))


def parse_prompt_file(path: Path) -> tuple[dict, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise PromptError(f"{path}: missing front-matter")
    _, fm, body = text.split("---\n", 2)
    meta = yaml.safe_load(fm) or {}
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise PromptError(f"{path}: front-matter missing {missing}")
    parts = _SECTION.split(body)
    sections = {parts[i].strip(): parts[i + 1].strip("\n") for i in range(1, len(parts), 2)}
    return meta, sections


def model_output_schema(schema_doc: dict) -> dict:
    """The part of an artefact schema the model returns ($defs.ModelOutput if present)."""
    model = (schema_doc.get("$defs") or {}).get("ModelOutput")
    if model is not None:
        return model
    return {k: v for k, v in schema_doc.items() if k not in ("$schema", "$id", "title", "$defs")}


@dataclass(frozen=True)
class Prompt:
    prompt_id: str
    version: str
    task: str
    output_schema: str
    model_slot: str
    temperature: float
    max_tokens: int
    system_template: str                 # assembled; may still hold {variables}
    user_template: str
    variants: dict[str, str] = field(default_factory=dict)  # extra system sections, e.g. WARMUP/CLOSING
    sha256: str = ""

    def system(self, **variables: object) -> str:
        return fill(self.system_template, variables)

    def user(self, **variables: object) -> str:
        return fill(self.user_template, variables)


@dataclass
class PromptRegistry:
    prompts: dict[str, Prompt]

    def get(self, prompt_id: str) -> Prompt:
        try:
            return self.prompts[prompt_id]
        except KeyError:
            raise PromptError(f"prompt {prompt_id!r} is not active") from None


def _example(prompt_id: str, fixtures_root: Path) -> dict | None:
    path = fixtures_root / prompt_id / "kimi_response.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_registry(active_file: str | Path, fixtures_root: Path | None = None) -> PromptRegistry:
    active_file = Path(active_file)
    root = active_file.parent
    fixtures_root = fixtures_root or contracts.contracts_dir() / "fixtures" / "ai"
    active = yaml.safe_load(active_file.read_text(encoding="utf-8")) or {}
    if BASE_RULES_ID not in active:
        raise PromptError("active.yaml must list base_rules")

    _, base_sections = parse_prompt_file(root / BASE_RULES_ID / f"v{active[BASE_RULES_ID]}.md")
    base = base_sections["System"]

    prompts: dict[str, Prompt] = {}
    for prompt_id, version in active.items():
        if prompt_id == BASE_RULES_ID:
            continue
        meta, sections = parse_prompt_file(root / prompt_id / f"v{version}.md")
        if meta["promptId"] != prompt_id or str(meta["version"]) != str(version):
            raise PromptError(f"{prompt_id}: front-matter does not match active.yaml")
        if "System" not in sections or "User" not in sections:
            raise PromptError(f"{prompt_id}: needs '## System' and '## User' sections")
        schema_path = contracts.contracts_dir() / "ai-schemas" / f"{meta['outputSchema']}.json"
        schema = model_output_schema(json.loads(schema_path.read_text(encoding="utf-8")))
        parts = [base, sections["System"], "Output schema:\n" + _compact(schema)]
        example = _example(prompt_id, fixtures_root)
        if example is not None:
            parts.append("Example output:\n" + _compact(example))
        # Schema/example JSON contains braces: protect them from {variable} filling.
        system_template = "\n\n".join(parts[:2] + [_protect(p) for p in parts[2:]])
        variants = {k: v for k, v in sections.items() if k not in ("System", "User")}
        prompts[prompt_id] = Prompt(
            prompt_id=prompt_id, version=str(version), task=meta["task"], output_schema=meta["outputSchema"],
            model_slot=meta["modelSlot"], temperature=float(meta["temperature"]), max_tokens=int(meta["maxTokens"]),
            system_template=system_template, user_template=sections["User"], variants=variants,
            sha256=hashlib.sha256(_restore(system_template).encode("utf-8")).hexdigest(),
        )
    return PromptRegistry(prompts)

