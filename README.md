# Pre-Op Triage Engine

## What this is

`triage_submission` decides whether a pre-operative submission package is `READY`, `NEEDS_FOLLOW_UP`, or `NOT_CLEARED` under the Cadence Surgical Center policy, and lists every blocking issue with evidence from the package. It is a deterministic Python rules engine with a narrow LLM layer for the few inputs keywords cannot settle, scored by the unmodified starter harness.

## Transcripts

The AI sessions that produced this repo are exported in full at the repo root:

- [AI Transcript - Designer.md](AI%20Transcript%20-%20Designer.md): wrote [DESIGN.md](DESIGN.md) before any code, from the decisions in the first prompt.
- [AI Transcript - Builder.md](AI%20Transcript%20-%20Builder.md): Opus 5.5 with Fable 5.1 as advisor, one prompt per milestone, every commit in this repo.
- [AI Transcript - Validator.md](AI%20Transcript%20-%20Validator.md): Opus 5.5, one session run at two checkpoints, after the rules engine and before submission. It reported findings and never edited.

A fourth planner session drafted every prompt the other three received and triaged the validator's findings; it has no separate export, and its prompts appear verbatim as the user turns in the three files. The exports come from Claude Code's `/export`, which records every prompt, reply, and tool command but not tool outputs, so those show as "(No output)"; the outputs live in this repo and under `results/`.

**Prompt order.** Each prompt went to one session and ended in the commits listed. Every builder prompt had the same six parts: where we are, decisions already made, task, constraints, acceptance criteria, report back. Every validator prompt had the rule: report, never fix.

| # | Session | Prompt | Commits |
|---|---|---|---|
| 1 | Designer | Write `DESIGN.md` from the planner's decisions; verify every oracle claim against the 50 cases | `4bd438f` |
| 2 | Designer | Record the resolutions to its open questions | (same file, before commit) |
| 3 | Builder | Milestone 1: capture the naive baseline on two models, scaffold the package, offline scorer, contract tests | `0442db7`, `f78b223` |
| 4 | Builder | Milestone 2: rules engine, golden test over all 50 cases, synthetic edge cases | `f89c8c5`, `b3940c6` |
| 5 | Validator | Checkpoint 1: review the rules against the policy, probe for wrong READYs | none |
| 6 | Builder | Fix round 1: the accepted checkpoint 1 findings, with D4 rewritten | `6f25e00`, `f26a193` |
| 7 | Builder | Milestone 3: LLM fallback resolver, cache, flags, stability script | `73c833d`, `eca02a7` |
| 8 | Builder | Milestone 4: committed runs on both models, README, cleanup | `bbc4743` to `432bea9` |
| 9 | Validator | Checkpoint 2: pre-submission review, every README number traced to a report | none |
| 10 | Builder | Fix round 2: model-confirmed consents, brand-name anticoagulants, unparseable dates, regenerate runs | `87b36a6` to `4ce25e1` |
| 11 | Builder | Generalization probes on the reviewers' strings, exposure statement | `082dd9d` to `fe48faa` |
| 12 | Builder | Model-assisted anticoagulant recognition, regenerate runs | `4062c49` to `cb77da9` |

Between prompts 5 and 6, and again between 9 and 10, the planner triaged each validator finding with the owner; accepted findings became decisions in the next builder prompt, and rejected ones were recorded with reasons in `DESIGN.md` section 8.

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

**Default mode, with a key.** Unknown document titles, ambiguous texts, consent confirmations, and unlisted medication names go to the model named by `MODEL`, which defaults to the Makefile's value. `make evals` always creates a remote OpenAI eval, so it needs the key in both modes.

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
| `PREOP_OFFLINE` | Never call the LLM. Unknown titles count as not required, ambiguous texts as negative, keyword-signed consents as signed, and unlisted medications as not anticoagulants. |
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
   |         medication (active true or null) -> KNOWN_ANTICOAG
   |                 | KNOWN_OTHER | UNKNOWN (each name asked once)
   v
[rules]      missing-data gates, Rule 1 (docs), Rule 2 (labs),
   |         Rule 3 (anticoagulation, once per active anticoagulant),
   |         Rule 4 (vitals)
   |         (only UNKNOWN titles and medications, UNPLACED texts, and
   |         keyword-POSITIVE consents leave this layer)
   v
[LLM fallback]  default mode: classify UNKNOWN titles and medications,
   |            judge UNPLACED texts, confirm keyword-POSITIVE consents;
   |            strict JSON schema, temperature 0, in-memory cache
   |            offline mode: UNKNOWN title -> not a required type;
   |                          UNKNOWN medication -> not an anticoagulant;
   |                          UNPLACED -> negative (so offline never
   |                          finds an anticoagulation plan clear)
   v
[assemble]   issues in fixed order, decision from issue categories,
             explanation = "CAT: desc | CAT: desc"
             -> TriageOutput
```

**The rules cover everything the policy states as a fact about structured fields.** Windows are date arithmetic. Tests are codes and dates, and safety exclusions are thresholds. Missing data is a null check. None of that benefits from a model, and all of it must be exact and repeatable, so the rules evaluate every rule and write every issue.

**The model exists for the three fuzzy inputs:** document titles, short free-text judgments, and medication names on neither of the rules' lists. The judgments are whether a consent is signed and whether an anticoagulation plan is clear. The rules classify all three with explicit patterns, keywords, and lists. The model sees only what they cannot settle, and returns one label through a strict JSON schema. One asymmetry governs every judgment that could clear a patient: keywords can add an issue but never remove one. A keyword-negative consent is final, and a keyword-positive consent clears only if the model confirms it. Only the model can call a plan clear. The model can add an unlisted medication to the anticoagulants but never remove a listed one.

**Measured on this sample:**
- **Offline and default scores.** Offline mode scores 100.0. Default mode scores 99.43 on both models, and case_00002 is the only case that differs from offline (see Known divergences).
- **Consent confirmation.** It changed no case on either model; the only failing case in each default run is case_00002.
- **Items sent to the model.** Default mode sends 15 distinct items to the model: 9 unknown titles, 5 consent confirmations, 1 unplaced plan text, and 0 unknown medication names. gpt-4.1-mini gave the same label on every run for all 15. gpt-6-luna gave the same label on every run for all 15.
- **Model disagreement on unknown titles.** The two models give a different label to 5 of the 9 unknown titles. None of them changes an output, because in each of those cases a newer recognized H&P exists.

The decisions behind every rule, with the evidence for each, are in [DESIGN.md](DESIGN.md).

## Decisions and trade-offs

Each line is a choice, why it was made, and what it cost or ruled out. The full reasoning and the rejected alternatives are in [DESIGN.md](DESIGN.md) sections 3 and 8.

- **Rules first, model only for what rules cannot place.** Every policy rule is date arithmetic, a threshold, or a presence check, and those must be exact and repeatable. The cost is that the rules were written after studying all 50 cases, so the sample score measures fit, not generalization; the reviewer probes are the only held-out evidence. Ruled out: the starter's single LLM call over the whole package, which scored 83.71 and never produced the same output twice.
- **Keywords can add an issue but never remove one.** A keyword negative is final because it moves toward follow-up, the policy's own default for ambiguity. Anything that would clear a patient on free text goes to the model. The cost is about 47 extra model calls per run for consent confirmation, and a model that wavers on a signed consent flips a READY case to follow-up. Ruled out: keyword positives as final, which the second review broke with "Consent signed by patient. Patient later withdrew consent."
- **No positive keywords for plan clarity at all.** The first review showed that any keyword test for "clear" cleared patients on text like "Cardiology to advise whether to hold or resume apixaban." So only the model can call a plan clear, and offline mode never clears an anticoagulated patient. That limitation is accepted and stated rather than hidden behind a list.
- **Strict title matching, no fuzzy matching.** Titles match an explicit pattern list; a misspelling falls through to the model. The cost is case_00002: the model reads "History & Phsyical" correctly, the oracle did not, and the category point is lost on purpose. Ruled out: edit-distance matching, whose threshold would be arbitrary and would hide the one place on this dataset where the fallback fires.
- **Structured procedure date only.** The oracle ignores the "Procedure date target" sentence in H&P text, so the engine does too, but it quotes that sentence in the missing-date issue so a human sees it. Ruled out: parsing dates from documents, and using `metadata.submission_received_at`, which equals the procedure date in every dated case but is not the policy's field.
- **Harness untouched, scorer imported.** The offline scorer imports the harness's own metric functions rather than copying them, so offline numbers are provably the official numbers. The cost is that importing the harness pulls in the OpenAI client even for keyless runs. Ruled out: forking the scorer, which would let the two drift.
- **Anticoagulant class is ours, with the model for unknowns.** The policy does not define the class. A finite generic and brand list is matched first; unlisted active medications go to the model in default mode and count as not anticoagulants offline. The cost is that this is the one question where an outage degrades toward clearance, because offline has no conservative answer short of flagging every drug. Ruled out: a pharmacology list beyond names the data or common brands show, which would be external medical knowledge dressed as code.
- **Unparseable dates raise an issue rather than being dropped.** The first version silently skipped a reading whose date did not parse and used an older one, which let a 186/112 blood pressure through. Now it is a missing-data issue. The cost is a follow-up on data-entry noise, the safe direction.
- **Model answers cached in memory, never on disk.** The harness's determinism check calls the function ten times in one process, and the cache makes that exact. A disk cache would also freeze mistakes and make a fresh checkout behave differently. The cost is that the check measures cache consistency, not the model; the stability script measures the model separately and is reported beside it.
- **Naive baseline captured before any code changed.** Without it the README could not show the before number. The cost was running it twice, once per model, before the scaffold existed.
- **Evidence strings extended past the oracle's own shapes.** The oracle's expected outputs fail the harness's grounding metric on 22 of 50 cases because their detail strings quote nothing the metric can find. Ours append the date, drug name, or present items so they ground. The cost is that details never match the oracle character for character, which the golden test does not require.

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
| stability-gpt-4.1-mini | gpt-4.1-mini | 124 | 5 | 100.0 | 98.25 of 114 | 620 | 0 | True | 15 of 15 | 2 |
| stability-gpt-6-luna | gpt-6-luna | 124 | 5 | 98.39 | 97.37 of 114 | 621 | 0 | False | 15 of 15 | 3 |

Self-agreement is the share of items whose label was identical on every run.
Rules agreement is the share of rule-placed items whose most common model label
equals the rule label. Default-mode items are the ones default mode actually
sends to the model (unknown titles, consent confirmations and unplaced consents,
unplaced plans, unknown medication names); the column counts how many were stable
on every run.

The stability script asks each model about every distinct title, consent text, plan-and-drug pair, and medication name in the dataset, five times with the cache off. It uses temperature 0 where the model accepts it. gpt-6-luna rejected the parameter and ran at its default, which accounts for its one extra API call.

Both models disagree with the rules on the same two titles. On "Consent Counseling Note" the oracle sides with the rules: cases 12 and 44 judge that document as the consent. On "Cardiology Progress Note - Anticoag" the oracle's citation of the note in case_00021 is explained by its text, which names an anticoagulant, so it does not show the oracle treats the title as a plan. gpt-6-luna also disagreed on "Perioperative Medication Review" (OTHER on 4 and ANTICOAG_PLAN on 1 runs). The oracle cites that document in cases 26 and 38, but both texts name an anticoagulant ("Anticoagulant noted in medication list" and "Apixaban listed"), so, as with the cardiology note, the citation is explained by the text rather than the title. Neither model disagreed with the medication lists: both called apixaban and warfarin anticoagulants and lisinopril and metformin not, on every run. The rules place every title above, so default mode never sends them to the model.

`stability-gpt-4.1-mini`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)

`stability-gpt-6-luna`:

- title "Cardiology Progress Note - Anticoag": rules say ANTICOAG_PLAN, model says OTHER x5 (cases: case_00021)
- title "Consent Counseling Note": rules say CONSENT, model says OTHER x5 (cases: case_00004, case_00012, case_00020, case_00028, case_00036, case_00044)
- title "Perioperative Medication Review": rules say ANTICOAG_PLAN, model says ANTICOAG_PLAN x1, OTHER x4 (cases: case_00026, case_00038)

In this regeneration both models gave the same label on every run for every item default mode sends, so either `MODEL` gives a reproducible default-mode run on this sample. Consent confirmation is still the item most exposed to run-to-run variation: the previous regeneration, at commit `6d40968`, saw gpt-4.1-mini, the harness default, call the consent "Signed consent scanned and verified before scheduling." unsigned on one run, and a harness run that drew that answer would flag every case carrying the text, including case_00012, one of the three READY cases. gpt-6-luna split 3 to 2 on the title "H&P Update", which the rules place, so default mode never asks the model about it. gpt-6-luna split 4 to 1 on the title "Perioperative Medication Review", which the rules place, so default mode never asks the model about it.

`stability-gpt-4.1-mini`:

- None.

`stability-gpt-6-luna`:

- title "H&P Update": H_AND_P x3, OTHER x2 (rule-placed, not sent in default mode)
- title "Perioperative Medication Review": ANTICOAG_PLAN x1, OTHER x4 (rule-placed, not sent in default mode)

### Generalization probes

| Run | Model | Pre-fix rules correct | HEAD rules correct | Model correct (majority) | Model strings with split answers | Failures |
|---|---|---|---|---|---|---|
| probes-gpt-4.1-mini | gpt-4.1-mini | 3 of 26 | 26 of 26 | 26 of 26 | 2 | 0 |
| probes-gpt-6-luna | gpt-6-luna | 3 of 26 | 26 of 26 | 25 of 26 | 1 | 0 |

These 26 strings are the probes the two reviewers wrote without seeing the keyword lists, to find inputs the rules of the day got wrong, plus five unlisted medication names added in the final round to test the medication question; each one is now a test. The pre-fix rules are the honest comparison, because the HEAD rules were extended in response to these exact strings, so the HEAD score of 26 of 26 is fitted; the pre-fix rules got 3, all of them the non-anticoagulant medication names they already ignored, and of their errors 18 would wrongly clear a patient and 5, all titles, would over-flag. Unaided, gpt-4.1-mini gave the correct majority label on 26 of 26, splitting on "Consent signed by patient. Patient later withdrew consent." (not signed on 4, signed on 1) and splitting on "Patient signed consent for left knee arthroscopy; new consent required for planned procedure." (not signed on 3, signed on 2); gpt-6-luna gave the correct majority label on 25 of 26, missing "Patient signed consent for left knee arthroscopy; new consent required for planned procedure." (signed on 5 of 5 runs) and splitting on "Consent Withdrawn" (OTHER on 4, CONSENT on 1). Neither model missed a medication name. The model catches most of what the old keywords missed, but not all, which is why a keyword negative stays final.

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
- Identify an unlisted medication as an anticoagulant, which adds an anticoagulation issue; it can never un-list one.
- Declare a plan clear for a drug. This is the only way an anticoagulation issue is ever removed.

The LLM cannot:

- Change any date arithmetic, lab window, vital threshold, or missing-data check.
- Write or edit an issue, description, source, detail, or decision. It returns one label from a strict schema, and the rules write the issue.
- Override a keyword-negative consent or a title the rules recognized.
- Add or remove a safety exclusion, so it can never move a case into or out of `NOT_CLEARED`.

On an API error the resolver retries twice with a short backoff, then uses the offline answer for that item. A failed consent confirmation keeps the keyword answer. It writes one line to stderr and appends a note to the explanation, and the engine never raises because of the API. An outage therefore degrades to offline behavior. Offline is not strictly more conservative. When the newest document has an unrecognized title, classifying it as other means an older recognized document is judged instead, which can remove an issue as well as add one. The medication question is the one place an outage degrades toward clearance: offline has no conservative answer for an unknown drug short of flagging every medication, so an unlisted anticoagulant goes unrecognized and its plan is never checked.

## Known divergences

- **case_00002, default mode only.** Its newest H&P is titled "History & Phsyical". The rules do not match misspellings, so offline mode flags the older H&P as out of window and matches the oracle. In default mode the model reads the title as an H&P, the documentation issue disappears, and the case costs the category point only. The decision stays `NOT_CLEARED` because of the temperature exclusion. This is accepted rather than adding fuzzy matching.
- **case_00042, source only.** Its plan document names neither the drug nor "anticoag", so the oracle cites `documents`. It is the only plan candidate, so the engine cites the document's index. Source is not scored, and no metric changes.

## Assumptions and limitations

- **Policy source and H&P completeness.** The policy appendix of the take-home PDF is the only source of truth, and [DESIGN.md](DESIGN.md) records how each rule was read. The starter's prompt copy of the policy was removed from `core.py`. The H&P's "completed within" window is read against its date; clinical completeness is not judged from its text.
- **Anticoagulant list.** The policy does not define the class, so the list is ours and finite. It covers apixaban, rivaroxaban, edoxaban, dabigatran, warfarin, enoxaparin, dalteparin, fondaparinux, and heparin, with their common brand names. Names are matched by word inside the medication name, and a drug named only in document text is not a current medication. A name on neither this list nor the known non-anticoagulants (lisinopril, metformin) goes to the model in default mode and counts as not an anticoagulant offline.
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
| `tests/test_classify.py` | 112 |
| `tests/test_synthetic.py` | 102 |
| `tests/test_normalize.py` | 69 |
| `tests/test_settings.py` | 51 |
| `tests/test_llm.py` | 40 |
| `tests/test_contract.py` | 10 |
| `tests/test_golden.py` | 7 |
| `tests/test_determinism.py` | 1 |
| **Total** | **392** |

- **Golden.** Runs all sample cases in offline mode. It checks that the decision and the category set match the oracle. It also matches every issue both ways on category, source, and description, with one documented exception for case_00042's source. A separate check runs the harness's own per-case scorer, including grounding.
- **Synthetic.** Each test builds a minimal package for one path. Paths include exact window and threshold boundaries, same-day vitals, offsets, unparseable dates, lab status, and multiple consents. They also cover combined consent titles, per-drug and brand-name anticoagulation, the reviewers' probe strings, empty collections, and fully empty packages. Consent confirmation is tested through an injected resolver.
- **LLM layer.** A fake client records every call. The tests cover the strict schema, prompts, cache hits, bypassing the cache, force mode, retries, and the outage fallback, including a failed consent confirmation. They also cover rejected temperature and ten-call determinism. No test touches the network.
- **Mutation checks.** `scripts/mutation_check.py` applies 29 deliberate bugs to a temporary copy of the repo and runs the suite against each. The suite caught 29. The report, with the per-suite counts above, is in `results/mutation/mutation_report.json`.

## Process

[DESIGN.md](DESIGN.md) was written and committed before any code, and it records every decision and the oracle conventions checked against the data. Each milestone was then built against it in its own commits: scaffold and baseline capture, rules engine, LLM layer, and evidence runs. A separate reviewer session verified the work at each checkpoint, and its findings, including the rejected ones, are recorded in `DESIGN.md`.

**Policy provenance.** The starter's `BASELINE_SYSTEM_PROMPT` paraphrased the policy. It matched the PDF appendix on rules 1, 2, and 4 and on the final determination, but its Rule 3 dropped "the plan must clearly describe how the medication will be managed before and after the procedure" and "if the plan is incomplete or ambiguous." The designer and builder sessions did not have the PDF; they worked from that paraphrase and from `DESIGN.md`, and the builder's milestone 2 report described its comparison as "the PDF and DESIGN.md disagree" when it was comparing the paraphrase. The two dropped clauses reached the code through decision D4 in `DESIGN.md`, written by the planner from the PDF, and through `RULE_3_BULLETS` in `preop_triage/llm.py`, which quotes the PDF verbatim. The planner and validator sessions had the PDF, and the validator checked the code against it directly; its finding that the paraphrase drops the Rule 3 clause is recorded in `DESIGN.md`.
