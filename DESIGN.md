# DESIGN.md — Pre-Op Scheduling Triage

Reference document for the hybrid rules-plus-LLM implementation of
`triage_submission`. The policy text in `core.py` (`BASELINE_SYSTEM_PROMPT`) is
the only source of truth for rules. Every data claim below was checked against
`data/patients_sample_50.jsonl` and the scoring logic in `run_evals.py`.

## 1. Purpose and scope

This system evaluates a single patient submission package against the Cadence
pre-operative scheduling policy and returns a decision (`READY`,
`NEEDS_FOLLOW_UP`, `NOT_CLEARED`) plus a list of evidence-backed issues in the
schema defined by `core.TriageOutput`. It is scored by the unmodified harness
(`run_baseline.py`, `run_evals.py`, `view_report.py`, `Makefile`) against the
50-case oracle. The deliverable is a deterministic rules engine with a narrowly
scoped LLM fallback, an offline mode, an offline scorer, tests, and a captured
naive baseline for comparison.

## 2. Architecture

Data flow, submission to output:

```
submission (dict | PatientSubmission)
   |
   v
[normalize]  validate with PatientSubmission; parse dates (date-only, UTC);
   |         alias lab codes (LAB-CBC->CBC, LAB-CMP->CMP); pick most-recent
   |         item per kind (BP, temperature, CBC, CMP); strip document titles
   v
[classify]   title -> REQUIRED (H&P | CONSENT | ANTICOAG_PLAN)
   |                 | KNOWN_OTHER | UNKNOWN
   |         text  -> consent signed? plan clear? -> POS | NEG | UNPLACED
   v
[rules]      missing-data gates, Rule 1 (docs), Rule 2 (labs),
   |         Rule 3 (anticoagulation), Rule 4 (vitals)
   |         (only UNKNOWN titles / UNPLACED texts leave this layer)
   v
[LLM fallback]  default mode: classify UNKNOWN titles and UNPLACED texts,
   |            strict JSON schema, temperature 0, in-memory cache
   |            offline mode: UNKNOWN -> not a required type;
   |                          UNPLACED -> negative
   v
[assemble]   issues in fixed order, decision from issue categories,
             explanation = "CAT: desc | CAT: desc"
             -> TriageOutput
```

Decision derivation: any `ACUTE_SAFETY_EXCLUSION` issue gives `NOT_CLEARED`;
otherwise any issue gives `NEEDS_FOLLOW_UP`; no issues gives `READY`.

Issue order matches the oracle: missing procedure fields, documentation,
testing, anticoagulation, vitals (safety exclusion or missing vitals). Order is
not scored (see section 4) but keeps diffs readable.

## 3. Decisions

### D1. Hybrid architecture, rules first

A deterministic Python rules engine evaluates every rule. An LLM is used only
as a fallback for inputs the rules cannot place. An `offline` flag disables
the LLM entirely.

Rationale: every rule is date arithmetic or a threshold on a structured
field. The only fuzzy inputs are document titles and short free-text passages.

Rules out: a single LLM call over the whole package (the naive baseline), and
an LLM that produces or edits issues directly.

Constraint from the harness: `run_baseline.py` and `run_evals.py` call
`triage_submission(submission=..., model=...)` and nothing else, and they must
not change. Mode flags therefore cannot be arguments the harness passes. They
are environment variables: `PREOP_OFFLINE`, `PREOP_NO_CACHE`,
`PREOP_FORCE_LLM` (any of `1`, `true`, `yes` enables). The engine entrypoint
also accepts an optional keyword-only `settings` object that overrides the
environment, for tests. `triage_submission(submission, *, model)` keeps its
signature and gains only that optional keyword.

### D2. Rules decide whenever they match; the LLM handles only what they cannot place

This single principle governs both title classification (D3) and text
judgments (D4). A rule match is final and is never sent to the LLM for
confirmation.

Rules out: LLM-as-verifier of rule outputs; ensemble voting between rules and
LLM.

### D3. Three-outcome title classification

Normalize a document title: lowercase; strip known prefixes (`scanned`,
`imported:`, `preop -`, `pre-op`, `preop`) and known suffixes (`[pdf]`,
`(scanned)`, `(external)`, `- signed`), repeating both passes until nothing
matches. Match the remaining core against an explicit pattern list with
three outcomes:

- `REQUIRED`: H&P, surgical consent, anticoagulation plan.
- `KNOWN_OTHER`: nursing intake, anesthesia pre-assessment, clinic follow-up
  note, and similar. Never a required type; never sent to the LLM.
- `UNKNOWN`: reaches the LLM in default mode; treated as not-required in
  offline mode.

"Strict" means the pattern list is explicit. A misspelling such as
"History & Phsyical" fails to match by construction. Repeated prefix
stripping is required because the data contains doubled prefixes
("Pre-op Pre-op Evaluation [PDF]", "Pre-op Preop H&P (external)",
case_00048, case_00034).

Title families observed in the data (all 50 cases, 85 recognized H&P documents across 94 distinct titles):

- H&P cores: `h&p`, `h+p`, `h/p`, `h and p`, `history & physical`,
  `history/physical`, `history and physical`, `hist & phys`, `hx & physical`,
  optionally followed by `(h&p)`, `exam`, `examination`, `note`, `update`,
  and optionally preceded by `admission`, `clinic`, `consult`, `initial`,
  `outpatient`, `hospitalist`, `surgical`, `pre-anesthesia`, `preoperative`.
- Consent cores: any normalized title containing `consent` (8 variants, including
  "Consent Counseling Note" and "Pre-op Consent Discussion", which the oracle
  treats as consent documents: case_00044 flags one, case_00012 accepts one).
- Anticoagulation plan cores: `perioperative medication plan`,
  `perioperative medication review`, `anticoag plan`,
  `cardiology progress note - anticoag`. See section 4 for why the oracle's
  source attribution for this category is text-based, not title-based.
- KNOWN_OTHER: `anesthesia pre-assessment`, `clinic follow-up note`,
  `nursing intake`.
- UNKNOWN on this dataset (8 titles, always the older "prior" H&P document,
  never outcome-determining): "Medical Clearance [PDF]", "Preop Medical
  Clearance [PDF]", "Pre-op Evaluation [PDF]", "Pre-op Pre-op Evaluation
  [PDF]", "Imported: Preoperative Assessment [PDF]", "Pre-op Surgical
  Clearance Note [PDF]", "Preop Pre-anesthesia Evaluation [PDF]", "Scanned
  Pre-anesthesia Evaluation [PDF]". Plus "History & Phsyical" (case_00002).
  Decision: these titles stay UNKNOWN. They are not added to KNOWN_OTHER.

Rules out: fuzzy or edit-distance matching, embedding similarity, and
classifying by document text instead of title.

### D4. Text judgments use positive and negative keyword sets with word-boundary matching

Two judgments: is the consent signed, and is the anticoagulation plan clear.

- Any negative hit resolves NEGATIVE (rules decide).
- A positive hit with no negative hit resolves POSITIVE (rules decide).
- Neither resolves UNPLACED. Default mode sends it to the LLM. Offline mode
  resolves it NEGATIVE, which is the policy's own rule for ambiguous plans.
- The positive set for plan clarity requires both hold language and resume
  language, because the policy requires management "before and after" the
  procedure. "plan" alone is never a positive keyword.

Substring traps observed in the data (word-boundary matching plus
negative-wins handles all three):

- "Unsigned consent noted; signature not yet on file." contains "signed"
  (case_00044).
- "Apixaban listed; perioperative management plan to be finalized." contains
  "plan" (case_00038).
- "Anticoagulation mentioned; no clear hold/resume guidance documented."
  contains both "hold" and "resume" (case_00004). The negative phrase
  "no clear" must win.

Negative set for plan clarity includes "no clear", "not yet",
"to be finalized", and "pending"; these cover six of the seven plan texts.
Case_00000's text ("Patient takes apixaban for atrial fibrillation. Follow up
with cardiology for peri-op recommendations.") matches neither set and goes
UNPLACED: the LLM judges it in default mode, offline mode resolves it
negative, and both modes reach the same flagged issue. "follow up" and
"recommendations" are not negative keywords (section 8). Negative phrases present in the three
unsigned consent texts: "unsigned" (all three), "awaiting", "not yet on file",
"requested signature". Positive consent phrases: "signed", "signature on
file". Note "signature" alone appears in both positive and negative texts and
must not be a positive keyword on its own.

Rules out: sentiment or embedding classifiers; treating title suffix
"- signed" as evidence of a signed consent (it appears on H&P titles only).

### D5. Case_00002 diverges from the oracle on purpose

Its most recent H&P is titled "History & Phsyical" (doc[0], 2026-02-21). The
older "prior" H&P (doc[1], "Scanned History and Physical Examination",
2026-01-30) is 32 days before the 2026-03-03 procedure.

- Offline mode: doc[0] is UNKNOWN, doc[1] is the most recent recognized H&P,
  it is flagged outside the 30-day window, and the output matches the oracle.
- Default mode: the LLM classifies doc[0] as an H&P (10 days prior, in
  window), the documentation issue disappears, and the category set becomes
  `{ACUTE_SAFETY_EXCLUSION}` instead of
  `{ACUTE_SAFETY_EXCLUSION, REQUIRED_DOCUMENTATION}`. The decision stays
  `NOT_CLEARED` because of the 101.0 F temperature. Cost: the category point
  only (1.0 of 3.5 on that case, 0.57 points on the 50-case aggregate).

This is the one place on this dataset where the fallback fires and changes the
result. We accept the loss and document it rather than adding fuzzy matching.

Rules out: fuzzy title matching; special-casing this title.

### D6. Procedure date comes only from `procedure.procedure_date`

The oracle ignores the "Procedure date target: YYYY-MM-DD" sentence in H&P
text. Verified on case_00024: its H&P says "Procedure date target:
2026-03-25", the structured field is null, and the oracle reports the date as
missing. Also case_00000, case_00031, case_00046.

When the structured field is null, quote the free-text target in the issue
details so a human reviewer sees it. `MISSING_REQUIRED_DATA` issues skip the
grounding metric (`_check_issues_value_grounding`), so this is free.

Rules out: parsing dates from documents; using
`metadata.submission_received_at` as a fallback (it equals the procedure date
in all 46 dated cases, but it is not the policy's field).

### D7. Python; harness files untouched

The offline scorer imports `_local_metrics_for_row` and
`_summarize_local_rows` from `run_evals.py` rather than copying them, so
offline numbers are provably the official numbers. Note that importing
`run_evals` imports `openai` at module load, so the package must be installed
even though the offline scorer makes no network call. `make evals` itself
always creates a remote OpenAI eval and needs an API key; the offline scorer
is what makes keyless iteration possible.

The existing tests in `tests/test_triage_submission.py` test the baseline's
exact API call (Responses API kwargs, prompt shape, schema `strict: False`),
not the harness contract. They will be replaced with tests of the new
contract, not deleted.

Rules out: forking or vendoring the harness; a second scoring implementation.

### D8. Capture the naive baseline before any code changes

`.gitignore` ignores `data/baseline_outputs.jsonl`, `data/eval_report.json`,
and `data/determinism_report.json`. The naive run must be written to a
separate folder via the Makefile variables `OUTPUT`, `REPORT`, and
`DETERMINISM_REPORT` (all three exist and are overridable) and that folder
(`results/naive/`) committed.

Rules out: editing `.gitignore` or the Makefile to un-ignore the defaults.

## 4. Oracle conventions (verified against all 50 cases)

Legend: VERIFIED, CORRECTED, UNVERIFIABLE (the data cannot distinguish the
claim from an alternative).

**Decision mix.** VERIFIED. 40 `NEEDS_FOLLOW_UP`, 7 `NOT_CLEARED`, 3 `READY`
(case_00012, case_00029, case_00041). Category counts: MISSING_REQUIRED_DATA
17, REQUIRED_DOCUMENTATION 14, REQUIRED_TESTING 13, ANTICOAGULATION_MANAGEMENT
7, ACUTE_SAFETY_EXCLUSION 7.

**NOT_CLEARED cases still list every other issue.** VERIFIED on case_00002
only; the other six NOT_CLEARED cases (3, 18, 20, 30, 39, 47) have no other
issue to list.

**Null `procedure_date`.** CORRECTED. The oracle emits the missing-date issue
and does not run date-window rules, but non-date rules still run: case_00000
has a null date and still gets an ANTICOAGULATION_MANAGEMENT issue. Cases
24, 31, 46 emit only the missing-date issue, but they also have a signed
consent, normal vitals, and no anticoagulant, so nothing else could fire.
Whether H&P-presence, consent, and CBC-presence checks run with a null date is
UNVERIFIABLE (all four cases have an H&P, a consent, and a CBC). Decision:
with a null date, skip only the window comparisons (H&P 30-day, CBC/CMP 14-
or 30-day). Presence checks for H&P, consent, and required labs still run,
because they do not need the date and the policy's missing-field rule does
not excuse them. Anticoagulation and vitals rules run as normal. This is an
assumption the sample cannot confirm.

**Null `procedure_risk`.** VERIFIED as far as the data allows. Cases 14, 22,
34, 49 emit only the missing-risk issue. All four have an in-window H&P,
signed consent, normal vitals, and a CBC 8 to 10 days old, so "skip lab
checks" is UNVERIFIABLE against "run lab checks and they pass". Design choice:
skip Rule 2 entirely when risk is null; run every other rule.

**Empty vitals.** VERIFIED. Cases 4, 28, 35 have `vitals: []` and get two
MISSING_REQUIRED_DATA issues, both with source `vitals`, descriptions
"Missing latest blood pressure" and "Missing latest temperature". Decision is
NEEDS_FOLLOW_UP, not NOT_CLEARED. Case_00004 also shows these coexist with an
anticoagulation issue. Only `blood_pressure` and `temperature` vital types
exist in the data.

**Anticoagulant with `active: null`.** VERIFIED. Cases 1, 23, 37: warfarin
with `active: null` gives MISSING_REQUIRED_DATA, source `medications[1]`,
description "Unknown anticoagulant active status", and no
ANTICOAGULATION_MANAGEMENT issue. No case has an anticoagulant with
`active: false`, so the inactive-anticoagulant path is UNVERIFIABLE.

**Lab code aliasing and recency.** VERIFIED. `LAB-CBC` counts as CBC
(case_00002 and case_00012 have only LAB-CBC and no testing issue; case_00043
reports a LAB-CBC row as "CBC" at `labs[0]`). `LAB-CMP` counts as CMP
(case_00019 reports a LAB-CMP row as "CMP" at `labs[2]`). All 150 lab rows have
`status: "final"`, so status filtering is UNVERIFIABLE. Labs are listed
newest-first in every case, so "most recent by `effective_at`" is
UNVERIFIABLE against "first in list"; the policy says most recent, so sort by
`effective_at`.

**Anticoagulants in the data.** VERIFIED. Medication names are lisinopril
(50, active), apixaban (7, active), metformin (4, active), warfarin (3, active
null). Only apixaban and warfarin are anticoagulants; lisinopril and metformin
are noise.

**Anticoagulation plan documents.** VERIFIED that all seven plan-bearing
cases (0, 4, 6, 21, 26, 38, 42) have a vague plan and all are flagged with
identical description and details. CORRECTED on source attribution: the
oracle locates the plan document by text, not title. Case_00042 has a document
titled "Perioperative Medication Plan" at index 4, yet the oracle reports
source `documents` (no index). Its text says "blood thinner" and never names
the drug or "anticoag". Cases 0 and 6 have the same title, texts naming
"apixaban" / "Anticoagulant", and get `documents[4]`. So: source is
`documents[i]` when a document's text mentions the anticoagulant or
"anticoag", else `documents`. Decision: locate the plan document by text
(mentions the active anticoagulant by name, or "anticoag"), matching the
oracle. A document whose title classifies as a plan but whose text names
neither is still examined for clarity, and if it is the only candidate it is
cited as `documents[i]`. On case_00042 this yields `documents[4]` where the
oracle cites `documents`; source is not scored, so no metric is affected
(section 5).

**Consent "not clearly signed" texts.** VERIFIED. All three (cases 8, 17,
44) contain the word "unsigned"; they also contain "awaiting", "requested
signature", and "not yet on file" respectively. Three cases (11, 25, 33) have
no consent document at all and get source `documents`. No case has two consent
documents, so consent tie-breaking is UNVERIFIABLE.

**H&P selection.** VERIFIED that the oracle uses the most recent recognized
H&P and flags it when out of window (case_00007: doc[0] 2026-01-22 is flagged
although an older H&P exists at doc[1]). The newer H&P is always at a lower
index than the older one, so "most recent by date" is UNVERIFIABLE against
"first recognized". Three cases (13, 27, 36) have no H&P and get source
`documents`. Documents are not date-sorted (the second document is always
older than the first), so the builder must sort by date and not by index.

**Day arithmetic.** VERIFIED. Days are calendar-date differences ignoring
time of day: case_00002 gives 32 for 2026-01-30 to 2026-03-03; case_00010
gives 20 for `2026-02-19T09:10:00Z` to 2026-03-11. Boundaries are
UNVERIFIABLE: the largest accepted H&P gap is 10 days and the smallest
rejected is 32; the largest accepted lab gap is 8 (both risk levels) and the
smallest rejected is 18. No document or lab is dated after its procedure date.
Design choice: "within N days" means `0 <= gap <= N`; a future-dated item is a
gap below zero and is treated as outside the window.

**Vitals.** VERIFIED. Most recent BP and most recent temperature are judged
separately. The flagged BP is 184/111 in all four BP cases (3, 20, 39, 47)
and the older BP is 179/107, one unit under each threshold. Flagged
temperature is 101.0 in all three temperature cases (2, 18, 30). BP and
temperature timestamps never tie (10:12 vs 10:15), and vitals are listed
oldest-first in every case, so tie handling and "most recent by date" versus
"last in list" are UNVERIFIABLE. All vitals fall within 12 days of the
procedure; no "at the time of review" date filter is testable.

**Evidence detail shapes.** Recorded below. CORRECTED in one important
respect: the oracle's own expected outputs, scored through
`_local_metrics_for_row`, reach only 93.71% because 22 of 50 cases fail
`issues_value_grounding`. Three oracle shapes cannot ground under the harness
rules, so the builder must extend them rather than copy them. The grounding
rule: for each non-MISSING issue, `details` must contain (a) a string field
value of at least 4 characters from the object at `source`, (b) a numeric field
value whose string form is at least 4 characters, (c) a `YYYY-MM-DD` date that
is a prefix of a date field, or (d) failing all that, any such value from any
object in the category's collection (labs, vitals, documents, or documents
plus medications). One ungrounded issue fails the whole case's 0.5.

| Category / source | Oracle description | Oracle details shape | Grounds? | Builder extension |
|---|---|---|---|---|
| MISSING_REQUIRED_DATA / `procedure.procedure_date` | Missing procedure date | `procedure.procedure_date is null` | skipped | append free-text target (D6) |
| MISSING_REQUIRED_DATA / `procedure.procedure_risk` | Missing procedure risk | `procedure.procedure_risk is null` | skipped | none |
| MISSING_REQUIRED_DATA / `vitals` | Missing latest blood pressure / Missing latest temperature | `No blood_pressure vital with valid date found` / `No temperature vital with valid date found` | skipped | none |
| MISSING_REQUIRED_DATA / `medications[i]` | Unknown anticoagulant active status | `Medication warfarin has active=null; cannot determine if currently taking` | skipped | none |
| REQUIRED_DOCUMENTATION / `documents[i]` | H&P outside 30-day window | `H&P date 2026-01-30 vs procedure_date 2026-03-03 (32 days prior; must be within 30)` | yes (date) | none |
| REQUIRED_DOCUMENTATION / `documents` | History and Physical document missing | `No History and Physical document with valid date found` | **no** | list the documents that are present, with titles and dates |
| REQUIRED_DOCUMENTATION / `documents` | Signed surgical consent missing | `No Surgical Consent document found` | **no** | same |
| REQUIRED_DOCUMENTATION / `documents[i]` | Surgical consent not clearly signed | `Consent document text does not clearly indicate signed consent: <full text>` | yes (text) | none |
| REQUIRED_TESTING / `labs` | CBC missing / CMP missing | `No CBC result with valid effective_at found for procedure_risk LOW` | **no** | list the labs that are present, with codes and dates |
| REQUIRED_TESTING / `labs[i]` | CBC outside 14-day window for HIGH risk procedure | `CBC effective_at 2026-02-19T09:10:00Z vs procedure_date 2026-03-11 (20 days prior; must be within 14)` | yes (date) | none |
| ANTICOAGULATION_MANAGEMENT / `documents[i]` or `documents` | Missing perioperative anticoagulation plan | `Active anticoagulant medication present (medications[1]) but no clear perioperative plan document found` | **no** | name the drug (grounds via medications) and quote the document text with its date when one exists |
| ACUTE_SAFETY_EXCLUSION / `vitals[i]` | Blood pressure meets exclusion threshold | `latest BP systolic=184, diastolic=111; threshold systolic>=180 or diastolic>=110` | **no** (3-digit numbers fail the 4-character minimum) | append the vital's date |
| ACUTE_SAFETY_EXCLUSION / `vitals[i]` | Temperature exceeds exclusion threshold | `latest temperature value_f=101.0; threshold is > 100.4` | yes ("101.0") | append the date anyway |

Shapes not present in the data and therefore UNVERIFIABLE: CBC outside the
30-day window for LOW/MODERATE risk, a lab or H&P dated after the procedure,
an inactive anticoagulant, a null document date.

When the relevant collection is empty (`labs: []` with a missing CBC,
`documents: []` with a missing consent or H&P), there is nothing to cite and
the fuzzy path scans that same empty collection, so the grounding half-point
is unreachable under the harness rules. Accepted; noted in the README. Every
sample case has at least one lab and one document.

Explanation string: `"CAT: description | CAT: description"` in issue order.
READY explanation: `"All required documentation, testing, anticoagulation
planning, and safety checks are satisfied."` Not scored.

**Scoring.** VERIFIED from `METRIC_WEIGHTS` and `_local_metrics_for_row`.
Per case out of 3.5: `json_schema_valid` 1.0, `decision_match_oracle` 1.0,
`issue_categories_match_oracle` 1.0 (compared as sorted unique category sets,
so duplicates and order do not matter), `issues_value_grounding` 0.5
(all-or-nothing per case). An extra issue in a category the oracle does not
list costs the full category point; an extra issue in an already-listed
category costs nothing unless it fails grounding. The `source` string must
match `^[a-z_]+(\[\d+\])?(\.[a-z_]\w*)?$` after lowercasing, with base in
`documents|labs|vitals|medications|conditions|procedure|patient|metadata`;
anything else resolves to nothing and falls through to the fuzzy path.

## 5. Known divergences from the oracle

1. **case_00002, default mode only.** Described in D5. Offline mode matches.
2. **case_00042, source only.** The document titled "Perioperative Medication
   Plan" at index 4 names neither the drug nor "anticoag", so the oracle cites
   `documents`. It is the only plan candidate, so we cite `documents[4]`.
   Category, decision, and grounding are unaffected; no metric changes.
3. **Detail strings.** By design the builder's details are supersets of the
   oracle's shapes (extra dates, drug names, present-item lists) so that they
   ground. Details are never compared character for character.
4. **UNKNOWN "prior" titles in default mode.** The eight clearance/evaluation
   titles listed in D3 go to the LLM. Whatever it answers, they are older
   than a recognized H&P in every case, so the output cannot change on this
   dataset. On a future case where such a document is the only or newest H&P
   candidate, default and offline modes would differ.

## 6. Blast radius: what the LLM can and cannot change

The LLM can:

- Turn an UNKNOWN title into H&P, CONSENT, ANTICOAG_PLAN, or OTHER. This can
  add or remove a REQUIRED_DOCUMENTATION or ANTICOAGULATION_MANAGEMENT issue
  and can change which document index is cited.
- Turn an UNPLACED text into POSITIVE or NEGATIVE. This can add or remove a
  "consent not clearly signed" issue or an anticoagulation issue.

The LLM cannot:

- Change any date arithmetic, lab window, vital threshold, or missing-data
  gate.
- Produce, edit, or reorder issues, descriptions, sources, or details. It
  returns an enum from a strict schema; the rules layer writes the issue.
- Override a rule match. Anything the rules placed is final.
- Change the decision except through the two issue types above. It can never
  produce or remove an ACUTE_SAFETY_EXCLUSION, so it can never flip a case
  into or out of NOT_CLEARED.

Consequences: the maximum per-case damage from a wrong LLM answer is one
category point plus the grounding half-point. With the in-memory cache, the
harness determinism run (`make determinism`, record 0, 10 calls in one
process) exercises the LLM at most once per distinct title or text; the
stability script with `no-cache` is the real measure of model variance.
Case_00000, the determinism record, does reach the LLM in default mode: its
doc[1] title "Preop Pre-anesthesia Evaluation [PDF]" is UNKNOWN, and its plan
text is UNPLACED. Either LLM answer leaves the anticoagulation issue in place
(no hold/resume language exists to make the plan clear), so both modes reach
the same output on this record.

## 7. Milestone plan

1. **Baseline capture and scaffold.** Naive run committed to `results/naive/`
   via `make baseline evals determinism OUTPUT=... REPORT=...
   DETERMINISM_REPORT=...`. Package layout. `triage_submission` signature
   preserved (`submission`, keyword-only `model`). Offline scorer importing
   from `run_evals`. Replacement tests.
2. **Rules engine.** Normalization, three-outcome classifier, rules 1 to 4
   and missing-data gates. Golden test over all 50 cases in offline mode
   matching on decision, category set, and per-issue source and description
   (details are checked by the grounding metric, not character for
   character). Synthetic tests for: complete anticoagulation plan, lab dated
   after the procedure, exact boundaries (30 days, 14 days, 180 systolic,
   110 diastolic, 100.4 F), tied vital timestamps, inactive anticoagulant,
   null document date, and the three substring-trap strings from D4.
   Offline scorer must show 100% decision and category match and 100%
   grounding on all 50 cases.
   **Verifier checkpoint.**
3. **LLM layer.** Classifier for UNKNOWN titles and UNPLACED text, strict
   schema, temperature 0, in-memory cache keyed on title and text, the
   `PREOP_OFFLINE`, `PREOP_NO_CACHE`, and `PREOP_FORCE_LLM` environment
   variables with the `settings` override (D1), and a
   stability script reporting self-agreement across runs and agreement with
   the rules on documents the rules placed.
4. **Harness run in both modes**, determinism in both modes, stability
   numbers, README with build and run instructions.
   **Verifier checkpoint.**

## 8. Rejected findings

Reasons for rejecting verifier findings are recorded here.

1. **Add "follow up" and "recommendations" to the negative plan-clarity set
   (proposed during design review to keep case_00000 out of the LLM).**
   Rejected: "follow up" and "recommendations" are not negative signals about
   a plan on their own; adding them fits one string. Case_00000's plan text
   goes UNPLACED, and both modes reach the same flagged issue.

## 9. Open questions

All questions raised during design review are resolved.

1. **Flag transport.** Resolved: `PREOP_OFFLINE`, `PREOP_NO_CACHE`,
   `PREOP_FORCE_LLM` environment variables, with an optional keyword-only
   `settings` override on the engine entrypoint for tests (D1).
2. **The eight clearance/evaluation titles.** Resolved: they stay UNKNOWN
   (D3).
3. **"Follow up" as a negative plan keyword.** Rejected; see section 8.
4. **Window boundaries.** Resolved: inclusive. A gap of exactly N days is
   within N days. Assumption, not verifiable from the sample (largest accepted
   gaps are 10 and 8 days, smallest rejected are 32 and 18).
5. **Missing-item grounding with an empty collection.** Accepted as
   unreachable under the harness rules; noted in the README (section 4).
