"""Loads docs/prompts/policy_lexicon.yaml (§9)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


def lexicon_path() -> Path:
    env = os.environ.get("POLICY_LEXICON_FILE")
    if env:
        return Path(env)
    active = os.environ.get("PROMPTS_ACTIVE_FILE")
    if active and (Path(active).parent / "policy_lexicon.yaml").exists():
        return Path(active).parent / "policy_lexicon.yaml"
    for base in (Path("/app/prompts"), Path(__file__).resolve().parents[4] / "docs" / "prompts"):
        if (base / "policy_lexicon.yaml").exists():
            return base / "policy_lexicon.yaml"
    raise FileNotFoundError("policy_lexicon.yaml not found")


@dataclass(frozen=True)
class Lexicon:
    terms: dict[str, re.Pattern]          # category -> word-boundary pattern
    whitelist: re.Pattern | None
    language_extra: re.Pattern

    def protected_hits(self, text: str) -> list[str]:
        masked = self.whitelist.sub(" ", text) if self.whitelist else text
        return [cat for cat, pat in self.terms.items() if pat.search(masked)]


def _alt(terms, regex=False) -> re.Pattern:
    parts = [t if regex else r"\s+".join(map(re.escape, t.split())) for t in sorted(terms, key=len, reverse=True)]
    return re.compile(r"(?i)(?<![\w-])(?:" + "|".join(parts) + r")(?![\w])")


@lru_cache(maxsize=1)
def load() -> Lexicon:
    data = yaml.safe_load(lexicon_path().read_text(encoding="utf-8"))
    terms = {cat: _alt(words) for cat, words in data["categories"].items()}
    wl = data.get("whitelist") or []
    whitelist = re.compile(r"(?i)" + "|".join(re.escape(w) for w in sorted(wl, key=len, reverse=True))) if wl else None
    return Lexicon(terms, whitelist, _alt(data.get("language_extra") or [], regex=True))
