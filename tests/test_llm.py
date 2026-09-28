"""LLM fallback layer, with a fake client that records calls. No network.

The fake (``conftest.FakeClient``) stands in for ``openai.OpenAI``; nothing is
mocked at the HTTP layer. The autouse fixture resets the module-level cache
and resolver registry before every test and blocks the real client.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from core import triage_submission
from preop_triage import engine, llm
from preop_triage.classify import DocKind
from preop_triage.llm import LLMResolver, TextLabel, TitleLabel, cache_stats
from preop_triage.settings import Settings

DEFAULT = Settings()
NO_CACHE = Settings(no_cache=True)
FORCE = Settings(force_llm=True, no_cache=True)
MODEL = "test-model"

RULE_3_VERBATIM = (
    "A documented perioperative anticoagulation management plan is required prior to scheduling.",
    "The plan must clearly describe how the medication will be managed before and after the procedure.",
)


def _resolver(settings: Settings = DEFAULT) -> LLMResolver:
    return llm.get_resolver(MODEL, settings)


def _run(submission: dict, settings: Settings = DEFAULT, model: str = MODEL):  # type: ignore[no-untyped-def]
    return triage_submission(submission=submission, model=model, settings=settings)


def _canonical(output) -> str:  # type: ignore[no-untyped-def]
    return json.dumps(output.model_dump(), sort_keys=True, separators=(",", ":"))


# --- Schemas and prompts ------------------------------------------------------


def test_schemas_are_flat_strict_and_match_the_reply_models() -> None:
    for schema, model in ((llm.TITLE_SCHEMA, TitleLabel), (llm.TEXT_SCHEMA, TextLabel)):
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(model.model_fields)
        assert set(schema["properties"]) == set(model.model_fields)
        assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    assert llm.TITLE_SCHEMA["properties"]["kind"]["enum"] == [kind.value for kind in DocKind]


def test_every_call_passes_the_strict_schema_and_temperature_zero(fake_client) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver()
    resolver.resolve_title("Medical Clearance [PDF]")
    resolver.resolve_consent_signed("Consent reviewed with patient.")
    resolver.resolve_plan_clear("Patient takes apixaban.", "apixaban")

    assert len(fake_client.calls) == 3
    expected = [llm.TITLE_SCHEMA, llm.TEXT_SCHEMA, llm.TEXT_SCHEMA]
    for call, schema in zip(fake_client.calls, expected):
        fmt = call["text"]["format"]
        assert fmt["type"] == "json_schema"
        assert fmt["strict"] is True
        assert fmt["schema"] == schema
        assert call["temperature"] == 0
        assert call["model"] == MODEL


def test_title_prompt_receives_only_the_title(fake_client) -> None:  # type: ignore[no-untyped-def]
    _resolver().resolve_title("Preop Medical Clearance [PDF]")
    (call,) = fake_client.calls
    assert call["input"] == "Preop Medical Clearance [PDF]"


def test_title_prompt_lists_three_kinds_with_two_dataset_examples_each() -> None:
    prompt = llm.TITLE_PROMPT
    for example in (
        "History and Physical",
        "Pre-op H&P [PDF]",
        "Surgical Consent",
        "Procedure Consent Form",
        "Perioperative Medication Plan",
        "Anticoag Plan (scanned)",
    ):
        assert f'"{example}"' in prompt
    assert "Everything else is OTHER." in prompt


def test_plan_prompt_quotes_rule_3_and_input_names_the_drug(fake_client) -> None:  # type: ignore[no-untyped-def]
    for bullet in RULE_3_VERBATIM:
        assert bullet in llm.PLAN_PROMPT
    _resolver().resolve_plan_clear("Hold before; resume after.", "rivaroxaban")
    (call,) = fake_client.calls
    assert "rivaroxaban" in call["input"]
    assert "Hold before; resume after." in call["input"]


def test_consent_prompt_wording() -> None:
    prompt = llm.CONSENT_PROMPT
    assert "true only if the text states that the patient has signed" in prompt
    assert "pending, missing, refused, or withdrawn means false" in prompt
    assert "If the text does not say, return false." in prompt


@pytest.mark.parametrize("prompt", [llm.TITLE_PROMPT, llm.CONSENT_PROMPT, llm.PLAN_PROMPT])
def test_every_prompt_ends_with_text_alone_and_asks_only_for_a_label(prompt: str) -> None:
    assert prompt.endswith("Answer from the text alone; do not infer.")
    # No request for prose: the reply schema holds a single label field.
    for forbidden in ("explain", "summar", "write", "decision", "reason"):
        assert forbidden not in prompt.lower()


def test_parsed_labels_are_returned(fake_client) -> None:  # type: ignore[no-untyped-def]
    fake_client.answer = lambda q, _i: "H_AND_P" if q == "title" else True
    resolver = _resolver()
    assert resolver.resolve_title("History & Phsyical") is DocKind.H_AND_P
    assert resolver.resolve_consent_signed("text") is True
    assert resolver.resolve_plan_clear("text", "apixaban") is True


# --- Cache --------------------------------------------------------------------


def test_second_identical_call_hits_the_cache(fake_client) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver()
    resolver.resolve_title("Medical Clearance [PDF]")
    resolver.resolve_title("Medical Clearance [PDF]")
    assert len(fake_client.calls) == 1
    assert cache_stats() == {
        "hits": 1,
        "misses": 1,
        "size": 1,
        "misses_by_question": {"title": 1},
    }


def test_cache_key_separates_question_and_drug(fake_client) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver()
    resolver.resolve_plan_clear("same text", "apixaban")
    resolver.resolve_plan_clear("same text", "warfarin")
    resolver.resolve_consent_signed("same text")
    assert len(fake_client.calls) == 3
    assert cache_stats()["size"] == 3


def test_cache_key_separates_models(fake_client) -> None:  # type: ignore[no-untyped-def]
    llm.get_resolver("model-a", DEFAULT).resolve_title("Medical Clearance [PDF]")
    llm.get_resolver("model-b", DEFAULT).resolve_title("Medical Clearance [PDF]")
    assert [c["model"] for c in fake_client.calls] == ["model-a", "model-b"]


def test_repeated_evaluation_makes_zero_new_calls(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    submission = dataset[0]["submission"]
    _run(submission)
    first = len(fake_client.calls)
    _run(submission)
    assert first > 0
    assert len(fake_client.calls) == first


def test_no_cache_calls_every_time_and_stores_nothing(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver(NO_CACHE)
    resolver.resolve_title("Medical Clearance [PDF]")
    resolver.resolve_title("Medical Clearance [PDF]")
    assert len(fake_client.calls) == 2

    submission = dataset[0]["submission"]
    _run(submission, NO_CACHE)
    per_run = len(fake_client.calls) - 2
    _run(submission, NO_CACHE)
    assert len(fake_client.calls) == 2 + 2 * per_run
    assert cache_stats() == {"hits": 0, "misses": 0, "size": 0, "misses_by_question": {}}


def test_resolver_is_held_at_module_level_per_model_and_settings() -> None:
    assert engine.build_resolver(model="m", settings=DEFAULT) is engine.build_resolver(
        model="m", settings=DEFAULT
    )
    assert engine.build_resolver(model="m", settings=DEFAULT) is not engine.build_resolver(
        model="other", settings=DEFAULT
    )


# --- Only what the rules cannot place, once per evaluation -------------------------


def test_case_00000_sends_one_title_one_consent_confirmation_and_one_plan(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    # The consent is keyword-POSITIVE, so default mode asks the model to confirm it.
    _run(dataset[0]["submission"], NO_CACHE)
    assert [c["input"] for c in fake_client.calls_for("title")] == [
        "Preop Pre-anesthesia Evaluation [PDF]"
    ]
    assert [c["input"] for c in fake_client.calls_for("consent")] == [
        "Electronic consent obtained and signed by patient for procedure."
    ]
    assert len(fake_client.calls_for("plan")) == 1


def test_each_title_classified_once_and_each_text_judged_once(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    # force_llm sends everything; no_cache makes every request visible.
    for row in dataset:
        fake_client.calls.clear()
        submission = row["submission"]
        _run(submission, FORCE)
        titles = [c["input"] for c in fake_client.calls_for("title")]
        expected_titles = [d["type"] for d in submission["documents"] if d["type"]]
        assert titles == expected_titles, row["case_id"]
        assert len(fake_client.calls_for("consent")) <= 1, row["case_id"]
        plan_inputs = [c["input"] for c in fake_client.calls_for("plan")]
        assert len(plan_inputs) == len(set(plan_inputs)), row["case_id"]


# --- force_llm ------------------------------------------------------------------


def test_force_llm_routes_rule_placed_title_and_consent(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    submission = dataset[12]["submission"]  # case_00012: READY offline
    assert _run(submission, Settings(offline=True)).decision == "READY"

    # The fake calls every title OTHER and every consent unsigned.
    output = _run(submission, FORCE)

    titles = [c["input"] for c in fake_client.calls_for("title")]
    assert any(re.search(r"H&P|History", t) for t in titles)
    assert len(fake_client.calls_for("consent")) == 0  # no CONSENT kind survives
    descriptions = {i.description for i in output.issues}
    assert "History and Physical document missing" in descriptions
    assert "Signed surgical consent missing" in descriptions


def test_force_llm_routes_rule_placed_consent_text(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    submission = dataset[12]["submission"]
    # Keep the titles as the rules would, but judge the consent unsigned.
    fake_client.answer = lambda q, i: (
        ("CONSENT" if "onsent" in i else "H_AND_P" if "H&P" in i or "History" in i else "OTHER")
        if q == "title"
        else False
    )
    output = _run(submission, FORCE)
    consent_calls = fake_client.calls_for("consent")
    assert len(consent_calls) == 1
    assert "signed" in consent_calls[0]["input"].lower()  # a rule-POSITIVE text
    assert "Surgical consent not clearly signed" in {i.description for i in output.issues}


def test_force_llm_sends_rule_negative_consent_to_the_resolver(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    # case_00008's consent is keyword-NEGATIVE ("unsigned"): final in default
    # mode, sent to the model only under force_llm.
    submission = dataset[8]["submission"]
    # Under force_llm the fake also labels every title; keep consents as consents.
    fake_client.answer = lambda q, i: (
        ("CONSENT" if "onsent" in i else "H_AND_P") if q == "title" else True
    )
    _run(submission, NO_CACHE)
    assert fake_client.calls_for("consent") == []
    _run(submission, FORCE)
    consent_calls = fake_client.calls_for("consent")
    assert len(consent_calls) == 1
    assert "unsigned" in consent_calls[0]["input"]


def test_force_llm_sends_rule_negative_plan_to_the_resolver(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    # case_00004's plan is rule-NEGATIVE ("no clear hold/resume guidance").
    submission = dataset[4]["submission"]
    _run(submission, NO_CACHE)
    assert fake_client.calls_for("plan") == []
    _run(submission, FORCE)
    plan_calls = fake_client.calls_for("plan")
    assert len(plan_calls) == 1
    assert "no clear hold/resume guidance" in plan_calls[0]["input"]


def test_force_llm_is_ignored_offline(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    output = _run(dataset[12]["submission"], Settings(offline=True, force_llm=True))
    assert output.decision == "READY"
    assert fake_client.calls == []


# --- Failures ------------------------------------------------------------------------


def test_api_failure_returns_offline_answer_and_does_not_raise(fake_client, capsys) -> None:  # type: ignore[no-untyped-def]
    fake_client.fail_next = 100
    resolver = _resolver()
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.OTHER
    assert resolver.resolve_consent_signed("Consent reviewed.") is False
    assert resolver.resolve_plan_clear("text", "apixaban") is False
    # One initial attempt plus two retries per item.
    assert len(fake_client.calls) == 9
    assert resolver.failures == 3
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 3
    assert "Medical Clearance [PDF]" in err[0] and "simulated API outage" in err[0]


def test_failed_answer_is_not_cached(fake_client) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver()
    fake_client.fail_next = 3
    resolver.resolve_title("Medical Clearance [PDF]")
    fake_client.answer = lambda q, _i: "H_AND_P"
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.H_AND_P


def test_two_retries_then_success(fake_client) -> None:  # type: ignore[no-untyped-def]
    fake_client.fail_next = 2
    fake_client.answer = lambda q, _i: "H_AND_P"
    resolver = _resolver()
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.H_AND_P
    assert len(fake_client.calls) == 3
    assert resolver.failures == 0


def test_malformed_reply_degrades_to_offline_answer(fake_client) -> None:  # type: ignore[no-untyped-def]
    fake_client.answer = lambda q, _i: "NOT_A_KIND"
    assert _resolver().resolve_title("Medical Clearance [PDF]") is DocKind.OTHER
    assert cache_stats()["size"] == 0


def test_engine_notes_failure_in_explanation_and_never_raises(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    fake_client.fail_next = 1000
    output = _run(dataset[0]["submission"])
    offline = _run(dataset[0]["submission"], Settings(offline=True))
    assert output.decision == offline.decision
    assert output.issues == offline.issues
    # Title, consent confirmation, and plan all failed; the consent confirmation
    # fell back to the keyword answer, so the issues match offline mode.
    assert "LLM fallback unavailable for 3 item(s)" in output.explanation


def test_client_construction_failure_is_recorded_once(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    attempts = []

    def broken_factory():  # type: ignore[no-untyped-def]
        attempts.append(1)
        raise RuntimeError("The api_key client option must be set")

    monkeypatch.setattr(llm, "_make_client", broken_factory)
    resolver = _resolver()
    assert resolver.resolve_title("A") is DocKind.OTHER
    assert resolver.resolve_title("B") is DocKind.OTHER
    assert attempts == [1]
    assert resolver.failures == 2


def test_temperature_rejection_retries_once_without_it_and_is_recorded(fake_client) -> None:  # type: ignore[no-untyped-def]
    fake_client.reject_temperature = True
    fake_client.answer = lambda q, _i: "H_AND_P"
    resolver = _resolver()
    assert resolver.resolve_title("Medical Clearance [PDF]") is DocKind.H_AND_P
    assert ["temperature" in c for c in fake_client.calls] == [True, False]
    assert resolver.temperature_accepted is False
    resolver.resolve_title("Another title")
    assert "temperature" not in fake_client.calls[-1]
    assert resolver.failures == 0


def test_temperature_accepted_is_recorded(fake_client) -> None:  # type: ignore[no-untyped-def]
    resolver = _resolver()
    assert resolver.temperature_accepted is None
    resolver.resolve_title("Medical Clearance [PDF]")
    assert resolver.temperature_accepted is True


# --- Determinism and default-mode outcome ------------------------------------------


def test_ten_call_determinism_on_case_00000(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    submission = dataset[0]["submission"]
    outputs = {_canonical(_run(copy.deepcopy(submission))) for _ in range(10)}
    assert len(outputs) == 1
    # Only the first evaluation reached the client: a title, a consent, a plan.
    assert len(fake_client.calls) == 3


def test_default_mode_differs_from_offline_only_on_case_00002(fake_client, dataset) -> None:  # type: ignore[no-untyped-def]
    # A model that reads the misspelled title as an H&P and nothing else as
    # required (D5), confirms signed consents, and finds no plan clear.
    fake_client.answer = lambda q, i: (
        ("H_AND_P" if i == "History & Phsyical" else "OTHER") if q == "title" else q == "consent"
    )
    differing = []
    for row in dataset:
        default = _run(row["submission"])
        offline = _run(row["submission"], Settings(offline=True))
        if _canonical(default) != _canonical(offline):
            differing.append(row["case_id"])
    assert differing == ["case_00002"]
    categories = {i.category for i in _run(dataset[2]["submission"]).issues}
    assert categories == {"ACUTE_SAFETY_EXCLUSION"}


def test_no_model_id_in_package_or_core() -> None:
    root = Path(__file__).resolve().parent.parent
    files = [root / "core.py", *sorted((root / "preop_triage").glob("*.py"))]
    pattern = re.compile(r"\bgpt-|\bo[134]-|\bclaude-", re.IGNORECASE)
    offenders = [str(f) for f in files if pattern.search(f.read_text())]
    assert offenders == []


# --- Consent confirmation and outages -------------------------------------------------


def test_outage_on_consent_confirmation_keeps_the_keyword_answer(fake_client) -> None:  # type: ignore[no-untyped-def]
    # Keyword-POSITIVE consent: the failed confirmation falls back to "signed".
    fake_client.fail_next = 1000
    submission = {
        "procedure": {"procedure_risk": "LOW", "procedure_date": "2026-03-03"},
        "vitals": [
            {"type": "blood_pressure", "systolic": 120, "diastolic": 80, "date": "2026-02-24"},
            {"type": "temperature", "value_f": 98.6, "date": "2026-02-24"},
        ],
        "labs": [{"code": "CBC", "effective_at": "2026-02-25", "status": "final"}],
        "documents": [
            {"type": "History and Physical", "date": "2026-02-20", "text": "H&P done."},
            {"type": "Surgical Consent", "date": "2026-02-21", "text": "Consent obtained; signature on file."},
        ],
    }
    output = _run(submission)
    assert output.issues == []
    assert "LLM fallback unavailable for 1 item(s)" in output.explanation

    # UNPLACED consent: the failed call falls back to "not signed".
    submission["documents"][1]["text"] = "Consent reviewed with patient."
    output = _run(submission)
    assert "Surgical consent not clearly signed" in {i.description for i in output.issues}
