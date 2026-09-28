#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openai>=2.0.0",
#   "pydantic>=2.8.0",
# ]
# ///

"""Measure the LLM fallback's stability and its agreement with the rules.

Collects every distinct title, every distinct consent text, and every distinct
(plan text, drug) pair in the dataset, asks the model about each one ``--runs``
times with the cache off, and writes ``stability.json`` and ``stability.md``.

Items are collected the way the engine sees them: consent texts come from
documents the rules classify as consents, and plan pairs come from Rule 3's
candidate documents for each active anticoagulant. The rules' own label is
recorded where the rules place the item (REQUIRED or KNOWN_OTHER titles;
POSITIVE or NEGATIVE texts); UNKNOWN titles and UNPLACED texts have none.

Uses its own resolver with ``no_cache`` and a private cache, so the engine's
shared cache is never touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import PatientSubmission  # noqa: E402
from preop_triage.classify import (  # noqa: E402
    DocKind,
    OfflineResolver,
    TextJudgment,
    TitleOutcome,
    classify_title,
    judge_consent_signed,
    judge_plan_clear,
)
from preop_triage.llm import FallbackCache, LLMResolver  # noqa: E402
from preop_triage.rules import (  # noqa: E402
    active_anticoagulants,
    classify_documents,
    plan_candidates,
)
from preop_triage.settings import Settings  # noqa: E402

DEFAULT_MODEL = "gpt-4.1-mini"  # mirrors the Makefile's MODEL default
ERROR = "ERROR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model id")
    parser.add_argument("--runs", type=int, default=5, help="Runs per item")
    parser.add_argument(
        "--input",
        default=str(ROOT / "data" / "patients_sample_50.jsonl"),
        help="Dataset JSONL path",
    )
    parser.add_argument("--out", default=str(ROOT / "results" / "stability"), help="Output folder")
    return parser.parse_args()


def _text_rule_label(judgment: TextJudgment) -> str | None:
    if judgment is TextJudgment.POSITIVE:
        return "true"
    if judgment is TextJudgment.NEGATIVE:
        return "false"
    return None


def collect_items(path: Path) -> list[dict[str, Any]]:
    """Distinct items per question type, each with its rule label or None."""

    titles: dict[str, set[str]] = {}
    consents: dict[str, set[str]] = {}
    plans: dict[tuple[str, str], set[str]] = {}
    offline = OfflineResolver()

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = row["case_id"]
            submission = PatientSubmission.model_validate(row["submission"])
            kinds = classify_documents(submission, offline)
            for doc, kind in zip(submission.documents, kinds):
                if doc.type and doc.type.strip():
                    titles.setdefault(doc.type, set()).add(case_id)
                if kind is DocKind.CONSENT and doc.text and doc.text.strip():
                    consents.setdefault(doc.text, set()).add(case_id)
            for _, drug, _ in active_anticoagulants(submission):
                by_text, by_title = plan_candidates(submission, kinds, drug)
                for i in by_text + by_title:
                    text = submission.documents[i].text
                    if text and text.strip():
                        plans.setdefault((text, drug), set()).add(case_id)

    items: list[dict[str, Any]] = []
    for title, cases in sorted(titles.items()):
        result = classify_title(title)
        if result.outcome is TitleOutcome.REQUIRED and result.kind is not None:
            rule = result.kind.value
        elif result.outcome is TitleOutcome.KNOWN_OTHER:
            rule = DocKind.OTHER.value
        else:
            rule = None
        items.append({"question": "title", "text": title, "rule_label": rule, "cases": sorted(cases)})
    for text, cases in sorted(consents.items()):
        items.append(
            {
                "question": "consent",
                "text": text,
                "rule_label": _text_rule_label(judge_consent_signed(text)),
                "cases": sorted(cases),
            }
        )
    for (text, drug), cases in sorted(plans.items()):
        items.append(
            {
                "question": f"plan:{drug}",
                "text": text,
                "drug": drug,
                "rule_label": _text_rule_label(judge_plan_clear(text, drug)),
                "cases": sorted(cases),
            }
        )
    return items


def ask(resolver: LLMResolver, item: dict[str, Any]) -> str:
    before = resolver.failures
    if item["question"] == "title":
        label: str = resolver.resolve_title(item["text"]).value
    elif item["question"] == "consent":
        label = str(resolver.resolve_consent_signed(item["text"])).lower()
    else:
        label = str(resolver.resolve_plan_clear(item["text"], item["drug"])).lower()
    return ERROR if resolver.failures > before else label


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    stable = 0
    placed = 0
    agree = 0
    disagreements = []
    by_type: dict[str, Counter[str]] = {}
    for item in items:
        labels = item["labels"]
        distribution = Counter(labels)
        item["distribution"] = dict(distribution)
        item["modal_label"] = distribution.most_common(1)[0][0]
        item["self_agreement"] = len(distribution) == 1
        qtype = item["question"].split(":", 1)[0]
        counts = by_type.setdefault(qtype, Counter())
        counts["items"] += 1
        stable += item["self_agreement"]
        counts["stable"] += item["self_agreement"]
        if item["rule_label"] is not None:
            placed += 1
            counts["rule_placed"] += 1
            matches = item["modal_label"] == item["rule_label"]
            agree += matches
            counts["rule_agree"] += matches
            if not matches:
                disagreements.append(
                    {
                        "question": item["question"],
                        "text": item["text"],
                        "rule_label": item["rule_label"],
                        "model_labels": item["distribution"],
                        "cases": item["cases"],
                    }
                )
    return {
        "items": len(items),
        "self_agreement_pct": round(100.0 * stable / len(items), 2) if items else 0.0,
        "rule_placed_items": placed,
        "rules_agreement_pct": round(100.0 * agree / placed, 2) if placed else 0.0,
        "by_question_type": {k: dict(v) for k, v in sorted(by_type.items())},
        "disagreements": disagreements,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    s = report["summary"]
    lines = [
        "# LLM fallback stability",
        "",
        f"Model `{report['model']}`, {report['runs']} runs per item, cache off.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| Items | {s['items']} |",
        f"| Self-agreement (identical labels on every run) | {s['self_agreement_pct']}% |",
        f"| Items the rules placed | {s['rule_placed_items']} |",
        f"| Agreement with the rules (modal label equals rule label) | {s['rules_agreement_pct']}% |",
        f"| Temperature 0 accepted | {report['temperature_accepted']} |",
        f"| API calls made | {report['api_calls']} |",
        f"| Failed items (offline answer used) | {report['failures']} |",
        "",
        "## By question type",
        "",
        "| Question | Items | Stable | Rule-placed | Agree with rules |",
        "|---|---|---|---|---|",
    ]
    for qtype, c in s["by_question_type"].items():
        lines.append(
            f"| {qtype} | {c.get('items', 0)} | {c.get('stable', 0)} | "
            f"{c.get('rule_placed', 0)} | {c.get('rule_agree', 0)} |"
        )
    lines += ["", "## Disagreements with the rules", ""]
    if not s["disagreements"]:
        lines.append("None.")
    else:
        lines += ["| Question | Text | Rule label | Model labels | Cases |", "|---|---|---|---|---|"]
        for d in s["disagreements"]:
            text = d["text"].replace("|", "\\|")
            labels = ", ".join(f"{k}: {v}" for k, v in sorted(d["model_labels"].items()))
            lines.append(
                f"| {d['question']} | {text} | {d['rule_label']} | {labels} | "
                f"{', '.join(d['cases'][:3])}{' ...' if len(d['cases']) > 3 else ''} |"
            )
    unstable = [i for i in report["items"] if not i["self_agreement"]]
    lines += ["", "## Items with unstable labels", ""]
    if not unstable:
        lines.append("None.")
    else:
        lines += ["| Question | Text | Labels |", "|---|---|---|"]
        for i in unstable:
            labels = ", ".join(f"{k}: {v}" for k, v in sorted(i["distribution"].items()))
            lines.append(f"| {i['question']} | {i['text'].replace('|', '\\|')} | {labels} |")
    lines += [
        "",
        "Rules agreement counts only items the rules place: REQUIRED and KNOWN_OTHER",
        "titles, and POSITIVE or NEGATIVE texts. UNKNOWN titles and UNPLACED texts have",
        "no rule label. An ERROR label means the call failed and the offline answer",
        "would have been used.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.runs <= 0:
        raise SystemExit("--runs must be > 0")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    items = collect_items(Path(args.input))
    resolver = LLMResolver(
        model=args.model, settings=Settings(no_cache=True), cache=FallbackCache()
    )
    for item in items:
        item["labels"] = []
    for run in range(args.runs):
        print(f"run {run + 1}/{args.runs}: {len(items)} items", file=sys.stderr)
        for item in items:
            item["labels"].append(ask(resolver, item))

    report = {
        "model": args.model,
        "runs": args.runs,
        "temperature_accepted": resolver.temperature_accepted,
        "api_calls": resolver.api_calls,
        "failures": resolver.failures,
        "summary": summarize(items),
        "items": items,
    }
    (out / "stability.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(out / "stability.md", report)
    s = report["summary"]
    print(
        f"items={s['items']} self_agreement={s['self_agreement_pct']}% "
        f"rules_agreement={s['rules_agreement_pct']}% "
        f"disagreements={len(s['disagreements'])} temperature_accepted="
        f"{report['temperature_accepted']} api_calls={report['api_calls']} "
        f"failures={report['failures']}"
    )
    print(f"Wrote {out / 'stability.json'} and {out / 'stability.md'}")


if __name__ == "__main__":
    main()
