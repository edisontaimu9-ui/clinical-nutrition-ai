# Clinical Nutrition AI Agent

> **Status: Phase 0 approved (2026-11-08). Phase 1 (adult data model and deterministic core) in progress.**
> No application code exists yet. This project is **not** a medical device, is **not** approved for clinical use, and must be developed with **synthetic data only** until the gates in [`docs/ROADMAP.md`](docs/ROADMAP.md) are passed.

## Purpose

An independent, privacy-first AI agent that supports a registered dietitian or nutrition clinician by:

1. **Analysing** nutrition assessment information already collected for a patient.
2. **Identifying missing or inconsistent clinical information** so the clinician can complete the assessment.
3. **Drafting ADIME documentation** (Assessment, Diagnosis, Intervention, Monitoring & Evaluation) for clinician review.
4. **Suggesting evidence-based nutrition interventions**, each traceable to a curated, versioned source.

The agent is a **drafting and checking assistant**. The clinician remains the decision-maker and is accountable for everything that enters the record.

## What it does and does not do

| The agent **does** | The agent **never** |
|---|---|
| Highlight missing data and say *what* is missing and *why it matters* | Treats missing data as normal |
| Flag conflicting or implausible values | Silently resolves conflicts |
| Draft ADIME text where every statement traces to an input or a cited source | Fabricates findings, laboratory results or references |
| Suggest candidate nutrition diagnoses and interventions as drafts | Diagnoses a disease or prescribes treatment, diets, feeds or drugs on its own |
| Label all AI content and its uncertainty | Presents drafts as approved or final |
| Keep identifiable data on infrastructure you control (hosted LLM APIs only with synthetic data during development) | Sends identifiable patient data to external AI services without an approved legal and privacy framework |

## Scope and key decisions (v0.2)

- **Population:** adult nutrition assessment first. Paediatric and severe acute malnutrition (SAM) pathways are planned: the data model is ready for them, but they stay inactive and are refused until separately validated (D-018).
- **Models:** model-independent design. Development starts on hosted LLM APIs with **synthetic data only**; local or approved-framework inference is required before any real patient data (D-017).
- **Clinical governance:** the project owner leads product and clinical requirements. An independent senior clinical dietitian/nutrition professional must review the clinical logic before any clinical deployment; the role is configurable and currently vacant (D-019).

## Non-negotiable safety rules

These are enforced in design, code and tests (see [`CLINICAL_SAFETY.md`](CLINICAL_SAFETY.md) §4 for the enforcement mapping):

1. Never autonomously diagnose or prescribe.
2. Never fabricate clinical findings, references or laboratory results.
3. Distinguish **missing** information from **normal** findings.
4. Flag uncertainty and conflicting information.
5. Require clinician review before any clinical output is approved.
6. Use synthetic data during development.
7. Apply least-privilege access, encryption and audit logging.
8. Do not send identifiable patient information to external AI services without an approved legal and privacy framework.

## Document map

| Phase 0 task | Where it is answered |
|---|---|
| 1. Functional and non-functional requirements | [`REQUIREMENTS.md`](REQUIREMENTS.md) |
| 2. System architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) §1–6, §11–13 |
| 3. Clinical scope and limitations | [`CLINICAL_SAFETY.md`](CLINICAL_SAFETY.md) §1–3 |
| 4. Privacy, security, data protection | [`SECURITY.md`](SECURITY.md) |
| 5. Human-in-the-loop approval workflows | [`CLINICAL_SAFETY.md`](CLINICAL_SAFETY.md) §6 |
| 6. PostgreSQL database architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) §8, [`docs/data/schema-design.sql`](docs/data/schema-design.sql) |
| 7. API boundaries and agent responsibilities | [`ARCHITECTURE.md`](ARCHITECTURE.md) §6, §9 |
| 8. Suitable open-weight LLMs | [`ARCHITECTURE.md`](ARCHITECTURE.md) §10 |
| 9. Testing and clinical evaluation | [`docs/EVALUATION.md`](docs/EVALUATION.md) |
| 10. Phased implementation roadmap | [`docs/ROADMAP.md`](docs/ROADMAP.md) |
| Decisions and open questions | [`docs/decisions/decision-log.md`](docs/decisions/decision-log.md) |

## Repository structure

```
clinical-nutrition-ai/
├── README.md                  Project overview (this file)
├── REQUIREMENTS.md            Functional and non-functional requirements
├── ARCHITECTURE.md            Architecture, agents, database, APIs, LLM strategy
├── SECURITY.md                Privacy, security and data protection
├── CLINICAL_SAFETY.md         Clinical scope, hazards, human-in-the-loop workflow
├── docs/
│   ├── ROADMAP.md             Phased plan with exit gates
│   ├── EVALUATION.md          Testing and clinical evaluation plan
│   ├── decisions/             Architecture decision log
│   ├── data/                  Database design (schema-design.sql)
│   ├── clinical/              Generated review sheet, sign-off record, KB curation (later)
│   ├── TRACEABILITY.md        Phase 1 requirements mapped to tests
│   └── api/                   API specifications (future)
├── apps/
│   ├── api/                   Backend API (Phase 1+)
│   └── web/                   Clinician review UI (Phase 6)
├── services/
│   ├── agent/                 Workflow orchestrator and agents (Phase 5)
│   ├── rag/                   Knowledge retrieval and citation checks (Phase 4)
│   └── audit/                 Audit logging service (Phase 2)
├── packages/schemas/          Shared JSON Schemas / data contracts
├── db/
│   ├── migrations/            Versioned migrations (Phase 1)
│   └── seeds/                 Synthetic seed data only
├── data/synthetic/            Synthetic cases only (never real patient data)
├── evaluation/                Datasets, rubrics, harness (Phase 3+)
├── tests/                     unit / integration / safety
├── infra/                     Docker and CI definitions
└── scripts/                   Developer tooling
```

## Roadmap summary

| Phase | Theme | Gate to pass before moving on |
|---|---|---|
| **0** | Requirements, architecture, safety and security design | Written approval of Phase 0 documents |
| 1 | Data model (population-ready), synthetic data, deterministic core for adults (calculators, completeness rules) | Deterministic outputs match clinician-verified reference cases; planned pathways refused |
| 2 | Security and audit baseline | Security checklist passes; audit chain verified |
| 3 | Provider-neutral LLM gateway (mock + hosted, synthetic only), evaluation harness, backend bake-off | Backends selected on synthetic benchmark; hosted-gate tests pass |
| 4 | Curated knowledge base and citation verification | 100% of citations resolve to KB entries |
| 5 | Agent workflow (completeness → conflicts → ADIME → interventions → verifier) | Safety test suite passes with zero critical failures |
| 6 | Clinician review UI and approval workflow | Usability and workflow sign-off |
| 7 | Clinical evaluation and supervised shadow-mode study (adult) | Independent clinical safety reviewer, ethics and privacy approvals; real-data inference mode ready |
| 8 | Paediatric pathway activation | Activation criteria in CLINICAL_SAFETY.md §2.4 met |
| 9 | SAM pathway activation | Same as Phase 8 |

Only one phase is worked at a time. Each phase ends with validation and an explicit approval.

## Getting started

Phase 1 (adult data model and deterministic core) is built and awaiting approval. It uses synthetic data only.

```
pip install -r requirements-dev.txt
python -m pytest -q                                   # unit and safety tests; database tests run when DATABASE_URL is set
python -m cnai.cli analyse data/synthetic/adult/SYN-ADULT-001.json   # needs PYTHONPATH=packages/core/src
python scripts/scan_repo.py                           # synthetic-only and secrets scanner
python scripts/validate_docs.py                       # documents, decisions, traceability
```

Database tests need a throwaway PostgreSQL 16 database whose name contains `test`, `dev`, `ci` or `synthetic`, for example `DATABASE_URL=postgresql://user@localhost/cnai_test`. GitHub Actions runs everything on every push, so a phone does not need PostgreSQL.

For clinical review start with `docs/clinical/rule-review-sheet.md` and record decisions in `docs/clinical/rule-signoff.md`. Requirement coverage is in `docs/TRACEABILITY.md`.

## Disclaimer

This repository describes a research and development project. Nothing in it is medical advice, legal advice or a regulatory determination. Regulatory classification (for example, whether the finished system is software as a medical device in a given jurisdiction), data-protection compliance and ethics approval must be confirmed with qualified professionals before any use with real patients.
