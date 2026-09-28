# LLM fallback stability

Model `gpt-6-luna`, 5 runs per item, cache off.

| Measure | Value |
|---|---|
| Items | 120 |
| Self-agreement (identical labels on every run) | 97.5% |
| Items the rules placed | 110 |
| Agreement with the rules (modal label equals rule label) | 97.27% |
| Temperature 0 accepted | False |
| API calls made | 601 |
| Failed items (offline answer used) | 0 |

## By question type

| Question | Items | Stable | Rule-placed | Agree with rules |
|---|---|---|---|---|
| consent | 8 | 8 | 8 | 8 |
| plan | 5 | 5 | 4 | 4 |
| title | 107 | 104 | 98 | 95 |

## Disagreements with the rules

| Question | Text | Rule label | Model labels | Cases |
|---|---|---|---|---|
| title | Cardiology Progress Note - Anticoag | ANTICOAG_PLAN | OTHER: 5 | case_00021 |
| title | Consent Counseling Note | CONSENT | OTHER: 5 | case_00004, case_00012, case_00020 ... |
| title | H&P Update | H_AND_P | H_AND_P: 2, OTHER: 3 | case_00049 |

## Items with unstable labels

| Question | Text | Labels |
|---|---|---|
| title | H&P Update | H_AND_P: 2, OTHER: 3 |
| title | Perioperative Medication Review | ANTICOAG_PLAN: 4, OTHER: 1 |
| title | Pre-op Consent Discussion | CONSENT: 4, OTHER: 1 |

Rules agreement counts only items the rules place: REQUIRED and KNOWN_OTHER
titles, and POSITIVE or NEGATIVE texts. UNKNOWN titles and UNPLACED texts have
no rule label. An ERROR label means the call failed and the offline answer
would have been used.
