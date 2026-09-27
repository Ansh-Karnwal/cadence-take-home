#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "openai>=2.0.0",
#   "pydantic>=2.8.0",
# ]
# ///

"""Score baseline outputs locally with the harness's own metric functions.

Makes no OpenAI API calls. ``run_evals`` imports ``openai`` at module load, so
the package must be installed, but no client is created here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from run_evals import (
    PRIMARY_SCORE_NAME,
    ROOT,
    _extract_output_payload,
    _local_metrics_for_row,
    _summarize_local_rows,
    load_baseline_outputs,
    load_cases,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=str(ROOT / "data" / "patients_sample_50.jsonl"),
        help="Input patient JSONL file",
    )
    parser.add_argument(
        "--outputs",
        default=str(ROOT / "data" / "baseline_outputs.jsonl"),
        help="Baseline outputs JSONL file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = load_cases(Path(args.input))
    outputs_by_index = load_baseline_outputs(Path(args.outputs))

    local_rows: list[dict[str, object]] = []
    failures: list[str] = []
    for idx, case in enumerate(cases):
        # Same pairing as run_evals._build_eval_items.
        output_payload, output_error = _extract_output_payload(
            outputs_by_index.get(idx, {})
        )
        local = _local_metrics_for_row(
            submission=case.submission.model_dump(),
            oracle_output=case.expected_output,
            model_output_payload=output_payload,
        )
        local_rows.append(
            {"metrics": local["metrics"], "aggregate_local_score": local["aggregate_local_score"]}
        )

        failed = [name for name, passed in local["metrics"].items() if not passed]
        if failed:
            line = (
                f"  [{idx:02d}] {case.case_id}  FAILED: {', '.join(failed)}"
                f"  (score {local['aggregate_local_score']:.2f})"
            )
            if output_error:
                line += f"  error: {output_error.splitlines()[0]}"
            failures.append(line)

    summary = _summarize_local_rows(local_rows)

    print("Local metrics summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print(f"Failing cases ({len(failures)}):")
    for line in failures:
        print(line)

    print(f"Primary score ({PRIMARY_SCORE_NAME}): {summary.get(PRIMARY_SCORE_NAME, 0.0)}%")


if __name__ == "__main__":
    main()
