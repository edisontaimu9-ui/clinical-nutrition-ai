# Decision Log

Architecture and clinical-design decisions for the Clinical Nutrition AI Agent. Format: lightweight ADR. All entries are **Proposed** until the project owner approves Phase 0; then they become **Accepted**. Never delete entries; supersede them.

Status values: Proposed · Accepted · Superseded · Rejected.

## Index

| ID | Title | Status |
|---|---|---|
| D-001 | Independent project; patient data stays inside a controlled trust boundary | Proposed |
| D-002 | Deterministic-first design; LLM limited to drafting | Proposed |
| D-003 | Five-state data-element model | Proposed |
| D-004 | "Candidate nutrition diagnosis", never medical diagnosis | Proposed |
| D-005 | PostgreSQL as the single data engine (RLS, pgcrypto, pgvector) | Proposed |
| D-006 | Python/FastAPI modular monolith first | Proposed |
| D-007 | Local open-weight inference behind a single LLM gateway | Proposed |
| D-008 | Terminology: project-defined labels until licensing is confirmed | Proposed |
| D-009 | Synthetic data only until Phase 7 approvals | Proposed |
| D-010 | Append-only, hash-chained audit log | Proposed |
| D-011 | Fixed workflow state machine, not an autonomous agent loop | Proposed |
| D-012 | Model selection deferred to an evidence-based bake-off | Proposed |
| D-013 | Curated, versioned, licence-checked knowledge base; citations by ID only | Proposed |
| D-014 | FHIR R4 as a later interoperability target | Proposed |
| D-015 | Development workflow compatible with constrained devices; CI does heavy work | Proposed |
| D-016 | Phase 0 produces documentation and folder structure only | Proposed |

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

## D-007 · Local open-weight inference behind a single LLM gateway
- **Context:** Safety rule forbids sending identifiable data to external AI services without an approved framework.
- **Decision:** Open-weight models served locally; only the gateway may call models; egress denied.
- **Consequences:** Hardware cost and ops burden; full control and reproducibility.
- **Alternatives:** Hosted frontier APIs (rejected for identifiable data pending an approved framework; may be considered for synthetic-data experiments only).

## D-008 · Terminology: project-defined labels until licensing is confirmed
- **Context:** Standardised nutrition-care terminology sets may be licensed or copyrighted.
- **Decision:** Use project-defined labels and structure (Domain → Category → Item) now; confirm licence terms before ingesting or reproducing any standardised terminology, and map later if permitted.
- **Consequences:** Possible remapping work; avoids licence breach.
- **Alternatives:** Ingest an unlicensed terminology set (rejected).

## D-009 · Synthetic data only until Phase 7 approvals
- **Decision:** All development, test, CI and evaluation use synthetic cases created by clinicians and generators; automated scanning guards against identifiers. Real or de-identified data requires ethics approval, a DPIA and counsel sign-off.
- **Consequences:** Synthetic cases must be clinically realistic; includes deliberate traps (missing vs normal, conflicts, injections).

## D-010 · Append-only, hash-chained audit log
- **Decision:** `audit.event` insert-only with hash chain, verifier job and external anchoring of chain heads. Audit failure ⇒ writes fail closed.
- **Consequences:** Strong tamper evidence; adds write latency and operational dependency.

## D-011 · Fixed workflow state machine, not an autonomous agent loop
- **Decision:** A deterministic orchestrator sequences single-purpose steps; models never choose tools or take actions.
- **Consequences:** Predictable, testable, auditable; less "agentic" flexibility, by design.

## D-012 · Model selection deferred to an evidence-based bake-off
- **Context:** Model landscape and licences change quickly; the shortlist was checked only at a high level on 2026-11-07 (MedGemma terms of use via its developer page; Qwen3, Mistral and Llama licence characterisations via a secondary summary; others unverified).
- **Decision:** Shortlist by licence and hardware fit, then benchmark on the synthetic evaluation harness in Phase 3; record digests; no fine-tuning until justified.
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

---

## Open questions for the project owner

Please answer or consciously defer each; answers will be recorded as decisions.

| # | Question | Why it matters |
|---|---|---|
| Q-01 | **Initial population:** adults only, or also paediatrics / severe acute malnutrition pathways from the start? | Changes calculators, KB and validation scope (CLINICAL_SAFETY.md §2) |
| Q-02 | **Care setting and country:** which facilities and which national guidelines should the KB prioritise? | KB content, legal framework, applicability rules |
| Q-03 | **Clinical lead:** who will act as clinical safety lead and sign off rules and the hazard log? | Required for any safety claim and for Phase 1 rule authoring |
| Q-04 | **Terminology:** are you licensed for a standardised nutrition-care terminology, or should labels stay project-defined? | D-008 |
| Q-05 | **Hosting for real data (future):** on-premises, private cloud, or none until Phase 7? | Security architecture, cost |
| Q-06 | **Inference hardware** available for the Phase 3 bake-off (GPU/CPU/RAM, or rented GPU time)? | Model shortlist and quantisation |
| Q-07 | **Languages:** English only for now? Is Chichewa input needed later? | Model selection and evaluation |
| Q-08 | **Repository licence:** open source (which licence) or proprietary? | Model/KB licence compatibility |
| Q-09 | **Users in scope:** will trainees use the system (co-sign workflow in Phase 6)? | CLINICAL_SAFETY.md §6 |
| Q-10 | **Ethics/privacy contacts:** which research ethics committee and data-protection adviser will be engaged? | Phase 7 timeline |
