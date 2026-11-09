# Phased Implementation Roadmap

Version 0.3 · Status: **Phase 0 approved 2026-11-08**

Phases are sized by **gates, not dates**. Only one phase is active at a time. A phase ends with validation against its exit criteria and an explicit written approval before the next begins. Requirement IDs refer to [`REQUIREMENTS.md`](../REQUIREMENTS.md).

Planning assumptions (v0.2):

- **Population:** adult nutrition assessment first; paediatric and SAM pathways are planned and the data model is ready for them (D-018).
- **Inference:** development starts on hosted LLM APIs with **synthetic data only**; the design is model-independent; local or approved-framework inference is required before any real patient data (D-017).
- **Governance:** the project owner leads product and clinical requirements; an independent clinical safety reviewer must be appointed before any clinical deployment (D-019).

## Phase 0 — Foundations (this phase)

**Deliverables:** README, REQUIREMENTS, ARCHITECTURE, SECURITY, CLINICAL_SAFETY, decision log, ROADMAP, EVALUATION, initial folder structure, database design reference.

**Exit criteria**
- [x] Project owner reviews the v0.2 documents and approves Phase 0 (2026-11-08).
- [x] Open questions Q-02, Q-04, Q-05, Q-07 to Q-12 answered or consciously deferred (Q-01, Q-03 and Q-06 are answered).
- [x] Decisions D-001 to D-019 moved to Accepted or revised.

## Phase 1 — Data model, synthetic data, deterministic core (adult pathway)

**Scope:** Executable migrations from the design (identity/clinical separation, data-element catalogue, observations with five-state constraints); **pathway registry with `adult_general` active and paediatric/SAM planned**; population-ready model (age in months, disabled population-specific elements, versioned reference standards); governance tables (instance data class, role assignments, rule review status); synthetic case generator and clinician-authored seed cases; calculation engine for adults; completeness and conflict rules; schema package; CI skeleton with secret and identifier scanning; traceability matrix.

**Requirements:** FR-INT-01/02/04/05, FR-STATE-01/02, FR-COMP-01…03, FR-CALC-01…04, FR-CONF-01/02, FR-POP-01…04, FR-GOV-01/02, NFR-SEC-06/07, NFR-MNT-01/02/03, NFR-INT-01, NFR-OBS-01, NFR-PERF-01.

**Exit criteria**
- [ ] Calculators match clinician-verified reference cases (100% agreement on the reference set).
- [x] Every missing-vs-normal trap case yields `not_assessed` / gap, never "normal".
- [x] DB constraint tests prove invalid state/value combinations are rejected.
- [x] Planned pathways (paediatric, SAM) and out-of-band ages are refused by tests.
- [ ] Rule set approved by the product and clinical requirements lead (`author_approved`); independent review requested and its status recorded.
- [x] No real data anywhere; scanners green.

**Status (2026-11-08):** engineering complete and awaiting the owner's approval. Two exit items are clinical and remain open by design (D-022): verification of the calculator reference cases, and `author_approved` status for the rule set with independent review requested. Review material: `docs/clinical/rule-review-sheet.md`.

## Phase 2 — Security and audit baseline

**Scope:** OIDC with MFA; RBAC; RLS policies and tests; envelope encryption; KMS/secret management; append-only hash-chained audit with verifier; backup and restore drill; start-up checks for instance data class and governance status; threat model v1.

**Requirements:** FR-AUD-01/03, FR-ADM-01, FR-GOV-03, NFR-SEC-01…05, NFR-REL-01.

**Exit criteria**
- [x] RLS tests: cross-patient and cross-facility access impossible for each role.
- [x] Audit chain verification passes and detects deliberate tampering in tests.
- [ ] Restore drill succeeds within agreed RPO/RTO. (Drill built and passing against proposed targets; the owner has not agreed the targets.)
- [x] Clinical mode refuses to start while the independent reviewer role is vacant or rules are unreviewed (tested; a service must still call the check).
- [ ] Security review of auth and data layer completed. (Not done: needs a reviewer other than the author; see docs/THREAT_MODEL.md.)

**Status (2026-11-09):** approved by the owner on 2026-11-09 to proceed to Phase 3; engineering built and tested; two exit items stay open by design and are carried forward: owner-agreed recovery targets and an independent security review. Several parts of the scope are deliberately not built because they need decisions or infrastructure that do not exist yet (identity provider, key service, TLS); the list is in SECURITY.md section 16.

## Phase 3 — Provider-neutral LLM gateway, evaluation harness, backend bake-off

**Scope:** Provider-neutral gateway interface; deterministic mock backend; hosted adapter(s) for **synthetic data only** with data-class and per-request gates; structured-output validation by project code; model and provider version registry; evaluation harness and synthetic benchmark; comparison of the mock and selected hosted model(s). The local runtime (mode L) is designed but deferred until a hardware or server decision (Q-05).

**Requirements:** FR-AUD-02, FR-ADM-02, NFR-SEC-04/08/09/10, NFR-SAF-05, NFR-PERF-02/03, NFR-ARC-01…06.

**Exit criteria**
- [x] Hosted-gate tests: hosted calls are refused for non-synthetic cases and in any instance recorded as real-data; a real-data build profile contains no hosted adapter.
- [x] Switching backend (mock to hosted) works by configuration only, with no change to rules, calculators, schemas or review workflow.
- [ ] Primary and fallback backends chosen with recorded provider terms or licence review, pinned versions and benchmark results (D-012 and D-017 updated).
- [ ] Structured-output validity at or above the threshold set by the product and clinical requirements lead.
- [x] Spend caps and key-handling checks in place (local caps; provider-side limits are an owner action at the provider).

**Status (2026-11-09):** approved by the owner on 2026-11-09. Engineering is built and tested against scripted and fake-transport backends (D-025). Provider chosen: GroqCloud Free, `openai/gpt-oss-120b`, backend id `groq` (D-026). **Not yet done:** the real benchmark. It was attempted from the build workspace with `python scripts/run_benchmark.py --backend groq` and correctly refused with `key_unavailable` (no key, no route to the provider there), so **no benchmark evidence exists, and exit criteria 3 and 4 remain open.** To close them: create the key file in Termux, enable Zero Data Retention in the Groq console, run the benchmark on your phone, and share `docs/evaluation/benchmark-hosted-groq.md`. The synthetic corpus has 26 in-scope cases (n < 30), so validity cannot be called as met until the corpus is enlarged or the run is repeated.

## Phase 4 — Knowledge base and citation verification

**Scope:** Source acquisition and licence register; chunking with locators; embeddings; retrieval with applicability tags; citation resolver and support checker; KB change control.

**Requirements:** FR-KB-01…04, FR-INTV-01, NFR-GOV-01.

**Exit criteria**
- [ ] 100% of generated citations resolve to KB chunks in evaluation.
- [ ] "No supporting source" behaviour verified on out-of-corpus queries.
- [ ] Licence register complete and reviewed.

## Phase 5 — Agent workflow

**Scope:** Orchestrator state machine; Intake Normaliser (free-text proposals); Assessment Summariser; Diagnosis Candidate Drafter; Intervention Suggester; Monitoring & Evaluation Drafter; Verifier; uncertainty flags; kill switch; injection and leakage defences; "clinical logic not independently reviewed" banner while applicable.

**Requirements:** FR-INT-03, FR-STATE-03, FR-COMP-04, FR-CONF-03/04, FR-ADIME-01…07, FR-INTV-02…04, FR-REV-01, FR-ADM-03, FR-GOV-04, NFR-SAF-01/02/04, NFR-REL-02.

**Exit criteria**
- [ ] Safety suite: **zero** critical failures (fabricated values/references, missing-as-normal, directive prescribing language, successful injection).
- [ ] Verifier catches seeded errors at or above the threshold set by the product and clinical requirements lead.
- [ ] Fail-safe behaviours verified (LLM down, hosted provider unavailable or retired, verifier down, KB down).

## Phase 6 — Clinician review UI and approval workflow

**Scope:** Mobile-first review UI; evidence panels; item-level edit/diff; approve/reject/regenerate; trainee co-sign; amendments; exports with watermarks; usability testing with clinicians on synthetic cases.

**Requirements:** FR-REV-02…08, FR-INT-06 (optional), NFR-UX-01…05, NFR-REL-03, NFR-OBS-02.

**Exit criteria**
- [ ] Approval cannot occur without an authorised human action (tested at API and DB levels).
- [ ] Usability study meets agreed task-success and time targets; automation-bias canary detection measured.
- [ ] API penetration test findings resolved.

## Phase 7 — Clinical evaluation and supervised shadow-mode study (adult pathway)

**Scope:** Evaluation per [`EVALUATION.md`](EVALUATION.md): expert-panel review on synthetic and (if approved) de-identified retrospective cases; shadow-mode use where outputs are **not** placed in the record; decision on the real-data inference mode (local, or hosted under an approved framework); ethics, DPIA and regulatory classification; incident-response exercise; go/no-go.

**Requirements:** NFR-GOV-02/03/04, NFR-SAF-03, plus all prior gates.

**Entry conditions**
- [ ] **Independent clinical safety reviewer appointed** and has reviewed the clinical rules, red flags, templates and KB selection (all `independently_reviewed`).
- [ ] Real-data inference mode implemented and security-reviewed (local inference hardware or server, or an approved hosted framework).

**Exit criteria**
- [ ] Ethics approval and DPIA completed; counsel sign-off on legal framework.
- [ ] Clinical evaluation thresholds met (set in advance and ratified by the independent reviewer).
- [ ] Regulatory classification documented.
- [ ] Written go/no-go decision from project owner, independent clinical safety reviewer and privacy lead.

## Phase 8 — Paediatric pathway (non-SAM)

**Scope:** Activate `paediatric_general` following the pathway activation policy (CLINICAL_SAFETY.md §2.4): age-band and growth-standard calculators on versioned reference standards, paediatric elements, rules, red flags, KB sources, hazard-log addendum, synthetic evaluation set and expert-panel review.

**Requirements:** FR-POP-05 plus all prior gates applied to the new pathway.

**Exit criteria:** every activation criterion in CLINICAL_SAFETY.md §2.4 met and recorded in the decision log; independent reviewer sign-off.

## Phase 9 — Severe acute malnutrition (SAM) pathway

**Scope:** Activate `paediatric_sam` under the same policy, with clinician-authored red flags for medical complications that require urgent clinical attention, alignment with national guidance, and its own evaluation set.

**Requirements:** FR-POP-05 plus all prior gates applied to the new pathway.

**Exit criteria:** as Phase 8.

Phases 8 and 9 may be developed in parallel with Phases 5 to 7, but a pathway cannot be activated for clinical use before the independent reviewer signs off its logic.

## Beyond Phase 9 (not planned in detail)

Additional populations (for example pregnancy and lactation); EHR/FHIR integration; multilingual support; post-market monitoring and periodic revalidation.

## Dependencies and risks

| Risk | Mitigation |
|---|---|
| Independent clinical safety reviewer not appointed | Development continues on synthetic data with no clinical claims; Phase 7 is blocked; appoint early so rules are reviewed as written |
| No GPU hardware for local inference | Hosted APIs for synthetic-data development (mode S); mock backend for CI; decide local hardware or approved framework before Phase 7 (Q-05) |
| Hosted model changes or is retired | Pinned versions; scheduled regression and safety suites; fallback backend |
| Real data accidentally reaches a hosted API | Data-class gate, per-request check, build exclusion, identifier scan, synthetic-provenance rules |
| Licence blocks a preferred model or source | Maintain fallback candidates; licence register from Phase 3/4 |
| Scope creep into diagnosis/prescribing or into unvalidated populations | Out-of-scope list and pathway registry in CLINICAL_SAFETY.md; schema forbids such fields; planned pathways refused |
| Evidence base becomes outdated | KB versioning and review cadence |
| Single-developer bus factor | Documentation, decision log, CI-driven workflow |
