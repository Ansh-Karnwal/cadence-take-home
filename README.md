# Pre-Op Triage Engine

## What this is

`triage_submission` decides whether a pre-operative submission package is `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED` under the Cadence Surgical Center policy, and lists every blocking issue with evidence from the package. It is a deterministic Python rules engine with a narrow LLM fallback for the few inputs the rules cannot place, scored by the unmodified starter harness.

## How to run

**Setup.** Install [uv](https://docs.astral.sh/uv/) and confirm it works. Everything else is resolved by `uv run`.

```bash
uv --version
```

**Offline mode, no API key.** The rules engine runs with no network call. The offline scorer uses the harness's own metric functions and needs no key either.

```bash
PREOP_OFFLINE=1 make baseline
make -f Makefile.local score-offline
```

**Default mode, with a key.** Unknown document titles and ambiguous texts go to the model named by `MODEL`, which defaults to the Makefile's value. `make evals` always creates a remote OpenAI eval, so it needs the key in both modes.

```bash
export OPENAI_API_KEY="<your_api_key>"
make baseline
make evals
make determinism
make score
make report
```

**Environment flags.** Each accepts `1`, `true`, or `yes`, in any case.

| Variable | Effect |
|---|---|
| `PREOP_OFFLINE` | Never call the LLM. Unknown titles count as not required and ambiguous texts as negative. |
| `PREOP_NO_CACHE` | Bypass the in-process LLM answer cache. |
| `PREOP_FORCE_LLM` | Measurement only: send every title and text to the LLM and discard the rules' answer. Ignored when offline. |

**Tests.** No key or network is needed.

```bash
make test
```

**Reproducible runs.** These write the committed evidence under `results/`. They need the key.

```bash
PREOP_OFFLINE=1 make -f Makefile.local final TAG=offline
make -f Makefile.local final MODEL=gpt-4.1-mini
make -f Makefile.local stability MODEL=gpt-4.1-mini RUNS=5
python3 scripts/summarize_results.py
uv run scripts/mutation_check.py
```

## Architecture

```
submission (dict | PatientSubmission)
   |
   v
[normalize]  validate with PatientSubmission; day arithmetic on each item's
   |         local calendar date; ordering by full instant; alias lab codes
   |         (LAB-CBC->CBC, LAB-CMP->CMP); drop labs whose status is not
   |         final/amended/corrected/null; pick most-recent item per kind
   |         (H&P, consent, BP, temperature, CBC, CMP); strip document titles
   v
[classify]   title -> REQUIRED (H&P | CONSENT | ANTICOAG_PLAN)
   |                 | KNOWN_OTHER | UNKNOWN
   |                 (each title classified once per evaluation)
   |         text  -> consent signed? -> POS | NEG | UNPLACED
   |                  plan clear for drug X? -> NEG | UNPLACED (never POS)
   v
[rules]      missing-data gates, Rule 1 (docs), Rule 2 (labs),
   |         Rule 3 (anticoagulation, once per active anticoagulant),
   |         Rule 4 (vitals)
   |         (only UNKNOWN titles / UNPLACED texts leave this layer)
   v
[LLM fallback]  default mode: classify UNKNOWN titles and UNPLACED texts,
   |            strict JSON schema, temperature 0, in-memory cache
   |            offline mode: UNKNOWN -> not a required type;
   |                          UNPLACED -> negative (so offline never
   |                          finds an anticoagulation plan clear)
   v
[assemble]   issues in fixed order, decision from issue categories,
             explanation = "CAT: desc | CAT: desc"
             -> TriageOutput
```

Every policy rule is date arithmetic or a threshold on a structured field, so a rules engine evaluates all of them and writes every issue. The only fuzzy inputs are document titles and short free-text passages. The rules classify those with explicit patterns and keyword sets, and only what they cannot place goes to the LLM, which returns a single label through a strict JSON schema at temperature zero. In offline mode a conservative resolver answers instead. The decisions behind every rule, with the evidence for each, are in [DESIGN.md](DESIGN.md).

## Results

| Run | Mode | Model | Primary score (%) | json_schema_valid (%) | decision_match_oracle (%) | issue_categories_match_oracle (%) | issues_value_grounding (%) | Decision stability (%) | JSON stability (%) | Exact-output match (%) |
|---|---|---|---|---|---|---|---|---|---|---|
| naive | naive | gpt-4.1-mini | 83.71 | 98.0 | 88.0 | 72.0 | 70.0 | 100.0 | 100.0 | 10.0 |
| naive-gpt-6-luna | naive | gpt-6-luna | 94.0 | 100.0 | 100.0 | 94.0 | 70.0 | 100.0 | 100.0 | 10.0 |
| final-offline | offline | none (no LLM) | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| final-gpt-4.1-mini | default | gpt-4.1-mini | 99.43 | 100.0 | 100.0 | 98.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| final-gpt-6-luna | default | gpt-6-luna | 99.43 | 100.0 | 100.0 | 98.0 | 100.0 | 100.0 | 100.0 | 100.0 |

Modes: naive is the starter's single LLM call; offline is the rules engine with no
API call; default is the rules engine with the LLM fallback. Determinism uses
record 0 (case_00000), 10 calls in one process.

Notes:

- `naive`: case_00049 returned a response that failed `TriageOutput` validation; the harness scored it as schema-invalid.

The naive rows are the starter's single LLM call, kept as the before measurement. The rules engine scores 100.0 offline, and both models score 99.43 in default mode, where the only miss is case_00002's category point (see Known divergences).

| Run | Model | Items | Runs | Self-agreement (%) | Rules agreement (% of rule-placed items) | API calls | Failures | Temperature 0 accepted | Disagreements |
|---|---|---|---|---|---|---|---|---|---|
| stability-gpt-4.1-mini | gpt-4.1-mini | 120 | 5 | 99.17 | 98.18 of 110 | 600 | 0 | True | 2 |
| stability-gpt-6-luna | gpt-6-luna | 120 | 5 | 97.5 | 97.27 of 110 | 601 | 0 | False | 3 |

Self-agreement is the share of items whose label was identical on every run.
Rules agreement is the share of rule-placed items whose most common model label
equals the rule label.

The stability script asks each model about every distinct title, consent text, and plan-and-drug pair in the dataset, with the cache off. Both models disagree with the rules on the same two titles, "Cardiology Progress Note - Anticoag" and "Consent Counseling Note", and the oracle sides with the rules on both: case_00021 cites the cardiology note as its plan document, and cases 12 and 44 judge the counseling note as the consent. gpt-6-luna also split on "H&P Update" in case_00049, where the oracle raises no H&P issue even though the older H&P there is out of window, so it too counts "H&P Update" as the H&P. The rules place all three titles, so in default mode the model never sees them. gpt-6-luna rejected `temperature`, so the resolver retried that first call once without it and stopped sending it, which is the one extra API call in its row.

`stability-gpt-4.1-mini`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)

`stability-gpt-6-luna`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)
- title "H&P Update": rules say H_AND_P, model says H_AND_P x2, OTHER x3 (cases: case_00049)

## Determinism

The harness check calls `triage_submission` ten times on case_00000 in one process and compares the outputs.

- **Offline mode.** The check is exact by construction. No network call happens, and every step is a pure function of the input with a fixed issue order.
- **Default mode.** Case_00000 sends one unknown title and one ambiguous plan text to the LLM. The first call asks the model, and the next nine read the in-process cache. The check therefore measures cache consistency across ten calls in one process, not the model.
- **Model stability.** The stability script measures the model itself: it repeats every question with the cache off and reports how often the labels agree.

## Blast radius

The LLM can:

- Label an unknown title as an H&P, a consent, an anticoagulation plan, or other. That can add or remove a documentation or anticoagulation issue.
- Judge an ambiguous consent text signed or not.
- Declare a plan clear for a drug. This is the only way an anticoagulation issue is ever removed.

The LLM cannot:

- Change any date arithmetic, lab window, vital threshold, or missing-data check.
- Write or edit an issue, description, source, detail, or decision. It returns one label from a strict schema, and the rules write the issue.
- Override anything the rules placed.
- Add or remove a safety exclusion, so it can never move a case into or out of `NOT_CLEARED`.

On an API error the resolver retries twice with a short backoff, then uses the offline answer for that item. It also writes one line to stderr and appends a note to the explanation. The engine never raises because of the API. An outage therefore degrades to offline behavior, which can add follow-up issues but never clears a patient.

## Known divergences

- **case_00002, default mode only.** Its newest H&P is titled "History & Phsyical". The rules do not match misspellings, so offline mode flags the older H&P as out of window and matches the oracle. In default mode the model reads the title as an H&P, the documentation issue disappears, and the case costs the category point only. The decision stays `NOT_CLEARED` because of the temperature exclusion. This is accepted rather than adding fuzzy matching.
- **case_00042, source only.** Its plan document names neither the drug nor "anticoag", so the oracle cites `documents`. It is the only plan candidate, so the engine cites the document's index. Source is not scored, and no metric changes.

## Assumptions and limitations

- **Policy source.** The policy appendix of the take-home PDF is the only source of truth. [DESIGN.md](DESIGN.md) records how each rule was read.
- **Anticoagulant list.** The policy does not define the class, so the list is ours: apixaban, rivaroxaban, edoxaban, dabigatran, warfarin, enoxaparin, dalteparin, fondaparinux, heparin. Names are matched by word inside the medication name.
- **Inclusive windows.** "Within N days" includes a gap of exactly N days. An H&P or lab dated after the procedure is outside the window.
- **Local calendar dates.** Day counts use each item's own calendar date, dropping any UTC offset, because the procedure date is a local date. Ordering uses the full timestamp.
- **Consent titles.** A title containing "consent" is the surgical consent unless it also names anesthesia, anaesthesia, research, blood, transfusion, photo, withdrawn, refused, or revoked.
- **Most recent document wins.** The newest H&P and the newest consent are the ones judged. An older signed consent does not rescue a newer unsigned one.
- **Offline never clears an anticoagulated patient.** Only a resolver can call a plan clear, and the offline resolver never does. A genuinely clear plan is flagged offline.
- **Empty-collection grounding.** With no documents or no labs, a missing-item issue has nothing to cite, so the harness's grounding metric cannot pass there.
- **Vitals have no age limit.** The policy says "at the time of pre-operative review" but sets no window, so the most recent reading counts however old it is.
- **Lab status.** A lab counts only when its status is final, amended, corrected, or absent.
- **Null procedure fields.** With no procedure date, only the window checks are skipped. With no procedure risk, the testing rule is skipped. Each raises a missing-data issue.
- **Title matching is strict.** Titles match an explicit pattern list, with no fuzzy matching. Unrecognized titles go to the LLM in default mode and count as not required offline.

## Testing

| Suite | Tests |
|---|---|
| `tests/test_synthetic.py` | 75 |
| `tests/test_normalize.py` | 69 |
| `tests/test_classify.py` | 67 |
| `tests/test_settings.py` | 51 |
| `tests/test_llm.py` | 33 |
| `tests/test_contract.py` | 10 |
| `tests/test_golden.py` | 7 |
| `tests/test_determinism.py` | 1 |
| **Total** | **313** |

- **Golden.** Runs all sample cases in offline mode. It checks that the decision and the category set match the oracle. It also matches every issue both ways on category, source, and description, with one documented exception for case_00042's source. A separate check runs the harness's own per-case scorer, including grounding.
- **Synthetic.** Each test builds a minimal package for one path. Paths include exact window and threshold boundaries, same-day vitals, offsets, lab status, and multiple consents. They also cover per-drug anticoagulation, the verifier's probe strings, empty collections, fully empty packages, and resolver injection.
- **LLM layer.** A fake client records every call. The tests cover the strict schema, prompts, cache hits, bypassing the cache, force mode, retries, the outage fallback, rejected temperature, and ten-call determinism. No test touches the network.
- **Mutation checks.** `scripts/mutation_check.py` applies 20 deliberate bugs to a temporary copy of the repo and runs the suite against each. The suite caught 20. The report is in `results/mutation/mutation_report.json`.

## Process

[DESIGN.md](DESIGN.md) was written and committed before any code, and it records every decision and the oracle conventions checked against the data. Each milestone was then built against it in its own commits: scaffold and baseline capture, rules engine, LLM layer, and evidence runs. A separate reviewer session verified the work at each checkpoint, and its findings, including the rejected ones, are recorded in `DESIGN.md`.
