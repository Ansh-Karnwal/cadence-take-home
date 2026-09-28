# Generalization probes

Model `gpt-4.1-mini`, 5 runs per string, cache off.

| Answer source | Correct |
|---|---|
| Keyword rules before each string's fix | 0 of 21 |
| Keyword rules at HEAD | 21 of 21 |
| Model alone (majority label) | 19 of 19 |

Pre-fix errors that wrongly clear a patient: 16. Pre-fix errors that over-flag: 5.

| Review | Kind | String | Correct | Pre-fix rules (commit) | HEAD rules | Model majority (split) |
|---|---|---|---|---|---|---|
| first | plan | Home meds reviewed incl. apixaban. Hold metformin morning of surgery; resume diet after. [drug: apixaban] | false | true (POSITIVE) WRONG (b3940c6) | false (UNPLACED, deferred) ok | false (false x5) ok |
| first | plan | Cardiology to advise whether to hold or resume apixaban. [drug: apixaban] | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | plan | Unclear whether to hold apixaban; will resume later. [drug: apixaban] | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | plan | Patient on apixaban. Hold and resume per cardiology. [drug: apixaban] | false | true (POSITIVE) WRONG (b3940c6) | false (UNPLACED, deferred) ok | false (false x5) ok |
| first | consent | Consent not signed. | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | consent | Patient has not signed consent. | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | consent | Consent to be signed on day of surgery. | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | consent | Consent form signed by witness; patient signature missing. | false | true (POSITIVE) WRONG (b3940c6) | false (NEGATIVE) ok | false (false x5) ok |
| first | title | Anticoagulation Plan | ANTICOAG_PLAN | OTHER (UNKNOWN, deferred) WRONG (b3940c6) | ANTICOAG_PLAN (REQUIRED:ANTICOAG_PLAN) ok | ANTICOAG_PLAN (ANTICOAG_PLAN x5) ok |
| first | title | H & P | H_AND_P | OTHER (UNKNOWN, deferred) WRONG (b3940c6) | H_AND_P (REQUIRED:H_AND_P) ok | H_AND_P (H_AND_P x5) ok |
| first | title | Interval H&P | H_AND_P | OTHER (UNKNOWN, deferred) WRONG (b3940c6) | H_AND_P (REQUIRED:H_AND_P) ok | H_AND_P (H_AND_P x5) ok |
| first | title | H&P Addendum | H_AND_P | OTHER (UNKNOWN, deferred) WRONG (b3940c6) | H_AND_P (REQUIRED:H_AND_P) ok | H_AND_P (H_AND_P x5) ok |
| first | title | Consent Withdrawn | OTHER | CONSENT (REQUIRED:CONSENT) WRONG (b3940c6) | OTHER (KNOWN_OTHER) ok | OTHER (OTHER x5) ok |
| second | consent | Consent form signed by surgeon; patient signature outstanding. | false | true (POSITIVE) WRONG (432bea9) | false (NEGATIVE) ok | false (false x5) ok |
| second | consent | Consent signed by patient. Patient later withdrew consent. | false | true (POSITIVE) WRONG (432bea9) | false (NEGATIVE) ok | false (false x3, true x2) ok |
| second | consent | Signed consent rescinded by patient on 2026-02-22. | false | true (POSITIVE) WRONG (432bea9) | false (NEGATIVE) ok | false (false x5) ok |
| second | consent | Patient consent form. Signed: ______________ Date: ________ | false | true (POSITIVE) WRONG (432bea9) | false (NEGATIVE) ok | false (false x5) ok |
| second | consent | Patient signed consent for left knee arthroscopy; new consent required for planned procedure. | false | true (POSITIVE) WRONG (432bea9) | false (NEGATIVE) ok | false (false x5) ok |
| second | medication | Eliquis 5 mg BID | recognized | not recognized (none) WRONG (432bea9) | recognized (apixaban) ok | n/a (no model question) |
| second | medication | Xarelto 20 mg daily | recognized | not recognized (none) WRONG (432bea9) | recognized (rivaroxaban) ok | n/a (no model question) |
| second | title | Surgical and Anesthesia Consent | CONSENT | OTHER (KNOWN_OTHER) WRONG (432bea9) | CONSENT (REQUIRED:CONSENT) ok | CONSENT (CONSENT x5) ok |

A rules answer is graded by the decision offline mode would make: an UNPLACED consent
is not signed, an UNPLACED plan is not clear, an UNKNOWN title is OTHER. Deferred
answers are marked. Medication names have no model question and are excluded from
the model's total. The pre-fix rules come from a git worktree of the named commit.
