# Phased Implementation Roadmap

Version 0.1 · Status: **Draft for approval**

Phases are sized by **gates, not dates**. Only one phase is active at a time. A phase ends with validation against its exit criteria and an explicit written approval before the next begins. Requirement IDs refer to [`REQUIREMENTS.md`](../REQUIREMENTS.md).

## Phase 0 — Foundations (this phase)

**Deliverables:** README, REQUIREMENTS, ARCHITECTURE, SECURITY, CLINICAL_SAFETY, decision log, ROADMAP, EVALUATION, initial folder structure, database design reference.

**Exit criteria**
- [ ] Project owner approves documents and answers or defers open questions Q-01…Q-10.
- [ ] Clinical safety lead named (or explicit decision to proceed provisionally with no clinical claims).
- [ ] Decisions D-001…D-016 moved to Accepted or revised.

## Phase 1 — Data model, synthetic data, deterministic core

**Scope:** Executable migrations from the design (identity/clinical separation, data-element catalogue, observations with five-state constraints); synthetic case generator and clinician-authored seed cases; calculation engine; completeness and conflict rules; schema package; CI skeleton with secret and identifier scanning; traceability matrix.

**Requirements:** FR-INT-01/02/04/05, FR-STATE-01/02, FR-COMP-01…03, FR-CALC-01…04, FR-CONF-01/02, NFR-SEC-06/07, NFR-MNT-01/02/03, NFR-INT-01, NFR-OBS-01, NFR-PERF-01.

**Exit criteria**
- [ ] Calculators match clinician-verified reference cases (100% agreement on the reference set).
- [ ] Every missing-vs-normal trap case yields `not_assessed` / gap, never "normal".
- [ ] DB constraint tests prove invalid state/value combinations are rejected.
- [ ] Rule set reviewed and signed by the clinical lead.
- [ ] No real data anywhere; scanners green.

## Phase 2 — Security and audit baseline

**Scope:** OIDC with MFA; RBAC; RLS policies and tests; envelope encryption; KMS/secret management; append-only hash-chained audit with verifier; backup and restore drill; threat model v1.

**Requirements:** FR-AUD-01/03, FR-ADM-01, NFR-SEC-01…05, NFR-REL-01.

**Exit criteria**
- [ ] RLS tests: cross-patient and cross-facility access impossible for each role.
- [ ] Audit chain verification passes and detects deliberate tampering in tests.
- [ ] Restore drill succeeds within agreed RPO/RTO.
- [ ] Security review of auth and data layer completed.

## Phase 3 — LLM gateway, evaluation harness, model bake-off

**Scope:** LLM gateway with local runtime, constrained JSON decoding, egress deny; model registry with digests; evaluation harness and synthetic benchmark; model shortlist evaluation; mock model for tests.

**Requirements:** FR-AUD-02, FR-ADM-02, NFR-SEC-04/08, NFR-SAF-05, NFR-PERF-02/03.

**Exit criteria**
- [ ] Egress tests prove no outbound connectivity from AI zone.
- [ ] Primary and fallback model chosen with recorded licence review, digests and benchmark results (D-012 updated).
- [ ] Structured-output validity at or above the threshold set by the clinical lead.

## Phase 4 — Knowledge base and citation verification

**Scope:** Source acquisition and licence register; chunking with locators; embeddings; retrieval with applicability tags; citation resolver and support checker; KB change control.

**Requirements:** FR-KB-01…04, FR-INTV-01, NFR-GOV-01.

**Exit criteria**
- [ ] 100% of generated citations resolve to KB chunks in evaluation.
- [ ] "No supporting source" behaviour verified on out-of-corpus queries.
- [ ] Licence register complete and reviewed.

## Phase 5 — Agent workflow

**Scope:** Orchestrator state machine; Intake Normaliser (free-text proposals); Assessment Summariser; Diagnosis Candidate Drafter; Intervention Suggester; Monitoring & Evaluation Drafter; Verifier; uncertainty flags; kill switch; injection and leakage defences.

**Requirements:** FR-INT-03, FR-STATE-03, FR-COMP-04, FR-CONF-03/04, FR-ADIME-01…07, FR-INTV-02…04, FR-REV-01, FR-ADM-03, NFR-SAF-01/02/04, NFR-REL-02.

**Exit criteria**
- [ ] Safety suite: **zero** critical failures (fabricated values/references, missing-as-normal, directive prescribing language, successful injection).
- [ ] Verifier catches seeded errors at or above the threshold set by the clinical lead.
- [ ] Fail-safe behaviours verified (LLM down, verifier down, KB down).

## Phase 6 — Clinician review UI and approval workflow

**Scope:** Mobile-first review UI; evidence panels; item-level edit/diff; approve/reject/regenerate; trainee co-sign; amendments; exports with watermarks; usability testing with clinicians on synthetic cases.

**Requirements:** FR-REV-02…08, FR-INT-06 (optional), NFR-UX-01…05, NFR-REL-03, NFR-OBS-02.

**Exit criteria**
- [ ] Approval cannot occur without an authorised human action (tested at API and DB levels).
- [ ] Usability study meets agreed task-success and time targets; automation-bias canary detection measured.
- [ ] API penetration test findings resolved.

## Phase 7 — Clinical evaluation and supervised shadow-mode study

**Scope:** Evaluation per [`EVALUATION.md`](EVALUATION.md): expert-panel review on synthetic and (if approved) de-identified retrospective cases; shadow-mode use where outputs are **not** placed in the record; ethics, DPIA and regulatory classification; incident-response exercise; go/no-go.

**Requirements:** NFR-GOV-02/03/04, NFR-SAF-03, plus all prior gates.

**Exit criteria**
- [ ] Ethics approval and DPIA completed; counsel sign-off on legal framework.
- [ ] Clinical evaluation thresholds met (set in advance by clinical lead).
- [ ] Regulatory classification documented.
- [ ] Written go/no-go decision from project owner, clinical safety lead and privacy lead.

## Beyond Phase 7 (not planned in detail)

Limited supervised deployment; paediatric/SAM pathways (if not earlier); additional populations; EHR/FHIR integration; multilingual support; post-market monitoring and periodic revalidation.

## Dependencies and risks

| Risk | Mitigation |
|---|---|
| No clinical lead available | Hold clinical claims; limit to engineering scaffolding; escalate at Phase 1 gate |
| Hardware for inference unavailable | Small-model dev profile; rented GPU time on synthetic data only |
| Licence blocks a preferred model or source | Maintain fallback candidates; licence register from Phase 3/4 |
| Scope creep into diagnosis/prescribing | Out-of-scope list in CLINICAL_SAFETY.md; schema forbids such fields |
| Evidence base becomes outdated | KB versioning and review cadence |
| Single-developer bus factor | Documentation, decision log, CI-driven workflow |
