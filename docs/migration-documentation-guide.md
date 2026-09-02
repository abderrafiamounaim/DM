# Data Migration Documentation Guide
### Salesforce → Salesforce Migration — What Each Deliverable Is, What Goes In It, and What the Client Gets Out of It

This guide explains the nine core documents a data migration workstream should produce, in the order they'd typically be drafted across the project lifecycle. It's written for a Salesforce-to-Salesforce context (a legacy/source org migrating into a new target org, objects like Accounts, Contacts, Opportunities, Cases), but the structure applies to any CRM-to-CRM migration.

Think of these nine documents as telling one continuous story:
**Strategy** (how we'll do it) → **Mapping** (what maps to what) → **Data Quality Assessment** (is the source data usable) → **Runbook** (the step-by-step execution plan) → **Mock Migration Reports** (proof it works, before it's real) → **Reconciliation Report** (proof it worked, after it's real) → **Cutover Plan** (how we go live) → **Sign-off** (formal client acceptance) → **Final Migration Report** (the closing record).

A client-facing migration workstream that produces all nine, in this sequence, with consistent numbers across them, reads as a controlled, de-risked project — not "we ran some scripts and hoped."

---

## 1. Data Migration Strategy

**What it is:** The foundational, high-level document that answers "how are we going to do this migration, and why this way?" It's written early (kickoff phase) before any real mapping or extraction work starts. Everything downstream (mapping, runbook, cutover) should trace back to a decision made here.

**What it should contain:**
- **Objectives & scope** — which source org objects are in scope (Accounts, Contacts, Opportunities, Cases, Activities, custom objects) and explicitly what's out of scope.
- **Migration approach** — Big Bang vs. Phased/Wave migration, and why. Where objects have interdependencies (Accounts, Contacts, Opportunities), this usually means justifying a phased approach (referentials first, transactional data after).
- **Source and target system overview** — brief description of the source org's data model (standard + custom objects in scope) and the target org's data model.
- **High-level object load sequence** — e.g., Accounts → Contacts → Opportunities → Cases → Activities/Notes → Attachments. Explain the dependency logic (child records need parent IDs to exist first).
- **Tooling** — extraction method (API vs. DB export), transformation tooling (Python/SQL), load tooling (Data Loader, Bulk API, custom Python loader like this project's `load_records.py`).
- **Data volumes** — record counts per object, expected growth/delta between extraction and cutover.
- **Roles & responsibilities (RACI)** — who owns mapping validation, who owns data cleansing, who approves go/no-go.
- **Environments** — sandbox/staging vs. production, and how many migration rehearsals (mock loads) are planned.
- **Risk register** — known risks (duplicates, missing required fields, automation triggering unwanted emails, dedup timing) and mitigation strategy.
- **Timeline** — key milestones: mapping freeze, data quality remediation deadline, mock load 1/2/3, UAT, cutover, hypercare.
- **Rollback strategy** — what happens if migration fails (delete-and-retry vs. record-level rollback vs. full sandbox restore).

**What the client should get out of it:** Confidence that there's a deliberate, risk-aware plan — not an ad hoc data dump. This is the document a sponsor reads to understand *why* the team is doing things in a certain order, what could go wrong, and how it's being controlled. It's also the reference document you point back to whenever scope creep or "can we just migrate everything in one shot" questions come up mid-project.

---

## 2. Source-Target Mapping

**What it is:** The field-by-field dictionary connecting every source org field to its target org destination, with the transformation logic applied in between. This is the single most-referenced document by both the technical team (building transformations) and the business (validating that "what we see in the source org is what we'll see in the target org").

**What it should contain, per object (Account→Account, Contact→Contact, Opportunity→Opportunity, Case→Case, etc.):**
- **Source field** (API name + label), **source object**, data type, sample value.
- **Target field** (API name + label), **target object**, data type, whether it's required/has validation rules.
- **Transformation rule** — direct copy, concatenation, lookup/cross-reference (e.g., source owner ID → target User ID via an owner mapping table), value translation (picklist mapping), default value if source is null, conditional logic.
- **Cardinality / relationship handling** — how one-to-many or many-to-many relationships resolve into the target org's model.
- **External ID / legacy ID strategy** — the field used to track the source record ID on the target (e.g., `Legacy_Record_ID__c`) — critical for reconciliation and re-runs.
- **Owner/status column** — mapping confirmed by business (yes/no), open questions, last updated date, who validated it.
- **Exceptions & edge cases** — fields with no target equivalent (documented as "not migrated, and why"), fields requiring manual/business decision.

**What the client should get out of it:** A living, versioned contract between source and target — the artifact the business signs off on before any data moves. It's what lets a business user say "I don't see field X mapped" and get a direct answer, and what lets the technical team build transformation scripts without guessing. It also becomes the audit trail: when reconciliation later shows a discrepancy, this document is the first place to check "was this even supposed to map?"

---

## 3. Data Quality Assessment Report

**What it is:** An analysis of the *source* data's fitness for migration, run early (ideally before or alongside mapping) so that cleansing work has time to happen before load. This is where you answer "is the data we're about to migrate actually any good?"

**What it should contain:**
- **Scope** — objects/fields assessed, record counts analyzed.
- **Completeness** — % of records missing values in required/important fields.
- **Duplicates** — duplicate detection results, duplication rate, proposed dedup strategy and *when* it happens (critical: deduping too early can orphan child records that reference a duplicate ID — this needs to be flagged explicitly).
- **Validity** — values that won't pass target org validation rules (bad email formats, picklist values with no target equivalent, dates out of range, orphaned records with no parent).
- **Consistency** — cross-object inconsistencies (e.g., an Opportunity referencing an Account that doesn't exist in the Accounts export).
- **Uniqueness of identifiers** — confirmation that the source record ID (or whatever becomes the external ID) is unique and stable.
- **Findings summary table** — object, issue type, record count affected, % of total, severity (blocker / needs remediation / cosmetic), owner, remediation plan and deadline.
- **Recommendation** — go/no-go input for mapping freeze and for the first mock load; explicit list of what must be fixed before Mock Load 1 vs. what can be fixed later.

**What the client should get out of it:** An early warning system — this is what prevents the client from discovering, during cutover weekend, that a large share of their data has a structural problem. Delivering this report early, with clear severity and ownership, is what separates a controlled migration from a reactive one. It also gives the business a concrete, prioritized cleansing task list instead of a vague "the data is messy" statement.

---

## 4. Migration Runbook

**What it is:** The operational, step-by-step execution manual — the document someone unfamiliar with the project could follow to actually run the migration. Written once the strategy and mapping are stable; refined after each mock load.

**What it should contain:**
- **Pre-requisites checklist** — sandbox/prod readiness, automation bypass switches identified (flows, triggers, validation rules, email sends that must be disabled during load), user/license setup, API limits checked.
- **Execution sequence** — ordered list of load jobs (this project's `job_accounts.yaml`, `job_contacts.yaml`, `job_opportunities.yaml` etc. are exactly this kind of artifact) with dependencies explicitly called out (e.g., "Accounts must complete and be validated before Contacts starts").
- **Per-object steps** — extraction command/query, transformation script reference, load tool/job name, expected record count, expected duration, validation query to run immediately after.
- **Environment-specific parameters** — sandbox vs. prod endpoints, credentials handling (never plaintext — pointer to secrets management), batch sizes, API vs. Bulk API thresholds.
- **Error handling procedure** — where failed records land (`log/runs/<run_id>/fails.csv`), how to triage, retry logic, escalation path.
- **Rollback procedure per object** — how to undo a bad load (delete by external ID batch, restore from backup, etc.).
- **Roles during execution** — who runs each job, who validates, who has go/no-go authority mid-run.
- **Post-load validation steps** — record count checks, spot checks, automation re-enablement checklist.

**What the client should get out of it:** Operational predictability and business continuity insurance. If the primary migration engineer is unavailable, the runbook is what lets someone else execute without reinventing the sequence. It's also what a client's IT/security team will ask for before granting production load access — proof the team isn't improvising in a production Salesforce org.

---

## 5. Mock Migration Report(s)

**What it is:** The results of trial/rehearsal loads into a sandbox or staging Salesforce org, done *before* the real cutover, using production-like (ideally full-volume) data. There are usually multiple mock loads (Mock 1, Mock 2, sometimes Mock 3) as issues get fixed and re-tested — each gets its own short report, or they're tracked as versions of the same one.

**What it should contain, per mock load:**
- **Objective of this run** — what changed since the last mock.
- **Scope** — objects and record volumes included.
- **Timing metrics** — extraction time, transformation time, load time per object, total end-to-end duration (this feeds the cutover plan's time budget).
- **Results** — records attempted vs. loaded successfully vs. failed, by object.
- **Failure analysis** — top error categories (validation rule failures, duplicate rule blocks, required field missing, automation-triggered errors), with counts.
- **Automation/performance observations** — did any target org automation cause slowdowns or unwanted side effects — this directly informs the bypass list in the runbook.
- **Data spot-check results** — sample-based manual verification that migrated records look correct in the UI, not just "no error thrown."
- **Issues log & remediation owners** — each issue tied to mapping fix, data cleansing fix, or Salesforce config fix, with an owner and target date for the next mock.
- **Go/no-go recommendation** — is the process ready to proceed to the next mock, to UAT, or to cutover.

**What the client should get out of it:** Proof, not promises. Mock loads are where the client's business users typically get their first real look at their data living in the new org, and where they build (or lose) trust in the migration. A clean mock migration report trend across iterations is the strongest evidence you can hand a sponsor that cutover will succeed.

---

## 6. Data Reconciliation Report

**What it is:** The formal, quantitative proof that what was migrated matches what was intended — comparing source counts/values against target counts/values after a load (mock or, most importantly, the final production load). This is distinct from the Mock Migration Report: mocks are about process rehearsal, reconciliation is about data accuracy verification.

**What it should contain:**
- **Scope** — objects and the specific load run being reconciled (mock N or production cutover).
- **Volumetric reconciliation** — source record count vs. target record count, by object, with explained deltas.
- **Field-level reconciliation** — for a sample or full set, comparison of key field values between source and target to catch transformation logic errors, not just missing records.
- **Relationship integrity checks** — orphan check (every Contact has a valid Account, every Opportunity has a valid Account), no broken lookups.
- **Reconciliation methodology** — how the comparison was done (e.g., SQL join on the external/legacy ID field), sample size and confidence level if not 100% coverage.
- **Discrepancy log** — each unmatched or mismatched record, category, root cause, resolution status.
- **Sign-off-ready summary** — a single table: object, source count, target count, match rate %, status (reconciled / pending / accepted variance).

**What the client should get out of it:** Auditable, defensible evidence that the migration is complete and correct. This is also the document that closes the loop opened by the Data Quality Assessment: DQA said "here's what's wrong with the source," reconciliation says "here's proof it was fixed or accounted for."

---

## 7. Cutover Plan

**What it is:** The minute-by-minute (or hour-by-hour) operational plan for the actual go-live event — the final production migration where the business stops using the old org and starts using the new one. Built from everything learned in the mock loads.

**What it should contain:**
- **Cutover window** — exact start/end date-time, timezone, and why that window was chosen.
- **Freeze period** — when the source org becomes read-only/locked to prevent new data from being created mid-migration and missed.
- **Detailed timeline** — sequenced, timestamped steps: final delta extraction, transformation run, load per object, validation checkpoints, reconciliation run, automation re-enablement, go/no-go checkpoint, user access cutover.
- **Go/no-go decision points** — explicit checkpoints where the team pauses to confirm results before proceeding to the next irreversible step, and who has authority to make that call.
- **Roles & contact list** — who's doing what during the window, escalation contacts, war-room/bridge line info.
- **Communication plan** — what gets sent to end users before, during, and after, and to which distribution lists.
- **Rollback trigger criteria** — the specific, pre-agreed conditions under which the team aborts and rolls back.
- **Hypercare plan** — post-cutover support window, who's on call, how issues get logged and triaged for the first days/weeks after go-live.

**What the client should get out of it:** Certainty about a high-stakes, low-reversibility event. This is the document that lets business stakeholders plan around the migration and lets leadership know precisely what "success" and "abort" look like *before* the event.

---

## 8. Migration Sign-off

**What it is:** A short, formal document where the client (business owner and/or IT/security stakeholder) explicitly accepts the migration as complete and correct, closing the project (or a wave/phase of it) from a data standpoint. This is a governance artifact, not a technical one.

**What it should contain:**
- **Scope confirmation** — objects and wave/phase being signed off.
- **Reference to supporting evidence** — links/attachments to the Reconciliation Report, outstanding issues log, and any accepted variances.
- **Acceptance criteria met** — a checklist showing each criterion defined back in the Data Migration Strategy and its actual result.
- **Known open items** — anything not yet resolved, with an agreed remediation owner/date, explicitly carried forward rather than hidden.
- **Signatories** — named business owner and technical/IT owner, with date.
- **Conditions of acceptance** (if any) — e.g., "accepted subject to hypercare support through [date]."

**What the client should get out of it:** A clean, unambiguous project milestone and a paper trail — protecting both sides.

---

## 9. Final Migration Report

**What it is:** The retrospective, closing document for the overall migration project (or the closing document once all waves are complete). Written after hypercare, once things have stabilized.

**What it should contain:**
- **Executive summary** — objectives, what was migrated, overall outcome, in plain business language.
- **Scope delivered** — final object list, total records migrated, comparison to what was originally planned.
- **Timeline recap** — planned vs. actual, key milestones, reasons for major deviations.
- **Data quality outcomes** — before/after comparison referencing the DQA report.
- **Final reconciliation summary** — consolidated match rates by object.
- **Issues encountered & resolutions** — a summarized history of major incidents and how they were resolved.
- **Lessons learned** — what worked, what should be done differently next time.
- **Post-migration recommendations** — data governance suggestions, ongoing dedup/validation rule recommendations, deferred cleanup work.
- **Sign-off references** — links to all wave sign-offs, confirming formal closure.
- **Appendices** — links/pointers to the full mapping document, all reconciliation reports, runbook.

**What the client should get out of it:** Closure, a defensible record for audit/governance purposes, and reusable organizational knowledge.

---

## How These Fit Together (Quick Reference)

| # | Document | When it's written | Primary audience | Answers |
|---|----------|-------------------|-------------------|---------|
| 1 | Data Migration Strategy | Kickoff | Sponsor / Steering Committee | How & why will we migrate? |
| 2 | Source-Target Mapping | Early, iterated throughout | Business SMEs + technical team | What field goes where, and how? |
| 3 | Data Quality Assessment | Early, before mapping freeze | Business data owners | Is the source data usable? |
| 4 | Migration Runbook | Before first mock, refined after each | Technical/execution team | How do we actually run it? |
| 5 | Mock Migration Report(s) | After each rehearsal load | Project team + business validators | Did the rehearsal work? |
| 6 | Data Reconciliation Report | After each load (esp. production) | Business owners, compliance | Does target match source, provably? |
| 7 | Cutover Plan | Just before go-live | Everyone involved in go-live | What exactly happens, hour by hour? |
| 8 | Migration Sign-off | Right after cutover/reconciliation | Business owner, IT owner | Is this formally accepted? |
| 9 | Final Migration Report | After hypercare | Sponsor, future teams | What happened, overall, and what did we learn? |

**Consistency rule of thumb:** the record counts in your Mock Migration Reports, Reconciliation Report, Sign-off, and Final Report should all trace back to the same source numbers established in the Data Quality Assessment. If a reviewer cross-checks two of these documents and the numbers don't line up, it undermines trust in the whole set — so treat the record-count table as a single source of truth updated across documents, not independently re-derived each time.
