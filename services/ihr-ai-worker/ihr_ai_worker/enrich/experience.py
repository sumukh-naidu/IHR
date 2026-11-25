"""Total experience by interval merge (A-18). Month precision, inclusive months.

"PRESENT" = month of the parse; overlapping jobs counted once; internships counted;
entries with unknown dates are excluded (EMPLOYMENT_DATES added to missingOrAmbiguousFields).
"""
from __future__ import annotations

import re

_YM = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def month_index(value: str | None, current_month: str) -> int | None:
    if value is None:
        return None
    if value == "PRESENT":
        value = current_month
    m = _YM.match(value)
    return int(m.group(1)) * 12 + int(m.group(2)) - 1 if m else None


def role_interval(entry: dict, current_month: str) -> tuple[int, int] | None:
    s = month_index(entry.get("startDate"), current_month)
    e = month_index(entry.get("endDate"), current_month)
    if s is None or e is None or s > e:
        return None
    return s, e


def merged_months(intervals: list[tuple[int, int]]) -> int:
    total, cur = 0, None
    for s, e in sorted(intervals):
        if cur is None:
            cur = [s, e]
        elif s <= cur[1] + 1:
            cur[1] = max(cur[1], e)
        else:
            total += cur[1] - cur[0] + 1
            cur = [s, e]
    if cur is not None:
        total += cur[1] - cur[0] + 1
    return total


def experience(history: list[dict], current_month: str) -> tuple[int | None, int | None, bool]:
    """Return (totalExperienceMonths, mostRecentRoleMonths, any_undated)."""
    intervals, undated = [], False
    for entry in history:
        iv = role_interval(entry, current_month)
        if iv is None:
            undated = True
        else:
            intervals.append(iv)
    total = merged_months(intervals) if intervals else None
    recent = None
    if history:
        iv = role_interval(history[0], current_month)
        recent = iv[1] - iv[0] + 1 if iv else None
    return total, recent, undated


def skill_months(history: list[dict], skill: str, current_month: str, canonical) -> int | None:
    """Months of dated roles whose skillsMentioned include the skill (A-20 skill-specific evidence)."""
    target = canonical(skill).lower()
    ivs = [iv for e in history
           if any(canonical(s).lower() == target for s in e.get("skillsMentioned") or [])
           and (iv := role_interval(e, current_month)) is not None]
    return merged_months(ivs) if ivs else None


PARTIAL_SHARE = 0.75


def experience_status(months: int | None, min_months: int) -> str:
    """A-20: >= min -> MATCHED; >= 75 % -> PARTIAL; below -> MISSING; no dated history -> UNKNOWN.
    Exceeding experienceMaxMonths is never penalised."""
    if months is None:
        return "UNKNOWN"
    if months >= min_months:
        return "MATCHED"
    if months >= PARTIAL_SHARE * min_months:
        return "PARTIAL"
    return "MISSING"
