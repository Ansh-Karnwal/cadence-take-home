# DESIGN.md — Pre-Op Scheduling Triage

Reference document for the hybrid rules-plus-LLM implementation of
`triage_submission`. The Cadence Surgical Center Pre-Operative Scheduling
Policy in the appendix of the take-home PDF is the only source of truth for
rules. The starter's `BASELINE_SYSTEM_PROMPT` in `core.py` paraphrased it and
was removed in milestone 4 once nothing referenced it; where they differed,
the PDF won. Every data claim below was checked against
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

Decision derivation: any `ACUTE_SAFETY_EXCLUSION` issue gives `NOT_CLEARED`;
otherwise any issue gives `NEEDS_FOLLOW_UP`; no issues gives `READY`.

Issue order matches the oracle: missing procedure fields, documentation,
testing, anticoagulation, vitals (safety exclusion or missing vitals). Order is
not scored (see section 4) but keeps diffs readable.

## 3. Decisions

### D1. Hybrid architecture, rules first

A deterministic Python rules engine evaluates every rule. An LLM is used only
as a fallback for inputs the rules cannot place, and to confirm
keyword-positive consents (D2). An `offline` flag disables the LLM entirely.

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

### D2. Keywords can add an issue but never remove one

The rules decide whatever they can place, and the LLM handles what they
cannot. In default mode one asymmetry governs every judgment that could clear
a patient: keywords can add an issue but never remove one.

- Titles (D3): a rule match is final and is never sent to the LLM.
- Consent (D4): a keyword-NEGATIVE consent is final. A keyword-POSITIVE
  consent is sent to the LLM for confirmation and clears only if the LLM also
  says signed. UNPLACED goes to the LLM.
- Plan clarity (D4): keywords can only say NEGATIVE or UNPLACED; only the LLM
  can say clear.

Offline mode has no LLM, so it trusts keyword-POSITIVE consents and never
finds a plan clear (section 5).

Rules out: the LLM overriding a keyword NEGATIVE; the LLM re-labelling a
title the rules placed; ensemble voting between rules and LLM.

### D3. Three-outcome title classification

Normalize a document title: lowercase; collapse whitespace; rewrite `h & p`
to `h&p`; strip known prefixes (`scanned`, `imported:`, `preop -`, `pre-op`,
`preop`) and known suffixes (`[pdf]`, `(scanned)`, `(external)`, `- signed`),
repeating both passes until nothing matches. A prefix must be followed by
whitespace unless it ends in its own separator, so `preop` never eats
`preoperative`. Match the remaining core against an explicit pattern list
with three outcomes:

- `REQUIRED`: H&P, surgical consent, anticoagulation plan.
- `KNOWN_OTHER`: nursing intake, anesthesia pre-assessment, clinic follow-up
  note, consents that name another kind (below), and similar. Never a
  required type; never sent to the LLM.
- `UNKNOWN`: reaches the LLM in default mode; treated as not-required in
  offline mode.

Every title is classified once per evaluation, and the result is shared by
Rules 1 and 3, so the fallback is called at most once per title.

"Strict" means the pattern list is explicit. A misspelling such as
"History & Phsyical" fails to match by construction. Repeated prefix
stripping is required because the data contains doubled prefixes
("Pre-op Pre-op Evaluation [PDF]", "Pre-op Preop H&P (external)",
case_00048, case_00009).

Title families (all 50 cases, 85 recognized H&P documents; 107 distinct
titles in all):

- H&P cores: `h&p`, `h+p`, `h/p`, `h and p`, `history & physical`,
  `history/physical`, `history and physical`, `hist & phys`, `hx & physical`,
  optionally followed by any of `(h&p)`, `exam`, `examination`, `note`,
  `update`, `addendum`, and optionally preceded by one of `admission`,
  `clinic`, `consult`, `initial`, `interval`, `outpatient`, `hospitalist`,
  `surgical`, `pre-anesthesia`, `preoperative`. `interval` and `addendum` do
  not occur in the sample; they were added after the first review as common
  explicit variants.
- Consent: any normalized title containing the word `consent` is a surgical
  consent, unless it also contains one of `anesthesia`, `anaesthesia`,
  `research`, `blood`, `transfusion`, `photo`, `withdrawn`, `refused`,
  `revoked` (matched at word start), in which case it is KNOWN_OTHER. The
  exclusion does not apply when the title also contains `surgical` or
  `surgery`: "Surgical and Anesthesia Consent" is a surgical consent.
  Assumption: the policy requires a surgical consent, and a consent document
  that does not name another kind is taken to be the surgical one. The 8 sample variants include
  "Consent Counseling Note" (case_00044 flags one, case_00012 accepts one)
  and "Pre-op Consent Discussion" (case_00003 accepts one).
- Anticoagulation plan: any normalized title containing `anticoag` (which
  covers `anticoagulation`), or starting with `perioperative medication`.
  Sample cores: `perioperative medication plan`, `perioperative medication
  review`, `anticoag plan`, `cardiology progress note - anticoag`. See
  section 4 for why plan documents are located by text first.
- KNOWN_OTHER: `anesthesia pre-assessment`, `clinic follow-up note`,
  `nursing intake`, plus the excluded consent kinds above.
- UNKNOWN on this dataset (8 titles, always the older "prior" H&P document,
  never outcome-determining): "Medical Clearance [PDF]", "Preop Medical
  Clearance [PDF]", "Pre-op Evaluation [PDF]", "Pre-op Pre-op Evaluation
  [PDF]", "Imported: Preoperative Assessment [PDF]", "Pre-op Surgical
  Clearance Note [PDF]", "Preop Pre-anesthesia Evaluation [PDF]", "Scanned
  Pre-anesthesia Evaluation [PDF]". Plus "History & Phsyical" (case_00002).
  Decision: these titles stay UNKNOWN. They are not added to KNOWN_OTHER.

Rules out: fuzzy or edit-distance matching, embedding similarity, and
classifying by document text instead of title.

### D4. Text judgments: clause-aware consent keywords confirmed by the model; plan clarity only by resolver

Two judgments: is the consent signed, and is the anticoagulation plan clear
for a given drug. Both use case-insensitive word-boundary matching, and both
split text into clauses on `.`, `;`, and `:`.

**Consent signed.** Sign-words: `sign`, `signed`, `signature`. Negatives come
in two tiers.

- Tier 1 negation tokens count only in a clause that also has a sign-word:
  `not`, `no`, `never`, `unsigned`, `missing`, `pending`, `awaiting`,
  `to be`, `will be`, `refused`, `declined`, `withdrawn`, `outstanding`,
  `required`.
- Tier 2 bare negatives count in any clause: `unsigned`, `withdrew`,
  `withdrawn`, `rescinded`, `revoked`, `void`, `new consent required`, and a
  blank signature line of three or more underscores. These need no sign-word
  because the negation often sits in a clause of its own ("Patient later
  withdrew consent.", "new consent required for planned procedure.",
  "Signed: ______").
- A clause is NEGATIVE if it has a sign-word and a tier 1 token, or any tier
  2 negative. A clause is POSITIVE if it contains `signed` or `signature on
  file` and no tier 1 token.
- Any NEGATIVE clause makes the text NEGATIVE. Otherwise any POSITIVE clause
  makes it POSITIVE. Otherwise it is UNPLACED.
- Empty or missing text is NEGATIVE without a resolver call.
- Accepted cost of `required` as a tier 1 token: "Consent signed as required
  by policy." is NEGATIVE. A false follow-up is the safe direction.

In default mode the judged consent (the most recent) is then routed by D2: a
keyword-NEGATIVE text is final; a keyword-POSITIVE text goes to the LLM and
clears only if the LLM also says signed; an UNPLACED text goes to the LLM. If
the LLM call fails, the keyword answer stands, so an outage during a
confirmation keeps the consent signed, which is exactly offline behavior.
Offline mode trusts keyword positives.

Clauses keep a negation in one clause from cancelling a signature in
another ("Consent signed by patient; no complications." is POSITIVE), while
"Patient declined to sign consent." is NEGATIVE. Keyword positives are still
model-confirmed in default mode because no keyword set can list every way a
signed-looking text is not a valid patient signature.

**Plan clear.** The rules can only decide NEGATIVE or UNPLACED. There is no
positive keyword set. A plan is clear only when a resolver says so: the LLM
in default mode, never in offline mode.

- Negative tokens: `no clear`, `not yet`, `to be finalized`, `pending`,
  `unclear`, `whether`, `awaiting`, `tbd`.
- A negative token counts only in a clause that also mentions the drug by
  word, `anticoag`, `hold`, `resume`, `bridge`, `plan`, `management`,
  `perioperative`, `peri-op`, or `periop`. The last four were added after
  the first review so that "perioperative management details not yet
  documented" is rule-decided.
- Any counted negative makes the text NEGATIVE. Otherwise it is UNPLACED.
  Empty or missing text is NEGATIVE without a resolver call.

Rationale: the policy requires a plan that "clearly describe[s] how the
medication will be managed before and after the procedure". Any keyword test
for clarity clears patients on text like "Cardiology to advise whether to
hold or resume apixaban." Only a reader of the whole text can judge clarity.
Consequence, recorded as a limitation: offline mode never clears a patient on
an active anticoagulant.

Substring traps observed in the data (word-boundary matching plus
negative-wins handles all three):

- "Unsigned consent noted; signature not yet on file." contains "signed"
  (case_00044).
- "Apixaban listed; perioperative management plan to be finalized." contains
  "plan" (case_00038).
- "Anticoagulation mentioned; no clear hold/resume guidance documented."
  contains both "hold" and "resume" (case_00004). The negative phrase
  "no clear" must win.

Effect of scoping on the seven sample plan texts: six resolve NEGATIVE
(case_00004 "no clear" beside "hold"; cases 6, 21, 26 "not yet" beside
"perioperative management"; case_00038 "to be finalized" beside "plan";
case_00042 "pending" beside "plan"). One goes UNPLACED: case_00000 ("Follow
up with cardiology for peri-op recommendations."), which has no negative
token. Offline mode flags all seven. In default mode one of the seven
anticoagulation outcomes rests on the LLM's answer. "follow up" and
"recommendations" are not negative keywords (section 8).

The three unsigned sample consents all contain "unsigned". "signature" alone
appears in both positive and negative sample texts and is never positive on
its own.

Rules out: a positive keyword set for plan clarity; sentiment or embedding
classifiers; treating title suffix "- signed" as evidence of a signed consent
(it appears on H&P titles only).

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
offline numbers are provably the official numbers. Importing `run_evals`
imports `openai` at module load, so the package had to be installed even
though the offline scorer makes no network call. `make evals` itself always
creates a remote OpenAI eval and needs an API key; the offline scorer is what
made keyless iteration possible.

The starter's tests in `tests/test_triage_submission.py` tested the
baseline's exact API call (Responses API kwargs, prompt shape, schema
`strict: False`), not the harness contract. They were replaced with tests of
the new contract, not simply deleted.

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
`status: "final"`, so status filtering is UNVERIFIABLE. Decision (first
review): a lab counts only if `status` is null or one of `final`, `amended`,
`corrected` (case-insensitive). Other statuses (for example `preliminary`)
are ignored for selection and listed, marked ignored, in the missing-lab
details. Labs are listed newest-first in every case, so "most recent by
`effective_at`" is UNVERIFIABLE against "first in list"; the policy says most
recent, so sort by `effective_at`.

**Anticoagulants.** VERIFIED for the data: medication names are lisinopril
(50, active), apixaban (7, active), metformin (4, active), warfarin (3, active
null); lisinopril and metformin are noise. Decision (first review): the
anticoagulant list is apixaban, rivaroxaban, edoxaban, dabigatran, warfarin,
enoxaparin, dalteparin, fondaparinux, heparin, plus the brand names eliquis,
xarelto, savaysa, lixiana, pradaxa, coumadin, jantoven, lovenox, fragmin,
arixtra, each mapped to its generic. Names are matched case-insensitively by
word boundary inside the medication `name` string, so "Apixaban 5 mg BID" and
"Eliquis 5 mg BID" both match. The generic is the drug's identity for the
plan question and the cache key; plan candidates and plan scope words accept
the generic or any of its brands, so a medication listed as Eliquis finds a
plan that says "hold apixaban". Assumption: the policy does not define the
class, so this list is ours, and it is finite.

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

Decision (first review): Rule 3 runs once per active anticoagulant. A
document is a text candidate for a drug if its text names that drug by word
or contains `anticoag`; title-classified plan documents remain candidates for
every drug, after the text candidates. Each drug needs its own clear plan
(D4: only a resolver can say a plan is clear). One ANTICOAGULATION_MANAGEMENT
issue is emitted per unmet drug, citing that drug's most recent text
candidate, else its most recent title candidate, else `documents`. Details
quote the medication's full `name` string, not the matched word, so the
`documents` form grounds through the medications collection.

**Consent "not clearly signed" texts.** VERIFIED. All three (cases 8, 17,
44) contain the word "unsigned"; they also contain "awaiting", "requested
signature", and "not yet on file" respectively. Three cases (11, 25, 33) have
no consent document at all and get source `documents`. No case has two consent
documents, so consent tie-breaking is UNVERIFIABLE. Decision (first review):
the most recent consent document is the one judged, same as H&P. A newer
unsigned consent is not rescued by an older signed one. A consent has no
date window and counts as present regardless of its date; undated consents
are judged only when no consent has a date, and then the last one listed.

**H&P selection.** VERIFIED that the oracle uses the most recent recognized
H&P and flags it when out of window (case_00007: doc[0] 2026-01-22 is flagged
although an older H&P exists at doc[1]). The newer H&P is always at a lower
index than the older one, so "most recent by date" is UNVERIFIABLE against
"first recognized". Three cases (13, 27, 36) have no H&P and get source
`documents`. Documents are not date-sorted (the second document is always
older than the first), so the builder must sort by date and not by index.

**Day arithmetic and ordering.** VERIFIED. Days are calendar-date
differences ignoring time of day: case_00002 gives 32 for 2026-01-30 to
2026-03-03; case_00010 gives 20 for `2026-02-19T09:10:00Z` to 2026-03-11.
Decision (first review): day arithmetic uses the item's own local calendar
date, dropping any offset rather than converting it, because the procedure
date is a local calendar date (`2026-01-31T20:00:00-05:00` is 31 days before
2026-03-03, not 30). Ordering for "most recent" uses the full instant when a
time is present: offsets are converted to UTC, date-only values order as
midnight UTC, naive datetimes are taken as UTC, and ties go to the later list
index. Boundaries are
UNVERIFIABLE: the largest accepted H&P gap is 10 days and the smallest
rejected is 32; the largest accepted lab gap is 8 (both risk levels) and the
smallest rejected is 18. No document or lab is dated after its procedure date.
Design choice: "within N days" means `0 <= gap <= N`; a future-dated item is a
gap below zero and is treated as outside the window. A future-dated H&P or
lab is still the most recent and is flagged out of window.

**Unparseable item dates.** No sample item has one. Decision: a vital, lab,
or document whose date field is present but does not parse (including a
blank string) raises a MISSING_REQUIRED_DATA issue with source at that item
("Unparseable vital date", "Unparseable lab date", "Unparseable document
date") and details quoting the raw value. The item is excluded from
most-recent selection, and the issue is what keeps the case from READY. A
null date keeps its behavior: excluded from selection, no issue of its own.

**Vitals.** VERIFIED. Most recent BP and most recent temperature are judged
separately. The flagged BP is 184/111 in all four BP cases (3, 20, 39, 47)
and the older BP is 179/107, one unit under each threshold. Flagged
temperature is 101.0 in all three temperature cases (2, 18, 30). BP and
temperature timestamps never tie (10:12 vs 10:15), and vitals are listed
oldest-first in every case, so tie handling and "most recent by date" versus
"last in list" are UNVERIFIABLE. All vitals fall within 12 days of the
procedure; no "at the time of review" date filter is testable. Decision
(first review): no age limit on vitals. Policy gap: the policy says "at the
time of pre-operative review" and sets no window, so the most recent reading
counts however old it is.

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
| ANTICOAGULATION_MANAGEMENT / `documents[i]` or `documents` | Missing perioperative anticoagulation plan | `Active anticoagulant medication present (medications[1]) but no clear perioperative plan document found` | **no** | quote the medication's full `name` string (grounds via medications) and quote the document text with its date when one exists; one issue per unmet drug |
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
5. **Offline mode never clears a patient on a recognized anticoagulant
   (limitation).** "Recognized" means on the finite list in section 4, which
   is ours. Under D4 only a resolver can find a plan clear, and the offline
   resolver always answers no. On this dataset every plan is vague, so offline matches the
   oracle. On a case with a genuinely clear plan, offline mode returns
   NEEDS_FOLLOW_UP where the correct answer may be READY. This is the safe
   direction of failure.
6. **Offline mode trusts keyword-positive consents (limitation).** Default
   mode sends a keyword-POSITIVE consent to the model for confirmation (D2);
   offline mode has no model, so a signed-looking text that is not in fact a
   valid patient signature, and that no tier 1 or tier 2 negative catches,
   clears offline.

## 6. Blast radius: what the LLM can and cannot change

The LLM can:

- Turn an UNKNOWN title into H&P, CONSENT, ANTICOAG_PLAN, or OTHER. This can
  add or remove a REQUIRED_DOCUMENTATION or ANTICOAGULATION_MANAGEMENT issue
  and can change which document index is cited.
- Turn an UNPLACED text into POSITIVE or NEGATIVE. This can add or remove a
  "consent not clearly signed" issue or an anticoagulation issue.
- Decline to confirm a keyword-POSITIVE consent. This can add a "consent not
  clearly signed" issue; it can never remove one, because a keyword-NEGATIVE
  consent is final (D2).
- Declare a plan clear for a drug. Under D4 this is the only way any
  anticoagulation issue is ever removed. On this dataset one of the seven
  plan texts is UNPLACED (case_00000), so one anticoagulation outcome rests
  on the LLM's answer in default mode.

The LLM cannot:

- Change any date arithmetic, lab window, vital threshold, or missing-data
  gate.
- Produce, edit, or reorder issues, descriptions, sources, or details. It
  returns an enum from a strict schema; the rules layer writes the issue.
- Override a keyword NEGATIVE or a title the rules placed. Its only power
  over a keyword POSITIVE consent is to add the issue.
- Change the decision except through the two issue types above. It can never
  produce or remove an ACUTE_SAFETY_EXCLUSION, so it can never flip a case
  into or out of NOT_CLEARED.

Outage behavior: on any API error the resolver retries twice with a short
backoff, then returns the offline answer for that item (title OTHER, consent
not signed, plan not clear), writes one line to stderr naming the item and the
error, and does not cache that answer. A failed consent confirmation returns
the keyword answer. The engine appends one sentence to the explanation ("LLM
fallback unavailable for N item(s); conservative offline answers were used.")
and never raises. An outage therefore degrades to offline behavior. Offline
is not strictly more conservative: when the newest document has an
unrecognized title, classifying it as OTHER means an older recognized
document is judged instead, which can remove an issue as well as add one. A
missing API key is recorded once and not retried. If
the model rejects `temperature`, the resolver retries that call once without
it and stops sending it for the rest of the process; the stability report
states whether it was accepted.

`force_llm` exists for measurement only. It sends every title, the judged
consent text, and every plan candidate to the model and discards the rules'
answer, so the stability work can compare the model against the rules. It is
ignored in offline mode and is never a production setting.

Consequences: the maximum per-case damage from a wrong LLM answer is one
category point plus the grounding half-point. With the in-memory cache, the
harness determinism run (`make determinism`, record 0, 10 calls in one
process) exercises the LLM at most once per distinct title or text; the
stability script with `no-cache` is the real measure of model variance.
Case_00000, the determinism record, does reach the LLM in default mode: its
doc[1] title "Preop Pre-anesthesia Evaluation [PDF]" is UNKNOWN, its
keyword-POSITIVE consent is sent for confirmation, and its plan text is
UNPLACED. The LLM's answer on the plan text decides whether the
anticoagulation issue stays. The text gives no hold or resume guidance, so a
correct answer is "not clear" and both modes then reach the same output; a
wrong "clear" answer would drop the issue and cost the category point.

## 7. Milestone plan (as carried out)

1. **Baseline capture and scaffold.** The naive run was committed to
   `results/naive/` via `make baseline evals determinism OUTPUT=...
   REPORT=... DETERMINISM_REPORT=...`, before any code change. The package
   layout was scaffolded, `triage_submission` kept its signature
   (`submission`, keyword-only `model`), the offline scorer imported from
   `run_evals`, and the starter's tests were replaced.
2. **Rules engine.** Normalization, the three-outcome classifier, rules 1 to
   4, and the missing-data gates were built. A golden test ran all 50 cases
   in offline mode, matching on decision, category set, and per-issue source
   and description (details were checked by the grounding metric, not
   character for character). Synthetic tests covered an anticoagulation plan
   with hold and resume language (flagged offline, cleared only by a
   resolver), a lab dated after the procedure, exact boundaries (30 days, 14
   days, 180 systolic, 110 diastolic, 100.4 F), tied vital timestamps, an
   inactive anticoagulant, a null document date, and the three substring-trap
   strings from D4. The offline scorer showed 100% decision and category match
   and 100% grounding on all 50 cases.
   **First review.** A separate reviewer session then checked the rules
   engine; its accepted findings amended D3, D4, and section 4.
3. **LLM layer.** The classifier for UNKNOWN titles and UNPLACED text was
   built with a strict schema, temperature 0 where the model accepts it, an
   in-memory module-level cache keyed on (model, question, text) where
   question is `title`, `consent`, or `plan:<drug>`, the `PREOP_OFFLINE`,
   `PREOP_NO_CACHE`, and `PREOP_FORCE_LLM` environment variables with the
   `settings` override (D1), and a stability script reporting self-agreement
   across runs and agreement with the rules on items the rules placed.
4. **Harness runs in both modes**, determinism in both modes, stability
   numbers, and a README with build and run instructions.
   **Second review.** A second reviewer session then did the pre-submission
   review; its accepted findings amended D2, D3, D4, and sections 4 to 6
   (model-confirmed consent positives, brand-name anticoagulants, unparseable
   item dates, combined consent titles), and every committed run was
   regenerated afterward.
5. **Generalization probes.** The reviewers' probe strings, written without
   sight of the keyword lists, were run against the keyword rules as they
   stood before each fix, the rules at the final commit, and both models
   (`results/probes-*/`), as the nearest thing to held-out data in the repo.

## 8. Rejected findings

Reasons for rejecting verifier findings are recorded here.

1. **Add "follow up" and "recommendations" to the negative plan-clarity set
   (proposed during design review to keep case_00000 out of the LLM).**
   Rejected: "follow up" and "recommendations" are not negative signals about
   a plan on their own; adding them fits one string. Case_00000's plan text
   goes UNPLACED; offline flags it, and default mode flags it when the LLM
   answers correctly.
2. **`awaiting` and `pending` as consent negatives.** The reviewer noted that
   these tokens cause false follow-ups and did not propose a change. Kept:
   a false follow-up on consent is the safe failure and the policy's own
   default for ambiguity.
3. **Judge H&P completeness from its text.** Rejected. "Completed within 30
   days" is read as the completion date. Judging clinical completeness from
   free text is outside the policy; presence and date are what Rule 1
   specifies.
4. **Treat an anticoagulant named only in document text as current.**
   Rejected. The medication list is the policy's source for "currently
   taking"; a drug named only in narrative text is not a current medication
   record.
5. **Order a date-only value after same-day timestamps.** Kept at midnight.
   Any placement of a date-only value within its day is arbitrary; midnight
   is documented as the assumption.

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
