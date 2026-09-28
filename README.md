# Pre-Op Triage Engine

## What this is

`triage_submission` decides whether a pre-operative submission package is `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED` under the Cadence Surgical Center policy, and lists every blocking issue with evidence from the package. It is a deterministic Python rules engine with a narrow LLM layer for the few inputs keywords cannot settle, scored by the unmodified starter harness.

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

**Default mode, with a key.** Unknown document titles, ambiguous texts, and consent confirmations go to the model named by `MODEL`, which defaults to the Makefile's value. `make evals` always creates a remote OpenAI eval, so it needs the key in both modes.

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
| `PREOP_OFFLINE` | Never call the LLM. Unknown titles count as not required, ambiguous texts as negative, and keyword-signed consents as signed. |
| `PREOP_NO_CACHE` | Bypass the in-process LLM answer cache. |
| `PREOP_FORCE_LLM` | Measurement only: send every title and text to the LLM and discard the rules' answer. Ignored when offline. |

**Tests.** No key or network is needed.

```bash
make test
```

**Reproducible runs.** These write the committed evidence under `results/` and need the key, except `mutation`.

```bash
PREOP_OFFLINE=1 make -f Makefile.local final TAG=offline
make -f Makefile.local final MODEL=gpt-4.1-mini
make -f Makefile.local stability MODEL=gpt-4.1-mini RUNS=5
make -f Makefile.local mutation
python3 scripts/summarize_results.py
```

The starter's naive single-call implementation is at commit `62c2413`. The `naive` rows below were measured from it before any code changed.

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

**The rules cover everything the policy states as a fact about structured fields.** Windows are date arithmetic. Tests are codes and dates, and safety exclusions are thresholds. Missing data is a null check. None of that benefits from a model, and all of it must be exact and repeatable, so the rules evaluate every rule and write every issue.

**The model exists for the two fuzzy inputs:** document titles, and short free-text judgments. The judgments are whether a consent is signed and whether an anticoagulation plan is clear. The rules classify both with explicit patterns and keywords. The model sees only what they cannot settle, and returns one label through a strict JSON schema. One asymmetry governs every judgment that could clear a patient: keywords can add an issue but never remove one. A keyword-negative consent is final, and a keyword-positive consent clears only if the model confirms it. Only the model can call a plan clear.

**Measured on this sample:**
- **Offline and default scores.** Offline mode scores 100.0. Default mode scores 99.43 on both models, and case_00002 is the only case that differs from offline (see Known divergences).
- **Consent confirmation.** It changed no case on either model; the only failing case in each default run is case_00002.
- **Items sent to the model.** Default mode sends 15 distinct items to the model: 9 unknown titles, 5 consent confirmations, and 1 unplaced plan text. gpt-4.1-mini gave the same label on every run for 14 of 15. It split on the consent "Signed consent scanned and verified before scheduling.", which it called signed on 4 runs and unsigned on 1. gpt-6-luna gave the same label on every run for all 15.
- **Model disagreement on unknown titles.** The two models give a different label to 5 of the 9 unknown titles. None of them changes an output, because in each of those cases a newer recognized H&P exists.

The decisions behind every rule, with the evidence for each, are in [DESIGN.md](DESIGN.md).

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

The naive rows are the starter's single LLM call, kept as the before measurement. The engine is deterministic offline and matches the oracle on every case; in default mode the only miss on either model is case_00002's category point.

| Run | Model | Items | Runs | Self-agreement (%) | Rules agreement (% of rule-placed items) | API calls | Failures | Temperature 0 accepted | Default-mode items stable | Disagreements |
|---|---|---|---|---|---|---|---|---|---|---|
| stability-gpt-4.1-mini | gpt-4.1-mini | 120 | 5 | 99.17 | 98.18 of 110 | 600 | 0 | True | 14 of 15 | 2 |
| stability-gpt-6-luna | gpt-6-luna | 120 | 5 | 98.33 | 97.27 of 110 | 601 | 0 | False | 15 of 15 | 3 |

Self-agreement is the share of items whose label was identical on every run.
Rules agreement is the share of rule-placed items whose most common model label
equals the rule label. Default-mode items are the ones default mode actually
sends to the model (unknown titles, consent confirmations and unplaced consents,
unplaced plans); the column counts how many were stable on every run.

The stability script asks each model about every distinct title, consent text, and plan-and-drug pair in the dataset, five times with the cache off. It uses temperature 0 where the model accepts it. gpt-6-luna rejected the parameter and ran at its default, which accounts for its one extra API call.

Both models disagree with the rules on the same two titles. On "Consent Counseling Note" the oracle sides with the rules: cases 12 and 44 judge that document as the consent. On "Cardiology Progress Note - Anticoag" the oracle's citation of the note in case_00021 is explained by its text, which names an anticoagulant, so it does not show the oracle treats the title as a plan. gpt-6-luna also split on "H&P Update". The direct support for the rules there is the document's own text, "H&P completed". The indirect support is case_00049, where the oracle raises no H&P issue although the older H&P is out of window; that is weakened because the case has no procedure risk, and the oracle may skip every rule then. The rules place all three titles, so default mode never sends them to the model.

`stability-gpt-4.1-mini`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)

`stability-gpt-6-luna`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)
- title "H&P Update": rules say H_AND_P, model says H_AND_P x2, OTHER x3 (cases: case_00049)

gpt-4.1-mini split 4 to 1 on the consent "Signed consent scanned and verified before scheduling.", which default mode sends for confirmation. Because keyword-signed consents are now model-confirmed, a split on a consent text means the outcome depends on which answer a process draws first: the committed harness run drew "signed", but a run that drew "unsigned" would flag the consent in all 9 cases that carry this text. That is the cost of the confirmation step, and it errs toward follow-up. For a reproducible default-mode run, use `MODEL=gpt-6-luna`, which was stable on every item default mode sends. On `gpt-4.1-mini`, the harness default, one consent text drew 'unsigned' in 1 of 5 stability runs; a harness run that draws it would flag 9 cases and drop case_00012 from READY. gpt-6-luna split 3 to 2 on the titles "H&P Update" and "Perioperative Medication Review", which the rules place, so default mode never asks the model about them.

`stability-gpt-4.1-mini`:

- consent "Signed consent scanned and verified before scheduling.": false x1, true x4 (sent in default mode)

`stability-gpt-6-luna`:

- title "H&P Update": H_AND_P x2, OTHER x3 (rule-placed, not sent in default mode)
- title "Perioperative Medication Review": ANTICOAG_PLAN x3, OTHER x2 (rule-placed, not sent in default mode)

### Generalization probes

| Run | Model | Pre-fix rules correct | HEAD rules correct | Model correct (majority) | Model strings with split answers | Failures |
|---|---|---|---|---|---|---|
| probes-gpt-4.1-mini | gpt-4.1-mini | 0 of 21 | 21 of 21 | 19 of 19 | 1 | 0 |
| probes-gpt-6-luna | gpt-6-luna | 0 of 21 | 21 of 21 | 18 of 19 | 0 | 0 |

These 21 strings were written by the two reviewers, without seeing the keyword lists, to find inputs the rules of the day got wrong, and each one is now a test. The pre-fix rules are the honest comparison, because the HEAD rules were extended in response to these exact strings, so the HEAD score of 21 of 21 is fitted and the pre-fix score of 0 is zero by construction; of those pre-fix errors, 16 would wrongly clear a patient and 5, all titles, would over-flag. Unaided, gpt-4.1-mini gave the correct majority label on 19 of 19 strings it can be asked about but split 3 to 2 on "Consent signed by patient. Patient later withdrew consent.", calling it signed on 2 of 5 runs, and gpt-6-luna got 18 of 19, calling "Patient signed consent for left knee arthroscopy; new consent required for planned procedure." signed on 5 of 5 runs; the model catches most of what the old keywords missed, but not all, which is why a keyword negative stays final.

## Determinism

The harness check calls `triage_submission` ten times on case_00000 in one process and compares the outputs.

- **Offline mode.** The check is exact by construction. No network call happens, and every step is a pure function of the input with a fixed issue order.
- **Default mode.** Case_00000 sends an unknown title, a consent confirmation, and an ambiguous plan text to the model. The first call asks the model, and the next nine read the in-process cache. The check therefore measures cache consistency across ten calls in one process, not the model.
- **Model stability.** The stability script measures the model itself: it repeats every question with the cache off and reports how often the labels agree.

## Blast radius

The LLM can:

- Label an unknown title as an H&P, a consent, an anticoagulation plan, or other. That can add or remove a documentation or anticoagulation issue.
- Judge an ambiguous consent text signed or not.
- Decline to confirm a keyword-signed consent, which adds a consent issue. It can never remove one, because a keyword-negative consent is final.
- Declare a plan clear for a drug. This is the only way an anticoagulation issue is ever removed.

The LLM cannot:

- Change any date arithmetic, lab window, vital threshold, or missing-data check.
- Write or edit an issue, description, source, detail, or decision. It returns one label from a strict schema, and the rules write the issue.
- Override a keyword-negative consent or a title the rules recognized.
- Add or remove a safety exclusion, so it can never move a case into or out of `NOT_CLEARED`.

On an API error the resolver retries twice with a short backoff, then uses the offline answer for that item. A failed consent confirmation keeps the keyword answer. It writes one line to stderr and appends a note to the explanation, and the engine never raises because of the API. An outage therefore degrades to offline behavior. Offline is not strictly more conservative. When the newest document has an unrecognized title, classifying it as other means an older recognized document is judged instead, which can remove an issue as well as add one.

## Known divergences

- **case_00002, default mode only.** Its newest H&P is titled "History & Phsyical". The rules do not match misspellings, so offline mode flags the older H&P as out of window and matches the oracle. In default mode the model reads the title as an H&P, the documentation issue disappears, and the case costs the category point only. The decision stays `NOT_CLEARED` because of the temperature exclusion. This is accepted rather than adding fuzzy matching.
- **case_00042, source only.** Its plan document names neither the drug nor "anticoag", so the oracle cites `documents`. It is the only plan candidate, so the engine cites the document's index. Source is not scored, and no metric changes.

## Assumptions and limitations

- **Policy source and H&P completeness.** The policy appendix of the take-home PDF is the only source of truth, and [DESIGN.md](DESIGN.md) records how each rule was read. The starter's prompt copy of the policy was removed from `core.py`. The H&P's "completed within" window is read against its date; clinical completeness is not judged from its text.
- **Anticoagulant list.** The policy does not define the class, so the list is ours and finite. It covers apixaban, rivaroxaban, edoxaban, dabigatran, warfarin, enoxaparin, dalteparin, fondaparinux, and heparin, with their common brand names. Names are matched by word inside the medication name, and a drug named only in document text is not a current medication.
- **Inclusive windows.** "Within N days" includes a gap of exactly N days. An H&P or lab dated after the procedure is outside the window.
- **Dates.** Day counts use each item's own calendar date, dropping any UTC offset, because the procedure date is a local date. Ordering uses the full timestamp, with date-only values at midnight UTC. An item date that is present but unparseable raises a missing-data issue and is not selected.
- **Consent titles.** A title containing "consent" is the surgical consent. It is not if it also names anesthesia, anaesthesia, research, blood, transfusion, photo, withdrawn, refused, or revoked, unless it also names surgery.
- **Most recent document wins.** The newest H&P and the newest consent are judged. An older signed consent does not rescue a newer unsigned one.
- **Offline limitations.** Offline mode never clears a patient on a recognized anticoagulant, because only the model can call a plan clear. It also trusts keyword-signed consents that default mode would send for confirmation.
- **Empty-collection grounding.** With no documents or no labs, a missing-item issue has nothing to cite, so the harness's grounding metric cannot pass there.
- **Vitals have no age limit.** The policy says "at the time of pre-operative review" but sets no window, so the most recent reading counts however old it is.
- **Lab status.** A lab counts only when its status is final, amended, corrected, or absent.
- **Null procedure fields.** With no procedure date, only the window checks are skipped. With no procedure risk, the testing rule is skipped. Each raises a missing-data issue.
- **Title matching is strict.** Titles match an explicit pattern list, with no fuzzy matching. Unrecognized titles go to the model in default mode and count as not required offline.

## Testing

| Suite | Tests |
|---|---|
| `tests/test_classify.py` | 97 |
| `tests/test_synthetic.py` | 93 |
| `tests/test_normalize.py` | 69 |
| `tests/test_settings.py` | 51 |
| `tests/test_llm.py` | 35 |
| `tests/test_contract.py` | 10 |
| `tests/test_golden.py` | 7 |
| `tests/test_determinism.py` | 1 |
| **Total** | **363** |

- **Golden.** Runs all sample cases in offline mode. It checks that the decision and the category set match the oracle. It also matches every issue both ways on category, source, and description, with one documented exception for case_00042's source. A separate check runs the harness's own per-case scorer, including grounding.
- **Synthetic.** Each test builds a minimal package for one path. Paths include exact window and threshold boundaries, same-day vitals, offsets, unparseable dates, lab status, and multiple consents. They also cover combined consent titles, per-drug and brand-name anticoagulation, the reviewers' probe strings, empty collections, and fully empty packages. Consent confirmation is tested through an injected resolver.
- **LLM layer.** A fake client records every call. The tests cover the strict schema, prompts, cache hits, bypassing the cache, force mode, retries, and the outage fallback, including a failed consent confirmation. They also cover rejected temperature and ten-call determinism. No test touches the network.
- **Mutation checks.** `scripts/mutation_check.py` applies 26 deliberate bugs to a temporary copy of the repo and runs the suite against each. The suite caught 26. The report, with the per-suite counts above, is in `results/mutation/mutation_report.json`.

## Process

[DESIGN.md](DESIGN.md) was written and committed before any code, and it records every decision and the oracle conventions checked against the data. Each milestone was then built against it in its own commits: scaffold and baseline capture, rules engine, LLM layer, and evidence runs. A separate reviewer session verified the work at each checkpoint, and its findings, including the rejected ones, are recorded in `DESIGN.md`.
