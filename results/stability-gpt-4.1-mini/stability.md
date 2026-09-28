# LLM fallback stability

Model `gpt-4.1-mini`, 5 runs per item, cache off.

| Measure | Value |
|---|---|
| Items | 124 |
| Self-agreement (identical labels on every run) | 100.0% |
| Items the rules placed | 114 |
| Items default mode sends to the model | 15 |
| ...of which stable on every run | 15 |
| Agreement with the rules (modal label equals rule label) | 98.25% |
| Temperature 0 accepted | True |
| API calls made | 620 |
| Failed items (offline answer used) | 0 |

## By question type

| Question | Items | Stable | Rule-placed | Agree with rules | Sent in default mode | ...stable |
|---|---|---|---|---|---|---|
| anticoag | 4 | 4 | 4 | 4 | 0 | 0 |
| consent | 8 | 8 | 8 | 8 | 5 | 5 |
| plan | 5 | 5 | 4 | 4 | 1 | 1 |
| title | 107 | 107 | 98 | 96 | 9 | 9 |

## Disagreements with the rules

| Question | Text | Rule label | Model labels | Cases |
|---|---|---|---|---|
| title | Cardiology Progress Note - Anticoag | ANTICOAG_PLAN | OTHER: 5 | case_00021 |
| title | Consent Counseling Note | CONSENT | OTHER: 5 | case_00004, case_00012, case_00020 ... |

## Items with unstable labels

None.

Rules agreement counts only items the rules place: REQUIRED and KNOWN_OTHER
titles, and POSITIVE or NEGATIVE texts. UNKNOWN titles and UNPLACED texts have
no rule label. An ERROR label means the call failed and the offline answer
would have been used.
