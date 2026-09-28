# LLM fallback stability

Model `gpt-4.1-mini`, 5 runs per item, cache off.

| Measure | Value |
|---|---|
| Items | 120 |
| Self-agreement (identical labels on every run) | 99.17% |
| Items the rules placed | 110 |
| Items default mode sends to the model | 15 |
| ...of which stable on every run | 14 |
| Agreement with the rules (modal label equals rule label) | 98.18% |
| Temperature 0 accepted | True |
| API calls made | 600 |
| Failed items (offline answer used) | 0 |

## By question type

| Question | Items | Stable | Rule-placed | Agree with rules | Sent in default mode | ...stable |
|---|---|---|---|---|---|---|
| consent | 8 | 7 | 8 | 8 | 5 | 4 |
| plan | 5 | 5 | 4 | 4 | 1 | 1 |
| title | 107 | 107 | 98 | 96 | 9 | 9 |

## Disagreements with the rules

| Question | Text | Rule label | Model labels | Cases |
|---|---|---|---|---|
| title | Cardiology Progress Note - Anticoag | ANTICOAG_PLAN | OTHER: 5 | case_00021 |
| title | Consent Counseling Note | CONSENT | OTHER: 5 | case_00004, case_00012, case_00020 ... |

## Items with unstable labels

| Question | Text | Labels |
|---|---|---|
| consent | Signed consent scanned and verified before scheduling. | false: 1, true: 4 |

Rules agreement counts only items the rules place: REQUIRED and KNOWN_OTHER
titles, and POSITIVE or NEGATIVE texts. UNKNOWN titles and UNPLACED texts have
no rule label. An ERROR label means the call failed and the offline answer
would have been used.
