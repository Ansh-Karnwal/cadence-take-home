"""Regenerate results/SUMMARY.md from the JSON reports in each run folder.

Every number comes from a run's eval_report.json and determinism_report.json.
To add a run, append its folder name under results/ to RUNS and re-run:

    python3 scripts/summarize_results.py
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"

# Folder names under results/, in table order.
RUNS = ["naive", "naive-gpt-6-luna"]

METRICS = [
    "json_schema_valid",
    "decision_match_oracle",
    "issue_categories_match_oracle",
    "issues_value_grounding",
]


def build_summary(results: Path = RESULTS) -> str:
    header = (
        ["Run", "Model", "Primary score (%)"]
        + [f"{m} (%)" for m in METRICS]
        + ["Decision stability (%)", "JSON stability (%)", "Exact-output match (%)"]
    )
    lines = [
        "# Naive baseline results",
        "",
        "Generated from the JSON reports in each run folder. Scores are percentages.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    notes = []
    for run in RUNS:
        d = results / run
        ev = json.loads((d / "eval_report.json").read_text())
        det = json.loads((d / "determinism_report.json").read_text())
        rows = [
            json.loads(line)
            for line in (d / "outputs.jsonl").read_text().splitlines()
            if line.strip()
        ]
        models = {r["model"] for r in rows}
        assert models == {det["model"]}, (run, models, det["model"])
        s = ev["local_metrics_summary"]
        cells = (
            [run, det["model"], ev["primary_score"]["value_pct"]]
            + [s[f"{m}_rate_pct"] for m in METRICS]
            + [
                det["decision_stability_pct"],
                det["json_format_stability_pct"],
                det["exact_output_match_pct"],
            ]
        )
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
        for r in rows:
            if r["error"]:
                notes.append(
                    f"- `{run}`: {r['case_id']} returned a response that failed "
                    "`TriageOutput` validation; the harness scored it as schema-invalid."
                )
    lines.append("")
    lines.append(
        "Determinism uses record 0 (case_00000), 10 calls. Run folders hold "
        "outputs.jsonl, eval_report.json, and determinism_report.json."
    )
    if notes:
        lines += ["", "Notes:", ""] + notes
    return "\n".join(lines) + "\n"


def main() -> None:
    summary = build_summary()
    (RESULTS / "SUMMARY.md").write_text(summary)
    print(summary, end="")


if __name__ == "__main__":
    main()
