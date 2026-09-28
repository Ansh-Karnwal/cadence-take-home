"""Regenerate results/SUMMARY.md from the reports in every results/*/ folder.

Every number comes from a committed report file:

- Run folders hold outputs.jsonl, eval_report.json, and determinism_report.json.
  Folders named ``naive*`` are the starter baseline (mode "naive"). Folders
  written by ``make -f Makefile.local final`` also hold run_info.json, whose
  PREOP_OFFLINE value gives the mode ("offline" or "default").
- Stability folders hold stability.json from scripts/llm_stability.py.

Other folders (for example results/mutation) are ignored. Run:

    python3 scripts/summarize_results.py
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS = Path(__file__).resolve().parent.parent / "results"

METRICS = [
    "json_schema_valid",
    "decision_match_oracle",
    "issue_categories_match_oracle",
    "issues_value_grounding",
]
MODE_ORDER = {"naive": 0, "offline": 1, "default": 2}
RUN_FILES = ("outputs.jsonl", "eval_report.json", "determinism_report.json")
TRUTHY = {"1", "true", "yes"}


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _mode(folder: Path) -> str:
    if folder.name.startswith("naive"):
        return "naive"
    info = folder / "run_info.json"
    if not info.exists():
        raise SystemExit(f"{folder}: no run_info.json and not a naive run; cannot tell its mode")
    offline = str(_load(info).get("preop_offline", "")).strip().lower() in TRUTHY
    return "offline" if offline else "default"


def _run_rows(results: Path) -> tuple[list[str], list[str]]:
    folders = [
        f for f in sorted(results.iterdir()) if f.is_dir() and all((f / n).exists() for n in RUN_FILES)
    ]
    folders.sort(key=lambda f: (MODE_ORDER[_mode(f)], f.name))
    rows, notes = [], []
    for folder in folders:
        mode = _mode(folder)
        ev = _load(folder / "eval_report.json")
        det = _load(folder / "determinism_report.json")
        outputs = [
            json.loads(line)
            for line in (folder / "outputs.jsonl").read_text().splitlines()
            if line.strip()
        ]
        models = {r["model"] for r in outputs}
        assert models == {det["model"]}, (folder.name, models, det["model"])
        model = "none (no LLM)" if mode == "offline" else det["model"]
        s = ev["local_metrics_summary"]
        cells = (
            [folder.name, mode, model, ev["primary_score"]["value_pct"]]
            + [s[f"{m}_rate_pct"] for m in METRICS]
            + [
                det["decision_stability_pct"],
                det["json_format_stability_pct"],
                det["exact_output_match_pct"],
            ]
        )
        rows.append("| " + " | ".join(str(c) for c in cells) + " |")
        failing = [
            f"{r['case_id']} ({', '.join(k for k, v in r['metrics'].items() if not v)})"
            for r in ev["records"]
            if not all(r["metrics"].values())
        ]
        notes.append(
            f"- `{folder.name}` failing cases: {'; '.join(failing) if failing else 'none'}."
        )
        for r in outputs:
            if r["error"]:
                notes.append(
                    f"- `{folder.name}`: {r['case_id']} returned a response that failed "
                    "`TriageOutput` validation; the harness scored it as schema-invalid."
                )
            elif "LLM fallback unavailable" in (r["output"] or {}).get("explanation", ""):
                notes.append(
                    f"- `{folder.name}`: {r['case_id']} used offline answers after an LLM failure."
                )
    return rows, notes


def _stability_rows(results: Path) -> tuple[list[str], list[str]]:
    rows, lists = [], []
    for folder in sorted(results.iterdir()):
        path = folder / "stability.json"
        if not folder.is_dir() or not path.exists():
            continue
        report = _load(path)
        s = report["summary"]
        rows.append(
            "| "
            + " | ".join(
                str(c)
                for c in [
                    folder.name,
                    report["model"],
                    s["items"],
                    report["runs"],
                    s["self_agreement_pct"],
                    f"{s['rules_agreement_pct']} of {s['rule_placed_items']}",
                    report["api_calls"],
                    report["failures"],
                    report["temperature_accepted"],
                    f"{s['default_route_stable']} of {s['default_route_items']}",
                    len(s["disagreements"]),
                ]
            )
            + " |"
        )
        lists.append(f"`{folder.name}`:")
        lists.append("")
        if not s["disagreements"]:
            lists.append("- None.")
        for d in s["disagreements"]:
            labels = ", ".join(f"{k} x{v}" for k, v in sorted(d["model_labels"].items()))
            lists.append(
                f"- {d['question']} \"{d['text']}\": rules say {d['rule_label']}, "
                f"model says {labels} (cases: {', '.join(d['cases'])})"
            )
        lists.append("")
    return rows, lists


def _stability_reports(results: Path) -> dict[str, dict]:
    return {
        folder.name: _load(folder / "stability.json")
        for folder in sorted(results.iterdir())
        if folder.is_dir() and (folder / "stability.json").exists()
    }


def _split_lines(results: Path) -> list[str]:
    """Items whose labels were not identical on every run, per stability run."""

    lines = ["### Items with split labels", ""]
    for name, report in _stability_reports(results).items():
        split = [i for i in report["items"] if not i["self_agreement"]]
        lines.append(f"`{name}`:")
        lines.append("")
        if not split:
            lines.append("- None.")
        for i in split:
            labels = ", ".join(f"{k} x{v}" for k, v in sorted(i["distribution"].items()))
            route = "sent in default mode" if i["default_route"] else "rule-placed, not sent in default mode"
            lines.append(f"- {i['question']} \"{i['text']}\": {labels} ({route})")
        lines.append("")
    return lines


def _cross_model_lines(results: Path) -> list[str]:
    """Unknown titles (no rule label) whose modal model label differs between runs."""

    reports = _stability_reports(results)
    if len(reports) < 2:
        return []
    modal: dict[str, dict[str, str]] = {}
    for name, report in reports.items():
        for i in report["items"]:
            if i["question"] == "title" and i["rule_label"] is None:
                modal.setdefault(i["text"], {})[name] = i["modal_label"]
    differing = {t: m for t, m in sorted(modal.items()) if len(set(m.values())) > 1}
    lines = [
        "### Unknown titles where the models disagree",
        "",
        f"{len(differing)} of {len(modal)} unknown titles get a different most common label "
        "from different models.",
        "",
    ]
    for title, labels in differing.items():
        lines.append(
            f"- \"{title}\": " + ", ".join(f"{n}: {l}" for n, l in sorted(labels.items()))
        )
    lines.append("")
    return lines


def build_summary(results: Path = RESULTS) -> str:
    run_header = (
        ["Run", "Mode", "Model", "Primary score (%)"]
        + [f"{m} (%)" for m in METRICS]
        + ["Decision stability (%)", "JSON stability (%)", "Exact-output match (%)"]
    )
    run_rows, notes = _run_rows(results)
    lines = [
        "# Results",
        "",
        "Generated by `scripts/summarize_results.py` from the reports in each results",
        "folder. Scores are percentages.",
        "",
        "## Harness runs",
        "",
        "| " + " | ".join(run_header) + " |",
        "|" + "---|" * len(run_header),
        *run_rows,
        "",
        "Modes: naive is the starter's single LLM call; offline is the rules engine with no",
        "API call; default is the rules engine with the LLM fallback. Determinism uses",
        "record 0 (case_00000), 10 calls in one process.",
    ]
    if notes:
        lines += ["", "Notes:", ""] + notes

    stab_header = [
        "Run",
        "Model",
        "Items",
        "Runs",
        "Self-agreement (%)",
        "Rules agreement (% of rule-placed items)",
        "API calls",
        "Failures",
        "Temperature 0 accepted",
        "Default-mode items stable",
        "Disagreements",
    ]
    stab_rows, lists = _stability_rows(results)
    if stab_rows:
        lines += [
            "",
            "## LLM stability (cache off)",
            "",
            "| " + " | ".join(stab_header) + " |",
            "|" + "---|" * len(stab_header),
            *stab_rows,
            "",
            "Self-agreement is the share of items whose label was identical on every run.",
            "Rules agreement is the share of rule-placed items whose most common model label",
            "equals the rule label. Default-mode items are the ones default mode actually",
            "sends to the model (unknown titles, consent confirmations and unplaced consents,",
            "unplaced plans); the column counts how many were stable on every run.",
            "",
            *_split_lines(results),
            *_cross_model_lines(results),
            "### Disagreements with the rules",
            "",
            *lists,
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    summary = build_summary()
    (RESULTS / "SUMMARY.md").write_text(summary)
    print(summary, end="")


if __name__ == "__main__":
    main()
