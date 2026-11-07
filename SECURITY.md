# Security, Privacy and Data Protection

Version 0.1 · Status: **Draft for approval** · Phase 0

This document sets the minimum security and privacy requirements. It is not legal advice. Applicable law (for example national data-protection legislation, health-sector regulation and professional-practice rules) **must be confirmed with qualified counsel** before any processing of real patient data (NFR-GOV-02).

## 1. Principles

1. **Privacy by design and default.** Collect the minimum, keep it inside the trust boundary, separate identity from clinical data.
2. **Least privilege.** Every user, service and database role gets only what it needs.
3. **Defence in depth.** Application checks *and* database enforcement *and* network controls.
4. **Assume the model is untrusted.** The LLM can be manipulated or wrong; it gets no write access, no network and no authority.
5. **Everything auditable.** Access and change events are recorded in a tamper-evident log.
6. **Synthetic data until approved.** Real data is prohibited in development, test, CI and evaluation until the Phase 7 approvals.

## 2. Data classification

| Class | Examples | Handling |
|---|---|---|
| **C3 Restricted – identifiers** | Names, contact details, national/hospital IDs, exact dates of birth, photographs | Identity vault only; field-level encryption; narrowest access |
| **C3 Restricted – clinical** | Assessment data, notes, drafts, approvals | Encrypted; RLS; audited; never in logs or analytics |
| **C2 Internal** | Configuration, rules, prompt templates, model digests, KB metadata | Access-controlled; integrity-protected |
| **C1 Public** | Published documentation | No restriction |
| **Synthetic** | Generated test cases | Marked `synthetic=true`; never mixed with real data in one database |

**Free text is treated as identifiable by default.** Automated de-identification of free text is unreliable; de-identified status is claimed only after a documented, validated process and privacy review.

## 3. Roles and access control

| Role | Patient content | Notes |
|---|---|---|
| `clinician` | Read/write for patients in their care relationship | Cannot administer users |
| `trainee` | As clinician but approvals require co-sign | Supervisor link recorded |
| `supervisor` | As clinician plus co-sign | |
| `clinical_safety_lead` | Aggregated/sampled review access for safety incidents | Access logged and justified |
| `admin` | **None** | Manages users, config, kill switch |
| `auditor` | None (audit metadata only) | Read-only audit |
| `engineer` | **None in production**; synthetic only elsewhere | Break-glass is time-boxed, approved, logged |
| Service accounts | Scoped to one service's schemas | No shared credentials; short-lived tokens where possible |

Controls:

- Role-based access control plus attribute checks (facility, care relationship).
- **PostgreSQL row-level security** on all patient-related tables; `FORCE ROW LEVEL SECURITY`; per-request session context (`app.user_id`, `app.org_id`).
- Separate DB roles for each service; no application role is table owner or superuser.
- Quarterly access reviews; automatic deprovisioning on role change.
- **Break-glass** access: justification, time limit, second-person approval where feasible, high-priority audit alert and post-hoc review.

## 4. Authentication

- OIDC with a self-hosted identity provider (no external IdP receives patient data).
- MFA mandatory for all human accounts with data access (NFR-SEC-05).
- Short-lived access tokens, refresh rotation, session timeout on inactivity, device/session listing and revocation.
- Strong password/passkey policy; lockout and rate limiting; breached-password checks.
- Service-to-service: mutual TLS or signed tokens; no static shared secrets.

## 5. Encryption and key management

| Layer | Requirement |
|---|---|
| In transit | TLS 1.2 minimum (1.3 preferred) for client and **all internal** links; HSTS; modern cipher suites |
| At rest | Volume/database encryption for primary, replicas, backups and object storage |
| Field level | Envelope encryption (AES-256-GCM or equivalent) for identity fields and free-text notes; data keys wrapped by a KMS-managed key |
| Keys | Stored in a KMS/HSM or equivalent secret manager; separate keys per environment and per data class; rotation schedule; documented break-glass recovery |
| Hashes | Content hashes with SHA-256 or stronger; audit chain with HMAC where feasible |
| Backups | Encrypted, access-restricted, restore-tested |

Never roll custom cryptography. Use vetted libraries.

## 6. Network and infrastructure

- Zoned network (edge / application / AI / data / management) per ARCHITECTURE.md §11.
- Default-deny firewall rules; **AI zone has no outbound internet**.
- WAF and rate limiting at the edge; admin interfaces not publicly exposed.
- Hardened, minimal container images; non-root; read-only filesystems where possible; image signing and vulnerability scanning.
- Infrastructure as code with peer review; drift detection.
- Time synchronisation and centralised, access-controlled log collection.

## 7. External AI services and data egress

**Rule:** identifiable patient information must **not** be sent to external AI services (hosted LLM APIs, third-party embedding, speech or OCR services, analytics with AI features) unless an approved legal and privacy framework exists.

An *approved framework* means, at minimum, all of: a completed DPIA; counsel's confirmation of lawful basis and cross-border transfer position; a signed data-processing agreement with the provider including no-training/no-retention terms; documented security assessment of the provider; clinical governance approval; recorded approval reference (decision-log entry); and patient/organisation notice where required.

Technical enforcement:

1. LLM gateway is the only component allowed to invoke models and is configured for local endpoints only.
2. Network egress deny from application and AI zones; DNS egress monitored.
3. A **PHI egress filter** in the gateway blocks requests whose destination is not on a local allow-list.
4. Configuration cannot enable an external provider without a non-empty approval-reference value that is audited, and the build fails in CI if any external AI SDK is imported outside a designated, disabled adapter.
5. Even with an approved framework, send the minimum necessary, pseudonymised where possible.
6. Automated tests attempt egress and assert failure (`tests/safety/egress_block`).

Operational telemetry, crash reporters, package mirrors and model downloads are also egress paths: they must be allow-listed and must not carry patient data. Models and packages are fetched during build into an internal registry, not at runtime.

## 8. Application security

- Follow OWASP ASVS (target Level 2) and OWASP Top 10 for web/API risks.
- Input validation by schema on every boundary; parameterised queries only; output encoding; strict CORS and CSP; CSRF protection for cookie-based sessions.
- Rate limiting, request size limits, idempotency keys.
- Dependency management: lockfiles, automated advisories, SBOM generation, licence checks, signed releases.
- Secrets: never in the repository, images or logs; secret scanning in pre-commit and CI; rotation on exposure.
- Error handling that never leaks PHI or internals.
- Static analysis, dependency audit, container scanning and DAST in CI.

## 9. Data lifecycle, retention and subject rights

| Topic | Requirement |
|---|---|
| Minimisation | Capture only fields required by the assessment pathway |
| Purpose limitation | Clinical documentation support; secondary use (research, model improvement) only under explicit approvals |
| Retention | Clinical records retained per the sponsoring institution's policy and law; audit logs retained longer; drafts that were never approved retained for a defined, short period then deleted or anonymised |
| Deletion | Documented procedures; cryptographic erasure via key destruction where appropriate; backups age out |
| Subject rights | Process to support access, correction and objection requests as required by applicable law (clinical records may be restricted by law; counsel to advise) |
| Training use | No fine-tuning or evaluation on real patient data without ethics approval, DPIA and de-identification validation |
| Cross-border | No transfer outside the approved jurisdiction without legal review |

## 10. Audit logging specification

Every event records: `event_id`, UTC timestamp, actor ID and role, action, resource type and ID, patient pseudonym ID (where relevant), outcome, source IP/device, request ID, details (no clinical content), `prev_hash`, `hash`.

Events that **must** be logged:

- Authentication, MFA, failures, session revocation, privilege changes, break-glass.
- Any read of patient content (including search results counts and exports).
- Create/update/supersede of observations; analysis runs.
- AI runs (model ID and digest, prompt version, KB snapshot, input/output hashes), verifier decisions, regenerations.
- Review actions: approve, edit, reject, co-sign, override, amendment.
- Configuration, rule, prompt, model or KB version changes.
- Kill-switch use and egress-block events.

Properties: insert-only (UPDATE/DELETE revoked and blocked by trigger), hash-chained, periodically anchored (e.g., chain head exported to a separate trusted store), verified by a scheduled job that alerts on failure, retained per policy, readable only by the `auditor` role and security tooling. Logs contain no clinical free text.

## 11. LLM-specific threats and mitigations

| Threat | Mitigation |
|---|---|
| Prompt injection via notes or imported data | Treat all patient-sourced text as data inside delimited blocks; instruction-hierarchy prompting; schema-constrained output; no tools or network for the drafting model; verifier checks; injection test corpus in CI |
| Sensitive information disclosure | Local inference; context limited to the current case; no cross-case memory; prompts and outputs logged as hashes, not text, by default |
| Insecure output handling | Outputs never executed or rendered as HTML unescaped; schema validation before storage |
| Excessive agency | Fixed workflow; no model-initiated actions; write access only via orchestrator after verification |
| Hallucinated references | Citations only by KB chunk ID; resolver and verifier; "no source found" behaviour |
| Training-data/model supply-chain risk | Models from reviewed sources only; digest pinning; scan weights/containers; internal registry; no runtime downloads |
| Model denial of service | Queue limits, token limits, timeouts, per-user quotas |
| Embedding/vector store leakage | Embeddings of KB only (public/licensed text), **not** patient notes, unless a later decision adds them under the same controls |
| Memorisation/cross-patient leakage | No fine-tuning on patient data; no shared conversational memory across cases |

## 12. Secure development lifecycle

- Branch protection, mandatory pull-request review, signed commits where practical.
- Threat modelling at each phase kickoff; security acceptance criteria in every phase exit gate.
- Pre-commit hooks: secret scanning, identifier-pattern scanning (names, phone, ID formats) for the synthetic-only rule.
- CI gates: unit/integration/safety tests, SAST, dependency and container scanning, licence check, migration check, `synthetic-only-check`.
- Environments: `dev`, `test`, `staging`, `prod` with separate credentials and data; production data never copied down.
- The repository `.gitignore` excludes data files, keys, model weights and environment files; any such file committed is treated as an incident.
- Developer workstations (including mobile/terminal-based workflows) must use full-device encryption, screen lock and no local copies of real patient data.

## 13. Incident response

1. Detect (alerts, user reports) → 2. Triage severity (privacy breach, safety incident, availability) → 3. Contain (kill switch, revoke credentials, isolate) → 4. Eradicate and recover → 5. Notify (regulators, institutions, individuals as required by law and agreement, within legal deadlines) → 6. Post-incident review and corrective actions → 7. Update hazard log and threat model.

Safety incidents (a harmful or near-miss clinical output) follow the same path with the clinical safety lead as co-owner. Tabletop exercises before Phase 7.

## 14. Compliance mapping (to be completed with counsel)

Create and maintain a register mapping each applicable legal and regulatory requirement to controls and evidence. Frameworks commonly consulted as references: national data-protection law of the deployment country, applicable health-information confidentiality rules, professional council guidance, and, as design references only, GDPR and HIPAA principles, ISO/IEC 27001 and 27701, ISO 14971 (risk management), IEC 62304 (software lifecycle) and IEC 82304-1 (health software). Inclusion of a framework here does not mean it legally applies.

## 15. Security requirements by phase

| Phase | Security deliverables |
|---|---|
| 0 | This document; threat model v0; data classification |
| 1 | Identity/clinical separation; RLS design tests; synthetic data only; secret and identifier scanning in CI |
| 2 | AuthN/MFA, RBAC, RLS enforced, encryption, KMS integration, audit chain and verifier, backup/restore test |
| 3 | LLM gateway with egress deny and tests; model registry with digests |
| 4 | KB integrity and licence register |
| 5 | Prompt-injection and leakage test suites passing |
| 6 | UI security review (CSP, session handling), pen test of API |
| 7 | DPIA, ethics approval, penetration test, incident-response exercise, go/no-go security sign-off |
