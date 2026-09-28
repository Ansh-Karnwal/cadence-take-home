#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openai>=2.0.0",
#   "pydantic>=2.8.0",
# ]
# ///

"""Generalization probes: the reviewers' strings against old rules, HEAD rules, and the model.

The sample cannot measure what the model adds, because the rules were fitted
to it. The nearest held-out data in this repo is the set of probe strings the
two reviewers wrote without seeing the keyword lists. For each string this
script reports three answers:

- the keyword rules at the commit before that string's fix landed, imported
  from a temporary ``git worktree`` of that commit in a subprocess;
- the keyword rules at HEAD, through the same adapter;
- the model alone, asked with the cache off ``--runs`` times, through the same
  resolver calls the ``force_llm`` path makes. Reported as the majority label
  and the split.

A rules answer is graded by the decision offline mode would make: an UNPLACED
consent is not signed, an UNPLACED plan is not clear, and an UNKNOWN title is
OTHER. Deferrals are marked so a reader can see which answers come from that
default rather than a keyword. Medication names are graded as "recognized as an
anticoagulant" or not; the model answers them through the medication question.

    uv run scripts/probe_generalization.py --model gpt-4.1-mini --out results/probes-gpt-4.1-mini
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = "gpt-4.1-mini"  # mirrors the Makefile's MODEL default
FIRST_REVIEW_PREFIX = "b3940c6"
SECOND_REVIEW_PREFIX = "432bea9"

# (review, kind, text, drug or None, correct label, pre-fix commit)
# Correct labels: consent/plan "false" (not signed / not clear); titles a DocKind
# value; medications "recognized". Every string appears verbatim in the tests.
PROBES: list[tuple[str, str, str, str | None, str, str]] = [
    ("first", "plan", "Home meds reviewed incl. apixaban. Hold metformin morning of surgery; resume diet after.", "apixaban", "false", FIRST_REVIEW_PREFIX),
    ("first", "plan", "Cardiology to advise whether to hold or resume apixaban.", "apixaban", "false", FIRST_REVIEW_PREFIX),
    ("first", "plan", "Unclear whether to hold apixaban; will resume later.", "apixaban", "false", FIRST_REVIEW_PREFIX),
    ("first", "plan", "Patient on apixaban. Hold and resume per cardiology.", "apixaban", "false", FIRST_REVIEW_PREFIX),
    ("first", "consent", "Consent not signed.", None, "false", FIRST_REVIEW_PREFIX),
    ("first", "consent", "Patient has not signed consent.", None, "false", FIRST_REVIEW_PREFIX),
    ("first", "consent", "Consent to be signed on day of surgery.", None, "false", FIRST_REVIEW_PREFIX),
    ("first", "consent", "Consent form signed by witness; patient signature missing.", None, "false", FIRST_REVIEW_PREFIX),
    ("first", "title", "Anticoagulation Plan", None, "ANTICOAG_PLAN", FIRST_REVIEW_PREFIX),
    ("first", "title", "H & P", None, "H_AND_P", FIRST_REVIEW_PREFIX),
    ("first", "title", "Interval H&P", None, "H_AND_P", FIRST_REVIEW_PREFIX),
    ("first", "title", "H&P Addendum", None, "H_AND_P", FIRST_REVIEW_PREFIX),
    ("first", "title", "Consent Withdrawn", None, "OTHER", FIRST_REVIEW_PREFIX),
    ("second", "consent", "Consent form signed by surgeon; patient signature outstanding.", None, "false", SECOND_REVIEW_PREFIX),
    ("second", "consent", "Consent signed by patient. Patient later withdrew consent.", None, "false", SECOND_REVIEW_PREFIX),
    ("second", "consent", "Signed consent rescinded by patient on 2026-02-22.", None, "false", SECOND_REVIEW_PREFIX),
    ("second", "consent", "Patient consent form. Signed: ______________ Date: ________", None, "false", SECOND_REVIEW_PREFIX),
    ("second", "consent", "Patient signed consent for left knee arthroscopy; new consent required for planned procedure.", None, "false", SECOND_REVIEW_PREFIX),
    ("second", "medication", "Eliquis 5 mg BID", None, "recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "Xarelto 20 mg daily", None, "recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "Pradaxa 150 mg", None, "recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "Lovenox 40 mg SC daily", None, "recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "clopidogrel", None, "not recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "aspirin 81 mg", None, "not recognized", SECOND_REVIEW_PREFIX),
    ("second", "medication", "amlodipine", None, "not recognized", SECOND_REVIEW_PREFIX),
    ("second", "title", "Surgical and Anesthesia Consent", None, "CONSENT", SECOND_REVIEW_PREFIX),
]

# Runs inside a subprocess whose cwd is the code root being evaluated. It adapts
# to the three API shapes the history has, and reports where it imported from.
ADAPTER = r'''
import inspect, json, sys
from preop_triage import classify, rules
probes = json.load(sys.stdin)

def med(name):
    for mod, fn in ((classify, "anticoagulant_generic"), (rules, "anticoagulant_in"), (rules, "_anticoagulant_name")):
        if hasattr(mod, fn):
            return getattr(mod, fn)(name)
    raise RuntimeError("no anticoagulant matcher")

plan_takes_drug = len(inspect.signature(classify.judge_plan_clear).parameters) >= 2
out = []
for p in probes:
    kind, text, drug = p["kind"], p["text"], p["drug"]
    if kind == "consent":
        raw = classify.judge_consent_signed(text).value
        eff = {"POSITIVE": "true", "NEGATIVE": "false", "UNPLACED": "false"}[raw]
    elif kind == "plan":
        j = classify.judge_plan_clear(text, drug) if plan_takes_drug else classify.judge_plan_clear(text)
        raw = j.value
        eff = {"POSITIVE": "true", "NEGATIVE": "false", "UNPLACED": "false"}[raw]
    elif kind == "title":
        c = classify.classify_title(text)
        raw = c.outcome.value + (":" + c.kind.value if c.kind is not None else "")
        eff = c.kind.value if c.kind is not None else "OTHER"
    elif hasattr(classify, "classify_medication"):
        c = classify.classify_medication(text)
        raw = c.outcome.value + (":" + c.generic if c.generic else "")
        eff = "recognized" if c.outcome.value == "KNOWN_ANTICOAG" else "not recognized"
    else:
        found = med(text)
        raw = found or "none"
        eff = "recognized" if found else "not recognized"
    deferred = raw in ("UNPLACED", "UNKNOWN")
    out.append({"raw": raw, "effective": eff, "deferred": deferred})
print(json.dumps({"classify_file": classify.__file__, "answers": out}))
'''


def rules_answers(code_root: Path, probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "OPENAI_API_KEY")}
    proc = subprocess.run(
        [sys.executable, "-c", ADAPTER],
        cwd=code_root,
        env=env,
        input=json.dumps(probes),
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    imported = Path(result["classify_file"]).resolve()
    if not str(imported).startswith(str(code_root.resolve())):
        raise SystemExit(f"imported {imported}, expected code under {code_root}")
    return result["answers"]


def prefix_answers(commit: str, probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules answers from a temporary worktree of ``commit``, removed afterward."""

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / f"wt-{commit}"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), commit],
            cwd=ROOT, check=True, capture_output=True,
        )
        try:
            return rules_answers(worktree, probes)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=ROOT, capture_output=True)
            subprocess.run(["git", "worktree", "prune"], cwd=ROOT, capture_output=True)


def check_verbatim() -> None:
    tests = (ROOT / "tests" / "test_classify.py").read_text() + (ROOT / "tests" / "test_synthetic.py").read_text()
    missing = [text for _, _, text, _, _, _ in PROBES if text not in tests]
    if missing:
        raise SystemExit(f"probe strings not found verbatim in the tests: {missing}")


def model_answers(model: str, runs: int, probes: list[dict[str, Any]]) -> tuple[list[list[str]], Any]:
    from preop_triage.llm import FallbackCache, LLMResolver
    from preop_triage.settings import Settings

    resolver = LLMResolver(model=model, settings=Settings(no_cache=True), cache=FallbackCache())
    labels: list[list[str]] = [[] for _ in probes]
    for run in range(runs):
        print(f"run {run + 1}/{runs}", file=sys.stderr)
        for i, p in enumerate(probes):
            before = resolver.failures
            if p["kind"] == "medication":
                label = "recognized" if resolver.resolve_is_anticoagulant(p["text"]) else "not recognized"
            elif p["kind"] == "title":
                label = resolver.resolve_title(p["text"]).value
            elif p["kind"] == "consent":
                label = str(resolver.resolve_consent_signed(p["text"])).lower()
            else:
                label = str(resolver.resolve_plan_clear(p["text"], p["drug"])).lower()
            labels[i].append("ERROR" if resolver.failures > before else label)
    return labels, resolver


def direction(correct: str, answer: str) -> str:
    """How a wrong answer errs: toward clearing a patient, or toward a follow-up."""

    if answer == correct:
        return "correct"
    clears = {
        ("false", "true"),  # an unsigned consent or a vague plan accepted
        ("recognized", "not recognized"),  # an anticoagulant missed, so Rule 3 never runs
        ("recognized", "ERROR"),
        ("OTHER", "CONSENT"),  # a withdrawn consent counted as the surgical consent
    }
    return "wrongly clears" if (correct, answer) in clears else "over-flags"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", default=str(ROOT / "results" / "probes"))
    args = parser.parse_args()
    if args.runs <= 0:
        raise SystemExit("--runs must be > 0")

    check_verbatim()
    probes = [
        {"review": r, "kind": k, "text": t, "drug": d, "correct": c, "prefix_commit": pc}
        for r, k, t, d, c, pc in PROBES
    ]

    prefix: dict[int, dict[str, Any]] = {}
    for commit in sorted({p["prefix_commit"] for p in probes}):
        idx = [i for i, p in enumerate(probes) if p["prefix_commit"] == commit]
        for i, ans in zip(idx, prefix_answers(commit, [probes[i] for i in idx])):
            prefix[i] = ans
    head = rules_answers(ROOT, probes)
    labels, resolver = model_answers(args.model, args.runs, probes)

    for i, p in enumerate(probes):
        p["prefix_rules"] = prefix[i]
        p["prefix_correct"] = prefix[i]["effective"] == p["correct"]
        p["prefix_direction"] = direction(p["correct"], prefix[i]["effective"])
        p["head_rules"] = head[i]
        p["head_correct"] = head[i]["effective"] == p["correct"]
        dist = Counter(labels[i])
        majority = dist.most_common(1)[0][0]
        p["model"] = {"labels": labels[i], "split": dict(dist), "majority": majority}
        p["model_correct"] = majority == p["correct"]

    model_probes = [p for p in probes if p["model"] is not None]
    report = {
        "model": args.model,
        "runs": args.runs,
        "temperature_accepted": resolver.temperature_accepted,
        "api_calls": resolver.api_calls,
        "failures": resolver.failures,
        "totals": {
            "strings": len(probes),
            "prefix_rules_correct": sum(p["prefix_correct"] for p in probes),
            "head_rules_correct": sum(p["head_correct"] for p in probes),
            "model_strings": len(model_probes),
            "model_correct": sum(p["model_correct"] for p in model_probes),
            "prefix_wrongly_clears": sum(p["prefix_direction"] == "wrongly clears" for p in probes),
            "prefix_over_flags": sum(p["prefix_direction"] == "over-flags" for p in probes),
        },
        "probes": probes,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probes.json").write_text(json.dumps(report, indent=2) + "\n")
    write_markdown(out / "probes.md", report)
    t = report["totals"]
    print(
        f"prefix={t['prefix_rules_correct']}/{t['strings']} head={t['head_rules_correct']}/{t['strings']} "
        f"model={t['model_correct']}/{t['model_strings']} failures={report['failures']} "
        f"temperature_accepted={report['temperature_accepted']} api_calls={report['api_calls']}"
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    t = report["totals"]
    esc = lambda s: s.replace("|", "\\|")  # noqa: E731
    lines = [
        "# Generalization probes",
        "",
        f"Model `{report['model']}`, {report['runs']} runs per string, cache off.",
        "",
        "| Answer source | Correct |",
        "|---|---|",
        f"| Keyword rules before each string's fix | {t['prefix_rules_correct']} of {t['strings']} |",
        f"| Keyword rules at HEAD | {t['head_rules_correct']} of {t['strings']} |",
        f"| Model alone (majority label) | {t['model_correct']} of {t['model_strings']} |",
        "",
        f"Pre-fix errors that wrongly clear a patient: {t['prefix_wrongly_clears']}. "
        f"Pre-fix errors that over-flag: {t['prefix_over_flags']}.",
        "",
        "| Review | Kind | String | Correct | Pre-fix rules (commit) | HEAD rules | Model majority (split) |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in report["probes"]:
        def cell(ans: dict[str, Any], ok: bool) -> str:
            mark = "ok" if ok else "WRONG"
            defer = ", deferred" if ans["deferred"] else ""
            return f"{ans['effective']} ({ans['raw']}{defer}) {mark}"
        if p["model"] is None:
            model_cell = "n/a (no model question)"
        else:
            split = ", ".join(f"{k} x{v}" for k, v in sorted(p["model"]["split"].items()))
            model_cell = f"{p['model']['majority']} ({split}) {'ok' if p['model_correct'] else 'WRONG'}"
        drug = f" [drug: {p['drug']}]" if p["drug"] else ""
        lines.append(
            f"| {p['review']} | {p['kind']} | {esc(p['text'])}{drug} | {p['correct']} | "
            f"{cell(p['prefix_rules'], p['prefix_correct'])} ({p['prefix_commit']}) | "
            f"{cell(p['head_rules'], p['head_correct'])} | {model_cell} |"
        )
    lines += [
        "",
        "A rules answer is graded by the decision offline mode would make: an UNPLACED consent",
        "is not signed, an UNPLACED plan is not clear, an UNKNOWN title is OTHER. Deferred",
        "answers are marked; an UNKNOWN medication is not recognized. The model answers",
        "medication names through the medication question. The pre-fix rules come from a",
        "git worktree of the named commit.",
        "",
    ]
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
