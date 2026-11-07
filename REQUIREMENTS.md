# Requirements

Version 0.1 · Status: **Draft for approval** · Phase 0

Priority uses MoSCoW: **M** must, **S** should, **C** could. "Phase" is the roadmap phase in which the requirement is first delivered ([`docs/ROADMAP.md`](docs/ROADMAP.md)). IDs are stable; never reuse one.

## 1. Context, users and assumptions

**Users**

| Role | Description |
|---|---|
| Dietitian / nutrition clinician | Primary user. Reviews, edits, approves or rejects drafts. |
| Supervising clinician | Co-signs outputs of trainees or provisionally-licensed users where required. |
| Clinical safety lead | Owns clinical rules, hazard log and release sign-off. |
| Administrator | Manages users and configuration. **No** access to patient content. |
| Auditor | Read-only access to audit trail. |
| Engineer / ML engineer | Works with **synthetic data only**. |

**Assumptions**

- A1. Assessment data is entered or imported by a clinician; the agent does not interview patients.
- A2. Inference runs on infrastructure under the project's control (on-premises, private cloud or a hosted environment covered by an approved legal and privacy framework).
- A3. Primary language is English. Chichewa and other languages are out of scope until clinically validated.
- A4. Deployment settings may be low-resource (limited bandwidth, intermittent power); the design must not assume always-on high-speed connectivity.

**Out of scope (initial release)**

Medical diagnosis; prescribing of drugs, diets, enteral or parenteral formulas; drug dosing; emergency triage; direct patient-facing advice; image-based assessment; autonomous charting into an external EHR; any use with real identifiable data before Phase 7 approvals.

## 2. Functional requirements

### 2.1 Case and assessment intake (FR-INT)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-INT-01 | Create and manage a case (patient pseudonym, encounter, setting) with identity data held separately from clinical data. | M | 1 |
| FR-INT-02 | Capture assessment data as structured data elements covering anthropometry, biochemical data, clinical/physical findings, diet and intake history, nutrition-focused physical findings, functional status, medications and supplements, relevant medical history, and social/food-access context. | M | 1 |
| FR-INT-03 | Accept free-text clinical notes as **untrusted input** and extract candidate data elements into a *proposed* state that the clinician confirms. | S | 5 |
| FR-INT-04 | Record provenance for every value (who entered it, when, source, unit as entered, converted value). | M | 1 |
| FR-INT-05 | Support corrections as new versions; never overwrite history. | M | 1 |
| FR-INT-06 | Provide import via a documented schema (JSON; FHIR R4 mapping as a later option). | C | 6 |

### 2.2 Data-state integrity (FR-STATE)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-STATE-01 | Every data element has an explicit state: `present`, `not_assessed`, `not_applicable`, `unknown_or_unreliable`, or `declared_normal_by_clinician`. | M | 1 |
| FR-STATE-02 | The system never infers `normal` from absence. Absence is `not_assessed` unless a clinician states otherwise. | M | 1 |
| FR-STATE-03 | Outputs distinguish "normal", "abnormal", "not assessed" and "unknown" visually and in structured data. | M | 5 |

### 2.3 Completeness analysis (FR-COMP)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-COMP-01 | Deterministic rules identify missing elements required or recommended for the selected assessment pathway and population. | M | 1 |
| FR-COMP-02 | Each gap states: what is missing, why it matters, and its criticality (`blocking`, `important`, `optional`). | M | 1 |
| FR-COMP-03 | Gap rules are authored, versioned and approved by a clinician; the LLM may phrase explanations but cannot add or remove rules. | M | 1 |
| FR-COMP-04 | Provide a suggested follow-up question or measurement for each gap, phrased as a prompt to the clinician. | S | 5 |

### 2.4 Calculation and validation (FR-CALC)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-CALC-01 | All numeric derivations (BMI, percent weight change, growth z-scores, MUAC classification, estimated energy/protein ranges, etc.) are computed by deterministic, unit-tested code, never by the LLM. | M | 1 |
| FR-CALC-02 | Each calculation records formula identifier, version, inputs and the reference standard used. | M | 1 |
| FR-CALC-03 | Plausibility ranges and unit sanity checks reject or flag impossible values (e.g., height in cm entered as metres). | M | 1 |
| FR-CALC-04 | Calculations refuse to run on inputs that are missing, unreliable or not applicable and say so. | M | 1 |

### 2.5 Conflict and uncertainty detection (FR-CONF)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-CONF-01 | Detect conflicts between values (e.g., two different weights for one date, stated intake vs. documented NPO status). | M | 1 / 5 |
| FR-CONF-02 | Detect stale values (age of measurement exceeds the rule-defined window). | S | 1 |
| FR-CONF-03 | Every draft item carries an uncertainty flag (`supported`, `partially_supported`, `needs_clinician_input`, `conflicting_inputs`) with a human-readable reason. | M | 5 |
| FR-CONF-04 | Conflicts are never auto-resolved; both values and sources are displayed. | M | 5 |

### 2.6 ADIME drafting (FR-ADIME)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-ADIME-01 | Draft the Assessment section from confirmed data, citing the data elements used. | M | 5 |
| FR-ADIME-02 | Propose **candidate** nutrition diagnoses in PES format (Problem, Etiology, Signs/Symptoms), each linked to supporting evidence and marked "for clinician determination". | M | 5 |
| FR-ADIME-03 | Draft Intervention and Monitoring & Evaluation sections from approved clinician selections and cited guidance. | M | 5 |
| FR-ADIME-04 | Every statement in a draft links to at least one input observation and/or knowledge-base citation, or is explicitly marked as a prompt for clinician input. | M | 5 |
| FR-ADIME-05 | The draft must not state a finding for any data element whose state is not `present` or `declared_normal_by_clinician`. | M | 5 |
| FR-ADIME-06 | Drafts are structured (JSON validated against schema) and rendered to text; free-form-only output is not accepted. | M | 5 |
| FR-ADIME-07 | Terminology: use project-defined labels. Licensed terminology sets (e.g., standardized nutrition-care terminology) are only used after licence confirmation (decision D-008). | M | 1 |

### 2.7 Intervention suggestions (FR-INTV)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-INTV-01 | Suggest interventions only from the curated knowledge base, each with source, version, section locator and applicability conditions. | M | 4 |
| FR-INTV-02 | Present suggestions as options for the clinician; no suggestion is phrased as an order or prescription. | M | 5 |
| FR-INTV-03 | Show contraindication and caution prompts defined by clinician-authored rules (e.g., refeeding risk, dysphagia, renal restriction) as flags for review. | M | 5 |
| FR-INTV-04 | Quantities (energy, protein, fluid) appear only as ranges produced by FR-CALC and are labelled with method and assumptions. | M | 5 |

### 2.8 Knowledge base and references (FR-KB)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-KB-01 | Maintain a curated, versioned corpus (source, edition, licence, content hash). | M | 4 |
| FR-KB-02 | Citations are generated only by reference to KB chunk IDs; a verifier confirms every citation resolves and that the cited text supports the claim. | M | 4 |
| FR-KB-03 | The agent must say "no supporting source found" rather than cite from model memory. | M | 4 |
| FR-KB-04 | KB updates go through change control with clinical sign-off and a regression evaluation. | M | 4 |

### 2.9 Clinician review and approval (FR-REV)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-REV-01 | All AI outputs are created in state `draft` and visibly watermarked as AI-generated and unapproved. | M | 5 |
| FR-REV-02 | A draft can become `approved` only by an authorised human via an explicit action recorded with identity, time and content hash. | M | 5 |
| FR-REV-03 | Reviewers can approve, edit-and-approve, reject, or request regeneration, with a reason for edits and rejections. | M | 6 |
| FR-REV-04 | Trainee/provisional users require co-signature by an authorised supervisor before approval takes effect. | M | 6 |
| FR-REV-05 | Edits are stored as item-level diffs against the AI draft. | M | 6 |
| FR-REV-06 | Approved content is immutable; amendments create a new version linked to the prior one. | M | 6 |
| FR-REV-07 | Nothing is exported or shared as "final" unless approved. Exports of drafts carry the draft watermark. | M | 6 |
| FR-REV-08 | The system blocks approval while `blocking` gaps or unresolved conflicts exist, unless the reviewer records an explicit override reason. | S | 6 |

### 2.10 Audit, administration and operations (FR-AUD / FR-ADM)

| ID | Requirement | P | Phase |
|---|---|---|---|
| FR-AUD-01 | Log every access to patient data and every create, update, approve, reject, export and permission change. | M | 2 |
| FR-AUD-02 | Log every model run with model identifier and digest, prompt template version, KB snapshot, input hash and output hash. | M | 3 |
| FR-AUD-03 | Audit records are append-only and tamper-evident (hash chain) with a verification tool. | M | 2 |
| FR-ADM-01 | Role-based user management with least-privilege defaults and periodic access review support. | M | 2 |
| FR-ADM-02 | Configuration of rules, prompts, models and KB versions is versioned and traceable to each output. | M | 3 |
| FR-ADM-03 | An emergency "kill switch" disables AI generation while leaving the rest of the record accessible. | S | 5 |

## 3. Non-functional requirements

### 3.1 Safety and quality (NFR-SAF)

| ID | Requirement | P |
|---|---|---|
| NFR-SAF-01 | Zero tolerance in release gating for fabricated clinical values, laboratory results or references in evaluation sets. | M |
| NFR-SAF-02 | Every safety rule in `README.md` maps to at least one automated test in `tests/safety`. | M |
| NFR-SAF-03 | A hazard log is maintained and reviewed before each release. | M |
| NFR-SAF-04 | The system fails safe: if the model, verifier or KB is unavailable, it returns "unable to draft" plus deterministic completeness output, never a partial unverified draft. | M |
| NFR-SAF-05 | Reproducibility: given the same inputs, configuration, model digest and seed, outputs are re-generable or at least fully attributable. | S |

### 3.2 Security and privacy (NFR-SEC)

Detailed controls are in [`SECURITY.md`](SECURITY.md).

| ID | Requirement | P |
|---|---|---|
| NFR-SEC-01 | Encryption in transit (TLS 1.2+, 1.3 preferred) for all connections, including internal service links. | M |
| NFR-SEC-02 | Encryption at rest for databases, backups and object storage; field-level encryption for direct identifiers. | M |
| NFR-SEC-03 | Least-privilege authorisation enforced in the application **and** in PostgreSQL (row-level security). | M |
| NFR-SEC-04 | No identifiable patient data leaves the trust boundary to external AI services without an approved legal and privacy framework. Egress is blocked by default and tested. | M |
| NFR-SEC-05 | Multi-factor authentication for all human users with access to patient data. | M |
| NFR-SEC-06 | Secrets are never stored in the repository; automated secret scanning in CI. | M |
| NFR-SEC-07 | Development, test and CI environments contain synthetic data only. | M |
| NFR-SEC-08 | LLM-specific threats (prompt injection, data leakage, insecure output handling) have documented mitigations and tests. | M |

### 3.3 Performance and reliability (NFR-PERF / NFR-REL)

| ID | Requirement | P |
|---|---|---|
| NFR-PERF-01 | Deterministic completeness and calculation results return in under 2 seconds at the 95th percentile for a single case (proposed target, to be validated). | S |
| NFR-PERF-02 | An ADIME draft completes within a bounded time (proposed: under 120 seconds on reference hardware) and shows progress; the clinician can continue other work meanwhile. | S |
| NFR-PERF-03 | Inference must run on commodity or modest GPU/CPU hardware; minimum hardware tiers are defined during the Phase 3 bake-off. | S |
| NFR-REL-01 | Recovery point objective and recovery time objective are defined and tested via restore drills (targets set by the clinical sponsor). | M |
| NFR-REL-02 | Graceful degradation: the rest of the application works if inference is down. | M |
| NFR-REL-03 | Intermittent connectivity: clinician UI tolerates reconnects without data loss (draft autosave). | S |

### 3.4 Usability, accessibility, localisation (NFR-UX)

| ID | Requirement | P |
|---|---|---|
| NFR-UX-01 | Review UI is mobile-friendly and usable on small screens and low bandwidth. | M |
| NFR-UX-02 | Evidence for each draft statement is one tap/click away. | M |
| NFR-UX-03 | AI-generated and unapproved content is unmistakable (labels, colour and text, not colour alone). | M |
| NFR-UX-04 | WCAG 2.2 AA as the accessibility target for the web UI. | S |
| NFR-UX-05 | Units configurable (metric default); no silent unit conversion. | M |

### 3.5 Maintainability, interoperability, observability (NFR-MNT / NFR-INT / NFR-OBS)

| ID | Requirement | P |
|---|---|---|
| NFR-MNT-01 | Modular services with documented contracts; deterministic engines separable from LLM components. | M |
| NFR-MNT-02 | Infrastructure and schema changes via versioned migrations and reviewed pull requests. | M |
| NFR-MNT-03 | Architecture decisions recorded in `docs/decisions/decision-log.md`. | M |
| NFR-INT-01 | Open standards preferred: JSON Schema, OpenAPI 3.1, OIDC, FHIR R4 (mapping, later). | S |
| NFR-OBS-01 | Structured logs, metrics and traces, with **no patient identifiers or clinical content** in operational logs. | M |
| NFR-OBS-02 | Quality monitoring dashboards: edit rates, rejection reasons, flagged-hallucination counts, verifier failures. | S |

### 3.6 Legal, governance and licensing (NFR-GOV)

| ID | Requirement | P |
|---|---|---|
| NFR-GOV-01 | Model licences and KB source licences are recorded and compatible with intended use (see D-012, D-013). | M |
| NFR-GOV-02 | A data protection impact assessment is completed before any real-data processing. | M |
| NFR-GOV-03 | Ethics approval is obtained before any study using real or de-identified patient data. | M |
| NFR-GOV-04 | Regulatory classification of the system is assessed and documented before clinical deployment. | M |

## 4. Traceability

Each requirement ID is referenced by tests (`tests/**`) and by the roadmap exit criteria. A traceability matrix (requirement → design element → test → evidence) is created in Phase 1 and maintained thereafter.

## 5. Acceptance of Phase 0

Phase 0 is accepted when the project owner and clinical lead confirm: requirements are complete and correctly prioritised; out-of-scope items are agreed; open questions in the decision log are answered or consciously deferred.
