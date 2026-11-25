"""Prompt immutability and release freeze (A-11, §15 "prompts frozen v1.0.0 → release tags").

docs/prompts/prompts.lock.json records the sha256 of every prompt version file. Rules (checked in CI):
  - a recorded version file must never change (new behaviour = new version file);
  - every version in active.yaml must be recorded;
  - when frozen, active.yaml must equal the frozen release set (changes need an explicit re-freeze).

  python tools/prompt_lock.py --check            # CI
  python tools/prompt_lock.py --update           # record newly added version files
  python tools/prompt_lock.py --freeze --release v1.0.0   # after the evaluation report + calibration sign-off
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

PROMPTS = Path(__file__).resolve().parents[1] / "docs" / "prompts"
LOCK = PROMPTS / "prompts.lock.json"


def _files() -> dict[str, str]:
    return {f"{p.parent.name}/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(PROMPTS.glob("*/v*.md"))}


def _active() -> dict[str, str]:
    return {k: str(v) for k, v in yaml.safe_load((PROMPTS / "active.yaml").read_text()).items()}


def load() -> dict:
    return json.loads(LOCK.read_text()) if LOCK.exists() else {"files": {}, "frozen": None}


def check(lock: dict) -> list[str]:
    errors = []
    current = _files()
    for name, digest in lock["files"].items():
        if name not in current:
            errors.append(f"{name}: recorded prompt version was deleted")
        elif current[name] != digest:
            errors.append(f"{name}: released prompt file changed in place; create a new version instead")
    for pid, ver in _active().items():
        if f"{pid}/v{ver}.md" not in lock["files"]:
            errors.append(f"active {pid} {ver} is not recorded in prompts.lock.json (run --update)")
    frozen = lock.get("frozen")
    if frozen and frozen["active"] != _active():
        errors.append(f"prompts are frozen at release {frozen['release']}; active.yaml differs (re-freeze needed)")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--update", action="store_true")
    g.add_argument("--freeze", action="store_true")
    ap.add_argument("--release", help="release tag for --freeze, e.g. v1.0.0")
    a = ap.parse_args()
    lock = load()
    if a.check:
        errs = check(lock)
        for e in errs:
            print("ERROR", e)
        print("prompts.lock: OK" if not errs else f"prompts.lock: {len(errs)} problem(s)")
        return 1 if errs else 0
    changed = [n for n, d in lock["files"].items() if _files().get(n, d) != d]
    if changed:
        print("refusing: recorded files changed in place:", changed)
        return 1
    lock["files"] = {**_files(), **{k: v for k, v in lock["files"].items() if k in _files()}}
    if a.freeze:
        if not a.release:
            ap.error("--freeze needs --release")
        lock["frozen"] = {"release": a.release, "active": _active()}
    LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(f"recorded {len(lock['files'])} prompt files" + (f"; frozen at {a.release}" if a.freeze else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
