# Naive baseline results

Generated from the JSON reports in each run folder. Scores are percentages.

| Run | Model | Primary score (%) | json_schema_valid (%) | decision_match_oracle (%) | issue_categories_match_oracle (%) | issues_value_grounding (%) | Decision stability (%) | JSON stability (%) | Exact-output match (%) |
|---|---|---|---|---|---|---|---|---|---|
| naive | gpt-4.1-mini | 83.71 | 98.0 | 88.0 | 72.0 | 70.0 | 100.0 | 100.0 | 10.0 |
| naive-gpt-6-luna | gpt-6-luna | 94.0 | 100.0 | 100.0 | 94.0 | 70.0 | 100.0 | 100.0 | 10.0 |

Determinism uses record 0 (case_00000), 10 calls. Run folders hold outputs.jsonl, eval_report.json, and determinism_report.json.

Notes:

- `naive`: case_00049 returned a response that failed `TriageOutput` validation; the harness scored it as schema-invalid.
