"""Evaluation metrics for the §13.3 gates. Pure functions, unit-tested (eval/tests)."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

# ---- text normalisation -------------------------------------------------------------------------


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


# ---- set / field metrics ------------------------------------------------------------------------


def precision_recall(predicted: list[str], gold: list[str]) -> tuple[float, float]:
    """Set precision / recall on normalised strings. Empty predicted -> precision 1.0 (nothing wrong)."""
    p, g = {norm(x) for x in predicted if norm(x)}, {norm(x) for x in gold if norm(x)}
    tp = len(p & g)
    return (tp / len(p) if p else 1.0), (tp / len(g) if g else 1.0)


def f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


EMPLOYMENT_FIELDS = ("employer", "title", "startDate", "endDate")


def employment_counts(predicted: list[dict], gold: list[dict]) -> tuple[int, int, int]:
    """Field-level (tp, n_predicted_fields, n_gold_fields) for employer/title/start/end.

    Roles are aligned greedily by employer+title similarity; each aligned pair contributes one TP per equal field.
    """
    def key(e):
        return set(norm(f"{e.get('employer') or ''} {e.get('title') or ''}").split())

    def jacc(a, b):
        return len(a & b) / len(a | b) if a | b else 0.0

    pairs, used = [], set()
    for gi, g in enumerate(gold):
        best, best_s = None, 0.3
        for pi, p in enumerate(predicted):
            if pi in used:
                continue
            s = jacc(key(p), key(g))
            if s > best_s:
                best, best_s = pi, s
        if best is not None:
            used.add(best)
            pairs.append((predicted[best], g))
    tp = sum(1 for p, g in pairs for f in EMPLOYMENT_FIELDS if norm(str(p.get(f))) == norm(str(g.get(f))))
    n_pred = sum(1 for p in predicted for f in EMPLOYMENT_FIELDS if p.get(f) is not None)
    n_gold = sum(1 for g in gold for f in EMPLOYMENT_FIELDS if g.get(f) is not None)
    return tp, n_pred, n_gold


def micro_f1(counts: list[tuple[int, int, int]]) -> float:
    tp, npred, ngold = (sum(c[i] for c in counts) for i in range(3))
    return f1(tp / npred if npred else 1.0, tp / ngold if ngold else 1.0)


def share(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else math.nan


# ---- agreement metrics --------------------------------------------------------------------------


def agreement(pred: list, gold: list) -> float:
    return share([p == g for p, g in zip(pred, gold, strict=True)])


def cohen_kappa(pred: list, gold: list) -> float:
    n = len(pred)
    if n == 0:
        return math.nan
    po = agreement(pred, gold)
    cp, cg = Counter(pred), Counter(gold)
    pe = sum(cp[k] * cg[k] for k in set(cp) | set(cg)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def quadratic_weighted_kappa(pred: list[int], gold: list[int], lo: int = 1, hi: int = 5) -> float:
    k = hi - lo + 1
    n = len(pred)
    if n == 0:
        return math.nan
    obs = [[0] * k for _ in range(k)]
    for p, g in zip(pred, gold, strict=True):
        obs[p - lo][g - lo] += 1
    hp = [sum(obs[i]) for i in range(k)]
    hg = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2
            num += w * obs[i][j]
            den += w * hp[i] * hg[j] / n
    return 1.0 if den == 0 else 1 - num / den


def within_one(pred: list[int], gold: list[float]) -> float:
    return share([abs(p - g) <= 1 for p, g in zip(pred, gold, strict=True)])


def median(values: list[float]) -> float:
    v = sorted(values)
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


# ---- speech -------------------------------------------------------------------------------------


def wer_counts(hyp: str, ref: str) -> tuple[int, int]:
    """(word edit distance, reference word count)."""
    h, r = norm(hyp).split(), norm(ref).split()
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1], len(r)


def wer(pairs: list[tuple[str, str]]) -> float:
    errs = refs = 0
    for hyp, ref in pairs:
        e, n = wer_counts(hyp, ref)
        errs, refs = errs + e, refs + n
    return errs / refs if refs else math.nan


def term_accuracy(hyp: str, terms: list[str]) -> tuple[int, int]:
    """(terms present in the hypothesis, terms expected) — whole-phrase, normalised."""
    h = f" {norm(hyp)} "
    found = sum(1 for t in terms if f" {norm(t)} " in h)
    return found, len(terms)


def percentile(values: list[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return math.nan
    idx = min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))
    return v[idx]
