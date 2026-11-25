"""§9 question policy (A-21) and language policy (A-22)."""
import pytest

from ihr_ai_worker.policy import language, questions


@pytest.mark.parametrize("q", [
    "Which cloud platforms have you deployed production services to?",
    "How did you design the health check for the settlement service?",
    "How would you avoid a race condition when two consumers update one record?",
    "How do you integrate third-party payment APIs safely?",
])
def test_good_questions_pass(q):
    assert questions.violations(q) == []


@pytest.mark.parametrize("q,rule", [
    ("Too short?", "LENGTH"),
    ("x" * 301 + "?", "LENGTH"),
    ("Why? How? When? Where did you build it?", "MULTIPLE_QUESTIONS"),
    ("Can [CANDIDATE] explain the Kafka migration in detail?", "PERSONAL_DATA"),
    ("Are you married or planning a family soon?", "PROTECTED_TOPIC"),
    ("How old were you when you started programming?", "PROTECTED_TOPIC"),
    ("What is your current salary and notice period?", "PROTECTED_TOPIC"),
    ("Where are you from originally and what is your mother tongue?", "PROTECTED_TOPIC"),
    ("Did you really build the settlement service yourself?", "ACCUSATORY"),
    ("Please upload your Aadhaar card before the interview.", "DOCUMENT_REQUEST"),
])
def test_bad_questions_fail(q, rule):
    assert rule in questions.violations(q)


def test_followup_length_and_name_and_competency():
    assert "LENGTH" in questions.violations("x" * 201 + "?", followup=True)
    assert "PERSONAL_DATA" in questions.violations("Rahul, tell me about your Kafka work please.", name_tokens=("Rahul",))
    assert "OFF_COMPETENCY" in questions.violations("Tell me about a time you tuned a slow query.",
                                                    competency_codes=["C9"], allowed_codes={"C1", "C2"})


@pytest.mark.parametrize("text,rule", [
    ("The candidate should be hired for this role.", "HIRING_RECOMMENDATION"),
    ("Strong hire based on the CV.", "HIRING_RECOMMENDATION"),
    ("Not suitable for backend work.", "HIRING_RECOMMENDATION"),
    ("The CV appears fabricated in places.", "INTEGRITY_JUDGEMENT"),
    ("Answers suggest they used ChatGPT.", "INTEGRITY_JUDGEMENT"),
    ("Speaks with a strong accent.", "PROTECTED_ATTRIBUTE"),
    ("Seemed nervous during the answer.", "PROTECTED_ATTRIBUTE"),
    ("The candidate is married with children.", "PROTECTED_ATTRIBUTE"),
])
def test_language_policy_fails(text, rule):
    assert rule in language.violations(text)


@pytest.mark.parametrize("text", [
    "The CV shows Java backend and Kafka event-streaming work in payments over two roles.",
    "No stated AWS experience; the health endpoint design is described.",
    "Built a leader election module and integrated third-party APIs.",
])
def test_language_policy_passes(text):
    assert language.violations(text) == []
