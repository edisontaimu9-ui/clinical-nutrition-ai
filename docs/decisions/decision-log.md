# Decision Log

Version 0.4 · Phase 1 decisions D-020 to D-022 added as Proposed · Phase 0 status: **approved by the project owner on 2026-11-08**. Changes since v0.2: all decisions D-001 to D-019 moved to Accepted; the suggested defaults for all open questions were accepted (see the questions table); Phase 1 started.

Architecture and clinical-design decisions for the Clinical Nutrition AI Agent. Format: lightweight ADR. Never delete entries; supersede them.

**Approval record (2026-11-08):** the project owner approved the Phase 0 v0.2 documents and accepted the suggested defaults for Q-02, Q-04, Q-05, Q-07, Q-08, Q-09, Q-10, Q-11 and Q-12 with a "Yes" in reply to a message offering exactly that. Items that still need a real-world action (naming facility types, choosing a hosted provider, appointing the reviewer) are listed under "Follow-ups" below.

Status values: Proposed · Accepted · Superseded · Rejected.

## Index

| ID | Title | Status |
|---|---|---|
| D-001 | Independent project; patient data stays inside a controlled trust boundary | Accepted |
| D-002 | Deterministic-first design; LLM limited to drafting | Accepted |
| D-003 | Five-state data-element model | Accepted |
| D-004 | "Candidate nutrition diagnosis", never medical diagnosis | Accepted |
| D-005 | PostgreSQL as the single data engine (RLS, pgcrypto, pgvector) | Accepted |
| D-006 | Python/FastAPI modular monolith first | Accepted |
| D-007 | Real-data inference stays local; hosted APIs only under D-017 | Accepted (amended by D-017) |
| D-008 | Terminology: project-defined labels until licensing is confirmed | Accepted |
| D-009 | Synthetic data only until Phase 7 approvals | Accepted |
| D-010 | Append-only, hash-chained audit log | Accepted |
| D-011 | Fixed workflow state machine, not an autonomous agent loop | Accepted |
| D-012 | Model selection deferred to an evidence-based bake-off | Accepted |
| D-013 | Curated, versioned, licence-checked knowledge base; citations by ID only | Accepted |
| D-014 | FHIR R4 as a later interoperability target | Accepted |
| D-015 | Development workflow compatible with constrained devices; CI does heavy work | Accepted |
| D-016 | Phase 0 produces documentation and folder structure only | Accepted |
| D-017 | Hosted LLM APIs for synthetic-data development; model-independent gateway | Accepted |
| D-018 | Adult pathway first; data model ready for paediatric and SAM | Accepted |
| D-019 | Configurable governance roles; independent clinical review before clinical deployment | Accepted |
| D-020 | Instance data class enforced by the database and the engine (synthetic or real) | Proposed |
| D-021 | Phase 1 deterministic core: catalogue as single source of truth, exact decimal arithmetic, no LLM | Proposed |
| D-022 | Clinical sign-off is a recorded human act; software never marks content reviewed | Proposed |

---

## D-001 · Independent project; data stays inside a controlled trust boundary
- **Context:** The system will handle sensitive health data. Existing personal projects use hosted backends (for example edge workers and managed Postgres) that are fine for non-sensitive data.
- **Decision:** This is a standalone project. No patient data flows to other projects or to hosted third-party services. Hosted platforms may be used for **non-PHI development** only; identifiable data requires an approved legal and privacy framework (SECURITY.md §7).
- **Consequences:** Self-hosted or contractually covered infrastructure is needed for real data; slower early convenience, stronger privacy.
- **Alternatives:** Reuse existing hosted stack for everything (rejected: legal and privacy exposure).

## D-002 · Deterministic-first design
- **Context:** LLMs are unreliable at arithmetic, rule application and absence detection.
- **Decision:** Rules and calculators determine missing/conflicting/numeric facts; the LLM drafts text from verified facts only.
- **Consequences:** More upfront rule authoring and clinical review; much smaller hallucination surface; deterministic report works if AI is down.
- **Alternatives:** End-to-end LLM reasoning (rejected: unverifiable).

## D-003 · Five-state data-element model
- **Context:** Safety rule: distinguish missing from normal.
- **Decision:** States: `present`, `not_assessed`, `not_applicable`, `unknown_or_unreliable`, `declared_normal_by_clinician`; enforced by DB constraints; absence = `not_assessed`.
- **Consequences:** Slightly heavier data entry; structural protection against silent normalisation.
- **Alternatives:** Nullable fields with an implicit "normal" (rejected).

## D-004 · "Candidate nutrition diagnosis", never medical diagnosis
- **Context:** ADIME includes a nutrition diagnosis (PES). The safety rule forbids autonomous diagnosis.
- **Decision:** The agent proposes *candidate* PES statements with evidence, flagged "for clinician determination"; it never states medical diagnoses; the clinician adopts, edits or discards.
- **Consequences:** Preserves usefulness while keeping professional accountability.
- **Alternatives:** Omit diagnosis drafting (rejected: removes core ADIME value); allow confident diagnoses (rejected: violates safety rule).

## D-005 · PostgreSQL as the single data engine
- **Context:** Need relational integrity, RLS, JSONB, encryption helpers and vector search.
- **Decision:** PostgreSQL 16+ with RLS, pgcrypto and pgvector; separate schemas per concern; vector store may move to a dedicated in-boundary engine later if scale demands.
- **Consequences:** One operational surface; extension availability must be checked for the chosen hosting.
- **Alternatives:** Separate vector DB from day one (deferred).

## D-006 · Python/FastAPI modular monolith first
- **Context:** Small team; need AI ecosystem and schema validation.
- **Decision:** Modular monolith with clear service boundaries in `services/`; split into separate deployables only for the LLM runtime and (if needed) the audit service.
- **Consequences:** Faster development; boundaries must be policed by tests.
- **Alternatives:** Microservices from the start (rejected: overhead).

## D-007 · Real-data inference stays local; hosted APIs only under D-017
- **Status note:** amended 2026-11-08 by D-017.
- **Context:** Safety rule forbids sending identifiable data to external AI services without an approved framework.
- **Decision:** Any instance that handles real or identifiable patient data uses local/self-hosted open-weight inference, or a hosted environment under an approved framework (mode A in D-017). Only the LLM gateway may call models; egress is denied elsewhere.
- **Consequences:** Hardware or server cost and operations burden before real-data use; full control and reproducibility.
- **Alternatives:** Hosted frontier APIs for real data (rejected pending an approved framework). Hosted APIs for synthetic-data development are permitted by D-017.

## D-008 · Terminology: project-defined labels until licensing is confirmed
- **Context:** Standardised nutrition-care terminology sets may be licensed or copyrighted.
- **Decision:** Use project-defined labels and structure (Domain → Category → Item) now; confirm licence terms before ingesting or reproducing any standardised terminology, and map later if permitted.
- **Consequences:** Possible remapping work; avoids licence breach.
- **Alternatives:** Ingest an unlicensed terminology set (rejected).

## D-009 · Synthetic data only until Phase 7 approvals
- **Decision:** All development, test, CI and evaluation use synthetic cases created by clinicians and generators; automated scanning guards against identifiers. Real or de-identified data requires ethics approval, a DPIA and counsel sign-off.
- **Consequences:** Synthetic cases must be clinically realistic; includes deliberate traps (missing vs normal, conflicts, injections).
- **Clarification (v0.2):** Synthetic means invented or generated, not real records that were scrubbed or masked. Cases must not be modelled on identifiable real patients, including rare combinations of age, place, diagnosis and dates. Clinician-authored cases are reviewed for re-identification risk before merging (SECURITY.md §7.2). This matters more now that synthetic prompts may be sent to hosted APIs (D-017).

## D-010 · Append-only, hash-chained audit log
- **Decision:** `audit.event` insert-only with hash chain, verifier job and external anchoring of chain heads. Audit failure ⇒ writes fail closed.
- **Consequences:** Strong tamper evidence; adds write latency and operational dependency.

## D-011 · Fixed workflow state machine, not an autonomous agent loop
- **Decision:** A deterministic orchestrator sequences single-purpose steps; models never choose tools or take actions.
- **Consequences:** Predictable, testable, auditable; less "agentic" flexibility, by design.

## D-012 · Model selection deferred to an evidence-based bake-off
- **Context:** Model landscape and licences change quickly; the shortlist was checked only at a high level on 2026-11-07 (MedGemma terms of use via its developer page; Qwen3, Mistral and Llama licence characterisations via a secondary summary; others unverified).
- **Decision:** Shortlist by licence and hardware fit, then benchmark on the synthetic evaluation harness in Phase 3; record digests; no fine-tuning until justified.
- **Update (v0.2):** Until local hardware exists (D-017), the bake-off compares the mock backend and hosted model(s) using synthetic data. Results do not transfer between backends, so any new backend, provider model version or quantisation re-runs the full suite.
- **Consequences:** No model is committed in Phase 0.

## D-013 · Curated, versioned, licence-checked knowledge base
- **Decision:** Only licensed or openly licensed sources ingested; each with edition, version, hash and locator; citations by chunk ID only; verifier checks support; "no source found" is a valid output.
- **Consequences:** Curation effort; strong anti-fabrication property.

## D-014 · FHIR R4 as a later interoperability target
- **Decision:** Design data elements so they can map to FHIR resources later; no integration before Phase 6.

## D-015 · Development workflow compatible with constrained devices
- **Context:** Development may be done from a phone/terminal environment where local containers and GPUs are unavailable.
- **Decision:** Keep the repository light to clone; run tests, scans and builds in CI; provide a CPU-only, small-model "dev profile" for the LLM gateway and a mock model for unit tests; heavy inference and evaluation run on dedicated infrastructure (using synthetic data only).
- **Consequences:** CI minutes and a remote runner/GPU host become project needs.

## D-016 · Phase 0 produces documentation and folder structure only
- **Decision:** No application code, schemas as migrations, or model downloads in Phase 0. `docs/data/schema-design.sql` is a design reference and must not be executed against any environment.

## D-017 · Hosted LLM APIs for synthetic-data development; model-independent gateway
- **Context:** The owner works from an Android/Termux environment with no GPU hardware. The safety rule forbids sending identifiable patient data to external AI services without an approved framework.
- **Decision:**
  1. Four backend modes: **T** test (deterministic mock), **S** synthetic-hosted (hosted LLM API, **synthetic data only**), **L** local/self-hosted inference (default for real data), **A** approved-hosted (a hosted provider under an approved legal and privacy framework; unavailable until one exists).
  2. Hosted adapters load only when the instance data class is `synthetic`; each request is checked against its case; real-data and clinical builds exclude hosted adapters entirely (SECURITY.md §7.3).
  3. Core code depends only on a provider-neutral gateway interface. Provider SDKs live in adapters; structured output is validated by project code; no provider-specific features in clinical logic.
  4. Provider, model identifier and version are pinned and recorded for every run; the same evaluation suite runs against every backend; a mock backend lets CI run with no network or spend.
  5. Local inference is deferred, not dropped: it (or mode A) must exist before any real patient data is processed (Phase 7 entry condition).
- **Consequences:** Fast start without hardware. Risks: hosted-model drift or retirement, provider terms, spend, and key handling from a phone; evaluation results from hosted models do not certify local ones. Mitigations are in SECURITY.md §7.3 and ARCHITECTURE.md §10.2.
- **Alternatives:** Wait for GPU hardware (rejected: blocks progress); hosted APIs for real data (rejected pending an approved framework).

## D-018 · Adult pathway first; data model ready for paediatric and SAM
- **Context:** The product should eventually serve adults and paediatric patients, including severe acute malnutrition (SAM), but validation effort and risk differ greatly by population.
- **Decision:** `adult_general` is the only active pathway. `paediatric_general` and `paediatric_sam` are registered as `planned` and are refused by the API and database until activated under the policy in CLINICAL_SAFETY.md §2.4. The data model already carries age in months, a pathway registry with age bands, population-specific data elements (disabled until activation), and versioned reference standards. New roadmap Phases 8 and 9 cover activation.
- **Consequences:** Slightly more schema and tests up front (including refusal tests); no breaking migration later; adult assumptions cannot silently leak into children.
- **Alternatives:** Adult-only schema (rejected: costly migration and hidden assumptions); build all populations now (rejected: validation burden).

## D-019 · Configurable governance roles; independent clinical review before clinical deployment
- **Context:** The owner leads product development and clinical requirements. Independent clinical safety oversight is needed before clinical use, but the reviewer has not been appointed.
- **Decision:** Roles are assignments by function, not people: **Product and clinical requirements lead** (assigned: project owner) and **Independent clinical safety reviewer** (a senior clinical dietitian/nutrition professional; status `vacant` until formally appointed, recorded in this log). Rules, red flags, templates and KB sources carry a review status: `draft`, `author_approved`, `independently_reviewed`; an author cannot independently review their own item. While the reviewer role is vacant: synthetic-data development is allowed and content may reach `author_approved`; real patient data, shadow or clinical use, and any claim of clinical validation are not. Instances in clinical mode refuse to start unless the reviewer role is assigned and all active rules are independently reviewed.
- **Consequences:** Phases 1 to 6 can proceed; Phase 7 is blocked until the appointment. Appointing early lets rules be reviewed as they are written.
- **Alternatives:** Project owner as sole approver (rejected: no independence).

## D-020 · Instance data class enforced by the database and the engine
- **Context:** D-009 and D-017 allow hosted APIs only for synthetic data, so "synthetic only" must be a technical fact, not a habit.
- **Decision:** Each instance records its data class (`synthetic` or `real`) in `ops.instance_config`. Patients carry an `is_synthetic` flag that must match, and both are immutable. Real data class is impossible in development mode. The engine refuses a non-synthetic case in a synthetic instance. A repository scanner rejects identifier-like content in synthetic data and secrets anywhere.
- **Consequences:** A mistake cannot silently put real data into a development instance. Moving to real data (Phase 7) needs a separate, deliberate instance and its own approvals.
- **Alternatives:** Policy-only control (rejected: not testable).

## D-021 · Phase 1 deterministic core: catalogue as single source of truth, exact decimal arithmetic, no LLM
- **Context:** Phase 1 must prove that missing, conflicting and numeric facts are decided by code a clinician can read and review.
- **Decision:** The data-element catalogue and pathway registry live in JSON and generate a database migration (with a drift check). Calculators use exact decimal arithmetic with half-up rounding and report formula id, version, inputs and reference. Clinical cut-offs and rule text are listed in a generated review sheet read from the same code tables. The core package has no network or LLM imports. Rule content starts as `draft`.
- **Consequences:** Documents and code cannot drift apart. Reference cases, cut-offs and rules still need clinician verification before they are treated as correct.
- **Alternatives:** Hand-maintained SQL and docs (rejected: drift); floating point (rejected: rounding surprises at category boundaries).

## D-022 · Clinical sign-off is a recorded human act; software never marks content reviewed
- **Context:** FR-GOV-02 requires author approval and independent review, and the independent reviewer is not yet appointed.
- **Decision:** Sign-off is recorded by the signer in `docs/clinical/rule-signoff.md`, and a rule set's `review_status` changes only after that. Engineering may prepare review material (the generated review sheet) but may not change a review status or mark reference cases verified. Until independent review is recorded, every report carries the "not independently reviewed" banner.
- **Consequences:** Phase 1 can finish its engineering while the clinical exit items stay open and visible.
- **Alternatives:** Engineering marks items approved on the owner's verbal consent (rejected: no record, no independence).

---

## Open questions for the project owner

Status as of v0.2. Answered questions are linked to the decision that records them. For each open question a **suggested default** is given, so you can accept it with a one-line reply.

| # | Question | Status |
|---|---|---|
| Q-01 | Initial population: adults only, or also paediatrics / SAM from the start? | **Answered** — adult first; model ready for paediatric and SAM (D-018) |
| Q-02 | Care setting and country: which facilities and national guidelines should the KB prioritise? | **Closed: default accepted 2026-11-08.** Suggested default: Malawi; hospital inpatient and outpatient nutrition services; KB priority is national guidance first, then international sources as licences allow (confirm facility types) |
| Q-03 | Who is the clinical safety lead? | **Answered** — owner leads product and clinical requirements; independent senior clinical dietitian/nutrition professional to be appointed; role configurable (D-019) |
| Q-04 | Terminology: licensed standardised nutrition-care terminology, or project-defined labels? | **Closed: default accepted 2026-11-08.** Suggested default: project-defined labels (D-008) until licensing is confirmed |
| Q-05 | Hosting for real data (future): on-premises, private cloud, or none until Phase 7? | **Closed: default accepted 2026-11-08.** Suggested default: no real data before Phase 7; decide then between a local server (mode L) and an approved hosted framework (mode A) |
| Q-06 | Inference hardware for the bake-off? | **Answered** — no GPU; start with hosted APIs on synthetic data; model-independent design (D-017) |
| Q-07 | Languages: English only for now? Chichewa input later? | **Closed: default accepted 2026-11-08.** Suggested default: English only; revisit after Phase 7 |
| Q-08 | Repository licence: open source (which) or proprietary? | **Closed: default accepted 2026-11-08.** Suggested default: keep the repository private and unlicensed (all rights reserved) until decided; decide before making it public or accepting contributors |
| Q-09 | Will trainees use the system (co-sign workflow)? | **Closed: default accepted 2026-11-08.** Suggested default: the schema supports supervised co-sign from the start; enable it in Phase 6 |
| Q-10 | Ethics/privacy contacts: which research ethics committee and data-protection adviser? | **Closed: default accepted 2026-11-08.** Suggested default: identify both before Phase 6 so Phase 7 can be scheduled |
| Q-11 | Hosted provider(s) and budget for synthetic-data development: which API access do you have, and what monthly spend cap? | **Closed: default accepted 2026-11-08.** Suggested default: one primary hosted provider plus the mock backend; set a hard spend cap; confirm no-training and minimal-retention terms before first use and record them here |
| Q-12 | When do you expect to identify the independent clinical safety reviewer? | **Closed: default accepted 2026-11-08.** Suggested default: name a candidate before Phase 1 exits so the first rule set can be independently reviewed early |

### Follow-ups still needing real-world action

| # | Follow-up | Owner | Needed by |
|---|---|---|---|
| F-01 | Confirm the facility types the KB and rules should target (Q-02 default: Malawi hospital inpatient and outpatient nutrition services) | Project owner | Before KB sourcing, Phase 4 |
| F-02 | Choose the first hosted provider, set a hard spend cap, and record its retention and no-training terms here (Q-11) | Project owner | Phase 3 |
| F-03 | Name a candidate independent clinical safety reviewer (Q-12) | Project owner | Before Phase 1 exits, so the first rule set can be independently reviewed |
| F-04 | Identify the research ethics committee and data-protection adviser (Q-10) | Project owner | Before Phase 6 |
| F-05 | Decide between a local server (mode L) and an approved hosted framework (mode A) for real data (Q-05) | Project owner | Before Phase 7 |
