#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

"""Check that the test suite catches deliberate bugs (mutation testing, by hand).

Copies the repository (without .git and results) to a temporary folder,
applies one mutation at a time to that copy, runs the test suite there with
no API key, and records whether the suite failed. The working tree is never
modified. Each mutation is an exact, unique string replacement; a mutation
whose target text is missing is reported as not applied.

    uv run scripts/mutation_check.py [--out results/mutation]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_TIMEOUT_SECONDS = 300

TEST_COMMAND = [
    "uv", "run",
    "--with", "openai>=2.0.0",
    "--with", "pydantic>=2.8.0",
    "--with", "pytest>=8.0.0",
    "python", "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider",
]

# (name, file, original text, mutated text)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # Dates and ordering
    (
        "window boundary made exclusive",
        "preop_triage/normalize.py",
        "return 0 <= days_before(item_date, procedure_date) <= max_days",
        "return 0 <= days_before(item_date, procedure_date) < max_days",
    ),
    (
        "tie goes to the earlier list index",
        "preop_triage/normalize.py",
        "if best is None or (instant, index) >= best:",
        "if best is None or instant > best[0]:",
    ),
    (
        "offset converted to UTC for day arithmetic",
        "preop_triage/normalize.py",
        "    if isinstance(parsed, datetime):\n        return parsed.date()",
        "    if isinstance(parsed, datetime):\n"
        "        return parsed.astimezone(UTC).date() if parsed.tzinfo else parsed.date()",
    ),
    # Keywords and classification
    (
        "plan negative 'no clear' removed",
        "preop_triage/classify.py",
        '    "no clear",  # case_00004',
        "    # removed",
    ),
    (
        "consent clause splitting disabled",
        "preop_triage/classify.py",
        '_CLAUSE_SPLIT_RE = re.compile(r"[.;:]")',
        '_CLAUSE_SPLIT_RE = re.compile(r"\\x00")',
    ),
    (
        "consent title exclusions disabled",
        "preop_triage/classify.py",
        "        if _CONSENT_EXCLUSION_RE.search(core) and not _SURGICAL_RE.search(core):",
        "        if False:",
    ),
    # Rules
    (
        "systolic threshold made strict",
        "preop_triage/rules.py",
        "systolic is not None and systolic >= SYSTOLIC_LIMIT",
        "systolic is not None and systolic > SYSTOLIC_LIMIT",
    ),
    (
        "temperature threshold made inclusive",
        "preop_triage/rules.py",
        "elif value_f > TEMPERATURE_LIMIT_F:",
        "elif value_f >= TEMPERATURE_LIMIT_F:",
    ),
    (
        "BP exclusion details lose the date",
        "preop_triage/rules.py",
        'f"systolic>={SYSTOLIC_LIMIT} or diastolic>={DIASTOLIC_LIMIT}; "\n'
        "                    f\"recorded {getattr(vital, 'date', None)}\",",
        'f"systolic>={SYSTOLIC_LIMIT} or diastolic>={DIASTOLIC_LIMIT}",',
    ),
    (
        "lab status filter removed",
        "preop_triage/rules.py",
        "if canonical_lab_code(lab.code) == code and _lab_counts(lab)",
        "if canonical_lab_code(lab.code) == code",
    ),
    (
        "oldest consent judged instead of newest",
        "preop_triage/rules.py",
        "    index = _latest_document(documents, consent_indexes)",
        "    index = consent_indexes[0]",
    ),
    (
        "only the first anticoagulant checked",
        "preop_triage/rules.py",
        "    for med_index, drug, full_name, by_model in active:",
        "    for med_index, drug, full_name, by_model in active[:1]:",
    ),
    (
        "anticoagulation details quote the matched word, not the full name",
        "preop_triage/rules.py",
        'f"{full_name}) but no clear',
        'f"{drug}) but no clear',
    ),
    (
        "force_llm skips the consent text",
        "preop_triage/rules.py",
        "        or (force_llm and has_text)",
        "        or False",
    ),
    (
        "keyword-positive consents not confirmed in default mode",
        "preop_triage/rules.py",
        "        or (keyword_positive and confirm_positives)",
        "        or False",
    ),
    (
        "failed consent confirmation ignores the keyword answer",
        "preop_triage/rules.py",
        "fallback=keyword_positive)",
        "fallback=False)",
    ),
    (
        "inactive medications classified and sent",
        "preop_triage/rules.py",
        "        if med.active is False:\n            continue\n",
        "",
    ),
    (
        "model 'yes' on an unknown medication ignored",
        "preop_triage/rules.py",
        "            if ask(name):\n                found.append((i, medication_identity(name), name, med.active, True))",
        "            ask(name)",
    ),
    (
        "known-other medications sent to the model",
        "preop_triage/rules.py",
        "        elif result.outcome is MedicationOutcome.UNKNOWN and resolver is not None:",
        "        elif resolver is not None:",
    ),
    (
        "unparseable dates not flagged",
        "preop_triage/rules.py",
        "            if raw is not None and parse_date(raw) is None:",
        "            if False:",
    ),
    (
        "brand names ignored",
        "preop_triage/classify.py",
        "    for name in (generic, *brands)",
        "    for name in (generic,)",
    ),
    (
        "consent title naming surgery still excluded",
        "preop_triage/classify.py",
        "if _CONSENT_EXCLUSION_RE.search(core) and not _SURGICAL_RE.search(core):",
        "if _CONSENT_EXCLUSION_RE.search(core):",
    ),
    (
        "bare consent negative 'withdrew' removed",
        "preop_triage/classify.py",
        '    "withdrew",\n',
        "",
    ),
    (
        "force_llm skips rule-negative plans",
        "preop_triage/rules.py",
        "                force_llm and bool(text and text.strip())",
        "                False",
    ),
    # Engine and LLM layer
    (
        "force_llm honored in offline mode",
        "preop_triage/engine.py",
        "    force = settings.force_llm and not settings.offline",
        "    force = settings.force_llm",
    ),
    (
        "strict schema turned off",
        "preop_triage/llm.py",
        '"strict": True,',
        '"strict": False,',
    ),
    (
        "three retries instead of two",
        "preop_triage/llm.py",
        "MAX_RETRIES = 2",
        "MAX_RETRIES = 3",
    ),
    (
        "cache never written",
        "preop_triage/llm.py",
        "            self.cache.put(self.model, question, cache_text, label)",
        "            pass",
    ),
    (
        "temperature rejection not recorded",
        "preop_triage/llm.py",
        "                    self.temperature_accepted = False\n",
        "                    pass\n",
    ),
]


COLLECT_COMMAND = TEST_COMMAND[: TEST_COMMAND.index("-q")] + ["--collect-only", "-q"]


def collect_counts(workdir: Path) -> dict[str, int]:
    """Per-file test counts from pytest's collection of the unmutated suite."""

    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    proc = subprocess.run(COLLECT_COMMAND, cwd=workdir, env=env, capture_output=True, text=True)
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "::" in line:
            name = line.split("::", 1)[0]
            counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def run_tests(workdir: Path) -> tuple[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    try:
        proc = subprocess.run(
            TEST_COMMAND,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return "timeout", ""
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    return ("passed" if proc.returncode == 0 else "failed"), tail


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "results" / "mutation"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(
            ROOT,
            work,
            ignore=shutil.ignore_patterns(".git", "results", "__pycache__", ".pytest_cache", ".venv"),
        )
        baseline_status, baseline_tail = run_tests(work)
        if baseline_status != "passed":
            raise SystemExit(f"unmutated suite did not pass: {baseline_tail}")
        test_counts = collect_counts(work)
        print(f"unmutated: {baseline_tail}", file=sys.stderr)

        results = []
        for name, rel, original, mutated in MUTATIONS:
            path = work / rel
            source = path.read_text()
            if source.count(original) != 1:
                results.append({"mutation": name, "file": rel, "outcome": "not applied"})
                print(f"NOT APPLIED  {name}", file=sys.stderr)
                continue
            path.write_text(source.replace(original, mutated))
            try:
                status, tail = run_tests(work)
            finally:
                path.write_text(source)
            # A timeout also means the suite did not pass the mutant.
            caught = status in ("failed", "timeout")
            results.append(
                {"mutation": name, "file": rel, "outcome": "caught" if caught else "survived",
                 "test_status": status, "pytest_tail": tail}
            )
            print(f"{'CAUGHT  ' if caught else 'SURVIVED'} {name} ({status})", file=sys.stderr)

    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "unmutated_suite": baseline_tail,
        "tests_per_file": test_counts,
        "tests_total": sum(test_counts.values()),
        "mutations": len(results),
        "caught": sum(r["outcome"] == "caught" for r in results),
        "survived": sum(r["outcome"] == "survived" for r in results),
        "not_applied": sum(r["outcome"] == "not applied" for r in results),
        "results": results,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mutation_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"mutations={report['mutations']} caught={report['caught']} "
        f"survived={report['survived']} not_applied={report['not_applied']}"
    )
    if report["survived"] or report["not_applied"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
