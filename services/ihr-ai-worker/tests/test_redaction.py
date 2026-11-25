"""§5.2 redaction: every pattern with positive and negative cases, plus property tests."""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ihr_ai_worker.privacy.redaction import COUNT_KEYS, redact, redact_transcript


def r(text, **kw):
    return redact(text, **kw)


def test_unicode_normalise_removes_zero_width_and_controls():
    out = r("Ja​va\x07 ﬁle")
    assert out.text == "Java file"


@pytest.mark.parametrize("text", ["mail asha.verma+cv@example.co.in now", "x@y.io"])
def test_email(text):
    out = r(text)
    assert "[EMAIL]" in out.text and "@" not in out.text and out.counts["EMAIL"] == 1


def test_email_negative():
    assert r("Twitter handle @ashav and C@ alone").counts["EMAIL"] == 0


@pytest.mark.parametrize("text", ["see https://example.com/x?a=1", "www.asha.dev", "linkedin.com/in/asha",
                                  "github.com/asha-v", "medium.com/@asha"])
def test_url(text):
    out = r(text)
    assert "[URL]" in out.text and out.counts["URL"] == 1


def test_url_negative():
    assert r("Node.js and ASP.NET").counts["URL"] == 0


@pytest.mark.parametrize("text", ["Call +91 98765 43210", "Mobile: 9876543210", "Tel (022) 2345-6789-01"])
def test_phone(text):
    out = r(text)
    assert "[PHONE]" in out.text and out.counts["PHONE"] >= 1


@pytest.mark.parametrize("text", ["Worked 2014 - 2018 at X", "Version 10.2.1.4 released", "Batch 2018-2022",
                                  "Reduced latency 120ms to 45ms", "Ticket 12345"])
def test_phone_negative(text):
    out = r(text)
    assert out.counts["PHONE"] == 0 and out.text == text


@pytest.mark.parametrize("text,count", [("Aadhaar 2345 6789 0123", 1), ("PAN ABCDE1234F", 1),
                                        ("Passport J8369854", 1)])
def test_gov_ids(text, count):
    out = r(text, cv=False)
    assert "[GOV_ID]" in out.text and out.counts["GOV_ID"] == count and out.counts["PHONE"] == 0


def test_gov_id_negative():
    out = r("Aadhaar starts 1234 5678 9012; product SKU ABCDE12345")
    assert out.counts["GOV_ID"] == 0


@pytest.mark.parametrize("line", ["Date of Birth: 01/02/1995", "DOB - 1995", "Marital Status: Single",
                                  "Father's Name: X", "Religion : Y", "Languages Known: English",
                                  "Address – Flat 2", "Gender: F", "Blood Group: O+"])
def test_personal_detail_lines_removed(line):
    out = r(f"Skills\n{line}\nJava")
    assert out.text == "Skills\nJava" and out.counts["LINES_REMOVED"] == 1


def test_personal_lines_negative():
    text = "Agency work for category managers\nAge of empires mod (hobby project)"
    assert r(text).text == text


def test_address_line_with_pin_removed():
    out = r("Java\n12 MG Road, Pune 411001\nKafka")
    assert out.text == "Java\nKafka" and out.counts["LINES_REMOVED"] == 1


@pytest.mark.parametrize("line", ["Release 202401 of the platform", "Order 411001 shipped", "Flat 2 on MG Road", "Sector growth 12%"])
def test_address_negative(line):
    assert r(line).text == line


def test_declaration_block_removed_when_near_end():
    text = "Java\nDeclaration\nI hereby declare the above is true.\nPlace: Pune"
    out = r(text)
    assert out.text == "Java" and out.counts["LINES_REMOVED"] == 3


def test_declaration_far_from_end_kept():
    text = "Declaration of skills\n" + "\n".join(f"line {i}" for i in range(20))
    assert r(text).text.startswith("Declaration of skills")


def test_candidate_name_phrase_and_tokens():
    out = r("Asha Verma\nASHA led the team. Verma's code.", names=("Asha Verma",))
    assert "Asha" not in out.text and "Verma" not in out.text.replace("[CANDIDATE]", "")
    assert out.text.startswith("[CANDIDATE]\n") and out.counts["CANDIDATE"] == 3


def test_candidate_short_tokens_and_substrings_kept():
    out = r("Al Ng worked on Alpha and Nginx", names=("Al Ng",))
    assert out.text == "[CANDIDATE] worked on Alpha and Nginx"


def test_dob_anywhere():
    out = r("I was born on 3rd March 1995 in a city", cv=False)
    assert "[DOB]" in out.text and out.counts["DOB"] == 1


def test_transcript_skips_cv_only_steps():
    out = redact_transcript("Date of Birth: 1995\nDeclaration", names=())
    assert out.counts["LINES_REMOVED"] == 0 and "Declaration" in out.text


def test_counts_have_all_keys():
    assert set(r("x").counts) == set(COUNT_KEYS)


# ---- property tests ------------------------------------------------------------------------------
_safe = st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll"), max_codepoint=0x7a), min_size=0,
                max_size=40)


@settings(max_examples=200)
@given(st.text(max_size=300))
def test_idempotent(text):
    once = redact(text, names=("Asha Verma",)).text
    assert redact(once, names=("Asha Verma",)).text == once


@settings(max_examples=200)
@given(_safe, _safe, st.from_regex(r"[a-z]{3,8}\.[a-z]{2,6}@[a-z]{3,8}\.(com|in|io)", fullmatch=True))
def test_email_never_survives(a, b, email):
    assert email not in redact(f"{a} {email} {b}").text


@settings(max_examples=200)
@given(_safe, st.from_regex(r"[6-9]\d{9}", fullmatch=True))
def test_indian_mobile_never_survives(a, number):
    assert number not in redact(f"{a} +91 {number} end", cv=False).text


@settings(max_examples=200)
@given(st.text(alphabet="abcdefghij klmnop\n", max_size=200))
def test_plain_text_unchanged(text):
    assert redact(text, cv=False).text == text
