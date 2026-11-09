# Threat model v1 (Phase 2)

Version 0.1 · Status: **Draft for review** · Scope: the deterministic core, the PostgreSQL schema and the security package as built in Phases 1 and 2. LLM, API, UI and deployment threats are added at their phases (SECURITY.md section 11).

Method: assets, trust boundaries, then threats by category (spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege). For each threat: the control that exists **now**, how it is tested, and what remains open. "Open" means not mitigated today.

## Assets

| Asset | Why it matters |
|---|---|
| Patient clinical content (observations, findings, calculations) | Privacy; clinical safety if altered |
| Direct identifiers (identity vault) | Re-identification harm |
| Audit log | Accountability; evidence in an incident |
| Rules, catalogue and review status | Clinical safety; a falsely "reviewed" rule is a safety failure |
| Key material | Decrypts identifiers and backups |
| Backups | A complete copy of everything |

## Trust boundaries

1. User or client to application (not built yet: Phase 6).
2. Application to database: the application connects as a non-owner login role and acts through least-privilege roles with row-level security.
3. Database owner (migration role) to everyone else: the owner can bypass row-level security and is therefore never used by the application.
4. Application to key service and backup storage.

## Threats and controls

| ID | Threat | Control now | Evidence | Open |
|---|---|---|---|---|
| S1 | A stolen or forged token is accepted | Asymmetric signature allow-list (no `none`, no HMAC), issuer, audience, expiry and 1 h maximum lifetime checks | `test_security_auth_permissions.py` | Key distribution, revocation lists and the identity provider itself are not built |
| S2 | Sign-in without a second factor | Verifier requires an MFA claim | `test_a_password_only_token_is_refused` | Depends on the identity provider issuing honest `amr` claims; to be confirmed when it is chosen |
| S3 | A client claims a different role or organisation | Roles come from the database; the organisation is looked up from the active user and never from the client or token | `test_roles_in_the_token_are_ignored...`, `test_a_client_supplied_organisation_changes_nothing` | |
| T1 | Clinician reads or writes another clinician's patient, or another facility's | Row-level security through care relationships; organisation match | `test_db_security_rls.py` (30 tests) | Policies cover current tables only; a test fails if a new table with a patient or encounter key lacks row security |
| T2 | Observations edited or deleted after entry | Append-only triggers, no UPDATE or DELETE privilege for app roles | `test_observations_stay_append_only_for_app_roles` | Owner can still disable triggers (see E3) |
| T3 | Audit log edited, deleted, reordered or forged | Append-only triggers, hash chain, independent Python verifier, signed external anchors | `test_db_audit_chain.py` (22 tests) | An owner who rewrites history and recomputes all hashes is caught only by an anchor held outside the database; no scheduled anchor export exists yet |
| T4 | Stored identifiers or notes copied to another record | Ciphertext bound to organisation and patient | `test_a_record_copied_to_another_patient_will_not_decrypt` | |
| T5 | Backup tampered with or truncated | Chunked authenticated encryption detects flips, reordering, truncation and a wrong key | `test_security_crypto.py` | Backup storage location and its access control are not defined |
| R1 | A user denies an action | Actor taken from the session context, never from the caller; every data change and permission change is logged | `test_the_caller_cannot_choose_the_actor_or_write_directly` | Reads and exports are logged only if the application calls `record_event` (no API yet) |
| I1 | Admin, auditor or engineer reads patient content | No grants or policies for those roles on patient tables | `test_admin_has_no_access_to_patient_content`, `test_auditor_reads_audit_metadata_but_not_patient_content` | Engineers use the owner role in development; production access is not designed yet |
| I2 | Clinical text leaks through the audit log | Details are flat, short, and key-filtered; stored values never copied | `test_the_audit_log_never_stores_clinical_values`, `test_clinical_content_cannot_be_smuggled_into_details` | Key deny-list is a backstop, not proof; code review must keep details non-clinical |
| I3 | Real data enters a development instance | Data-class guard in the database and the engine; scanners | Phase 1 tests | |
| I4 | Key material in the repository or logs | Secret scanner in CI; key files must be mode 600; error messages never echo tokens or keys | `test_scanners.py`, `test_auth_errors_never_echo_the_token` | No managed key service yet |
| D1 | Audit lock contention slows writes | One advisory lock serialises audit writes | Performance sanity test only | Throughput under load not measured |
| E1 | A support person escalates to broad patient access | Break-glass: second person, supervisor or safety lead, same organisation, 1 to 24 hours, read only, justification, logged | `test_break_glass_rules` and related | No alert on break-glass use yet; post-hoc review process not defined |
| E2 | An admin grants themselves roles or appoints themselves reviewer | Policies block self-grant and self-appointment; governance triggers enforce independence | `test_admin_cannot_grant_roles_to_themselves...`, `test_admin_cannot_appoint_themselves...` | A second admin colluding is not prevented |
| E3 | The database owner or a superuser bypasses everything | Application never uses the owner role; startup refuses privileged runtime roles in clinical mode | `test_each_clinical_requirement_is_enforced_on_its_own` | Owner credentials need separate custody and monitoring (deployment) |
| E4 | An unreviewed rule is used clinically | Start-up refuses clinical mode unless the reviewer is assigned and rules are independently reviewed; review status changes only by trigger | `test_db_security_services.py`, Phase 1 governance tests | No service calls the check yet |
| E5 | SQL injection through the session role | Role names come from an allow-list, never from input | `test_bind_refuses_unknown_roles_and_autocommit` | |

## Not yet modelled

The API surface, the user interface, deployment and network layout, the LLM gateway and prompt-injection (Phase 5), the knowledge base supply chain (Phase 4), mobile devices, and physical security. Each is added at its phase kickoff.

## Review status

Written by engineering. **No independent security review has been done.** The Phase 2 exit criterion "security review of auth and data layer completed" stays open until someone other than the author reviews this document and the code it covers (owner, the privacy and security lead when appointed, or an external reviewer).
