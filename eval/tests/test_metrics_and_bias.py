"""Pure metric functions and counterfactual generators."""
import math

import pytest

import bias
import metrics as m


def test_precision_recall_and_f1():
    p, r = m.precision_recall(["Java", "Kafka", "Go"], ["java", "kafka", "Docker", "AWS"])
    assert (p, r) == (2 / 3, 0.5) and m.f1(p, r) == pytest.approx(4 / 7)


def test_employment_counts():
    gold = [{"employer": "Acme", "title": "Engineer", "startDate": "2020-01", "endDate": "PRESENT"},
            {"employer": "Globex", "title": "Intern", "startDate": "2019-01", "endDate": "2019-06"}]
    pred = [{"employer": "Acme Pvt", "title": "Engineer", "startDate": "2020-01", "endDate": "PRESENT"}]
    tp, npred, ngold = m.employment_counts(pred, gold)
    assert (tp, npred, ngold) == (3, 4, 8)


def test_kappas():
    assert m.cohen_kappa(["A", "B", "A", "B"], ["A", "B", "A", "B"]) == 1.0
    assert m.cohen_kappa(["A", "A", "B", "B"], ["A", "B", "A", "B"]) == 0.0
    assert m.quadratic_weighted_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0
    assert m.quadratic_weighted_kappa([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) < 0


def test_within_one_median_share():
    assert m.within_one([3, 4, 1], [4, 2, 1]) == pytest.approx(2 / 3)
    assert m.median([3, 4]) == 3.5 and math.isnan(m.share([]))


def test_wer_terms_percentile():
    assert m.wer([("we moved the batch to kafka", "we moved the settlement batch to kafka")]) == pytest.approx(1 / 7)
    assert m.term_accuracy("Spring Boot with Kafka", ["Kafka", "Spring Boot", "PostgreSQL"]) == (2, 3)
    assert m.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10


CV = """RAHUL SHARMA
Engineer | rahul@example.com
Experience
Acme | Senior Engineer | Apr 2022 - Present
Globex | Engineer | Jul 2018 - Mar 2022
Initech | Intern | Jan 2018 - Jun 2018
Education
IIT Bombay | B.Tech | 2014 - 2018"""


def test_name_swap_keeps_everything_else():
    v = bias.swap_name(CV, "Rahul Sharma", "Priya Sharma")
    assert v.startswith("PRIYA SHARMA") and "Rahul" not in v and "RAHUL" not in v
    assert v.split("\n")[1:] == CV.split("\n")[1:]          # everything except the name line is unchanged


def test_institution_and_age_and_gap():
    assert "GEC Nagpur" in bias.swap_institution(CV)
    older = bias.shift_graduation_years(CV, -15)
    assert "1999 - 2003" in older and "Apr 2022" in older and "Jul 2018" in older   # employment untouched
    gap = bias.add_career_gap(CV, 18)
    assert "Apr 2022 - Present" in gap and "Jan 2017 - Sep 2020" in gap and "Jul 2016 - Dec 2016" in gap


def test_cv_variants_labels_and_fit_score():
    labels = [l for l, _ in bias.cv_variants(CV, "Rahul Sharma")]
    assert {"INSTITUTION", "AGE:+15", "AGE:-15", "GAP"} <= set(labels) and sum(l.startswith("NAME:") for l in labels) >= 5
    reqs = [{"code": "A", "weight": 3}, {"code": "B", "weight": 3}, {"code": "C", "weight": 1}, {"code": "D", "weight": 1}]
    crit = [{"requirementCode": c, "status": s} for c, s in zip("ABCD", ["MATCHED", "PARTIAL", "MISSING", "UNKNOWN"])]
    assert round(100 * bias.cv_fit_score(crit, reqs)) == 56          # §8.3 worked example


def test_answer_variants():
    v = bias.answer_variants(["In my last project we used Kafka.", "It worked."])
    assert len(v) == 4 and v[0][1][0].startswith("My name is Priya and in my last project")
