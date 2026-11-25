"""Skill canonicalisation (A-19): contracts/ai-schemas/skill_aliases.json, unknown skills kept as written."""
from __future__ import annotations

import json
from functools import lru_cache

from ihr_worker_common.contracts import contracts_dir


@lru_cache(maxsize=1)
def alias_map() -> dict[str, str]:
    data = json.loads((contracts_dir() / "ai-schemas" / "skill_aliases.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for canonical, aliases in data.items():
        if canonical.startswith("$"):
            continue
        for name in [canonical, *aliases]:
            out[" ".join(name.lower().split())] = canonical
    return out


def canonical(name: str) -> str:
    key = " ".join(name.lower().split())
    return alias_map().get(key, " ".join(name.split()))


def canonicalise(names: list[str]) -> list[dict]:
    groups: dict[str, list[str]] = {}
    for n in names:
        c = canonical(n)
        originals = groups.setdefault(c, [])
        if n not in originals:
            originals.append(n)
    return [{"name": c, "originalNames": o} for c, o in groups.items()]
