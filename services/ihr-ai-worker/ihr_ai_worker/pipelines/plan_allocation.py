"""Question allocation (A-25, §7.3) and the fallback bank (§8.2). Pure functions."""
from __future__ import annotations

import re

MAX_PER_COMPETENCY = 3
FIRST_SLOT = "PRACTICAL_EXPERIENCE"

# Fallback question types per standard competency (ASSUMPTIONS #26); custom competencies -> SCENARIO
FALLBACK_TYPE = {"TECHNICAL_SKILL": "TECHNICAL_CONCEPT", "PROBLEM_SOLVING": "PROBLEM_SOLVING",
                 "PRACTICAL_EXPERIENCE": "PAST_EXPERIENCE", "COMMUNICATION": "SCENARIO", "ROLE_FIT": "ROLE_MOTIVATION"}


def allocate(competencies: list[dict], n: int) -> list[str]:
    """Return N slot competency codes in interview order."""
    weight = {c["code"]: float(c["weight"]) for c in competencies}
    order = [c["code"] for c in competencies]                                # request order breaks final ties
    by_weight = sorted(order, key=lambda code: (-weight[code], order.index(code)))
    slots = [code for code in by_weight if next(c for c in competencies if c["code"] == code)["mandatory"]][:n]
    quota = {code: weight[code] / 100 * n for code in order}
    count = {code: slots.count(code) for code in order}
    while len(slots) < n:
        candidates = [code for code in order if count[code] < MAX_PER_COMPETENCY]
        if not candidates:
            break
        best = max(candidates, key=lambda code: (max(0.0, quota[code] - count[code]), weight[code], -order.index(code)))
        slots.append(best)
        count[best] += 1
    return interleave(slots, weight, order)


def interleave(slots: list[str], weight: dict[str, float], order: list[str]) -> list[str]:
    left = {code: slots.count(code) for code in dict.fromkeys(slots)}
    out: list[str] = []
    if FIRST_SLOT in left:
        out.append(FIRST_SLOT)
        left[FIRST_SLOT] -= 1
    while len(out) < len(slots):
        avail = [c for c, k in left.items() if k > 0]
        pick = [c for c in avail if not out or c != out[-1]] or avail          # avoid repeats where possible
        best = max(pick, key=lambda c: (left[c], weight[c], -order.index(c)))
        out.append(best)
        left[best] -= 1
    return out


def _points(text: str) -> list[str]:
    parts = re.split(r",\s*|\s+and\s+", text)
    return [p.strip().rstrip(".") for p in parts if p.strip()]


def generic_rubric(anchors: dict) -> dict:
    """§7.3/§8.2: keyPoints = anchor 4 and 5 descriptions split into points (3-6)."""
    points: list[str] = []
    for p in _points(anchors["4"]) + _points(anchors["5"]):
        if p.lower() not in (x.lower() for x in points):
            points.append(p[:200])
    while len(points) < 3:
        points.append(anchors["3"][:200] if len(points) == 0 else anchors[str(4 + (len(points) % 2))][:200])
    return {"keyPoints": points[:6], "strongSignals": [anchors["5"][:200]], "weakSignals": [anchors["2"][:200]]}


def fallback_question(comp: dict, used: set[str], max_answer_seconds: int, slot: int) -> dict | None:
    for text in comp["fallbackQuestions"]:
        if text not in used:
            used.add(text)
            return {"slot": slot, "text": text, "competencyCodes": [comp["code"]],
                    "questionType": FALLBACK_TYPE.get(comp["code"], "SCENARIO"),
                    "expectedAnswerRubric": generic_rubric(comp["anchors"]),
                    "timeBudgetSeconds": min(360, max(60, max_answer_seconds)), "cvAnchor": None,
                    "source": "FALLBACK_BANK"}
    return None
