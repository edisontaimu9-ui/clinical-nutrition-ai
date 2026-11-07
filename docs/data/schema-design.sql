-- ============================================================================
-- Clinical Nutrition AI Agent — PostgreSQL schema DESIGN REFERENCE (Phase 0)
--
-- NOT A MIGRATION. Do not execute against any environment. It illustrates the
-- structure, constraints and security model described in ARCHITECTURE.md §8.
-- Executable, versioned migrations are created in Phase 1 under db/migrations/.
--
-- Contains no real data. All identifiers are placeholders.
-- Target: PostgreSQL 16+.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS iam;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS clinical;
CREATE SCHEMA IF NOT EXISTS kb;
CREATE SCHEMA IF NOT EXISTS ai;
CREATE SCHEMA IF NOT EXISTS review;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS eval;

-- ---------------------------------------------------------------------------
-- Enumerated types
-- ---------------------------------------------------------------------------
CREATE TYPE iam.role_name AS ENUM
  ('clinician', 'trainee', 'supervisor', 'clinical_safety_lead', 'admin', 'auditor', 'engineer');

CREATE TYPE clinical.element_status AS ENUM
  ('present', 'not_assessed', 'not_applicable', 'unknown_or_unreliable', 'declared_normal_by_clinician');

CREATE TYPE clinical.obs_source AS ENUM
  ('clinician_entry', 'import', 'proposed_from_note');

CREATE TYPE clinical.finding_kind AS ENUM
  ('gap', 'conflict', 'stale_value', 'red_flag', 'implausible_value');

CREATE TYPE clinical.criticality AS ENUM ('blocking', 'important', 'optional');

CREATE TYPE ai.draft_kind AS ENUM
  ('completeness_report', 'adime_draft', 'intervention_options');

CREATE TYPE ai.draft_state AS ENUM
  ('draft', 'under_review', 'pending_cosign', 'approved', 'rejected', 'expired', 'superseded', 'amended');

CREATE TYPE ai.adime_section AS ENUM
  ('assessment', 'diagnosis_candidate', 'intervention', 'monitoring_evaluation');

CREATE TYPE ai.uncertainty_flag AS ENUM
  ('supported', 'partially_supported', 'needs_clinician_input', 'conflicting_inputs');

CREATE TYPE review.decision_kind AS ENUM
  ('approve', 'approve_with_edits', 'reject', 'request_regeneration', 'cosign', 'return_to_reviewer');

-- ---------------------------------------------------------------------------
-- iam
-- ---------------------------------------------------------------------------
CREATE TABLE iam.app_user (
  user_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_subject text NOT NULL UNIQUE,         -- OIDC subject
  display_name   text NOT NULL,
  org_id         uuid NOT NULL,
  is_active      boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE iam.role_grant (
  user_id     uuid NOT NULL REFERENCES iam.app_user(user_id),
  role        iam.role_name NOT NULL,
  granted_by  uuid NOT NULL REFERENCES iam.app_user(user_id),
  granted_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz,
  PRIMARY KEY (user_id, role)
);

CREATE TABLE iam.cosign_relationship (
  trainee_id    uuid NOT NULL REFERENCES iam.app_user(user_id),
  supervisor_id uuid NOT NULL REFERENCES iam.app_user(user_id),
  active        boolean NOT NULL DEFAULT true,
  PRIMARY KEY (trainee_id, supervisor_id),
  CHECK (trainee_id <> supervisor_id)
);

-- ---------------------------------------------------------------------------
-- identity vault (direct identifiers live ONLY here)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.patient_identity (
  patient_id           uuid PRIMARY KEY,         -- same ID as clinical.patient
  org_id               uuid NOT NULL,
  encrypted_identifiers bytea NOT NULL,          -- envelope-encrypted blob (names, contacts, national/hospital IDs, DOB)
  key_id               text NOT NULL,            -- KMS key reference used for the data key
  created_at           timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- clinical (pseudonymised)
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.patient (
  patient_id    uuid PRIMARY KEY,
  org_id        uuid NOT NULL,
  pseudonym     text NOT NULL UNIQUE,            -- opaque, non-derivable from identifiers
  sex_at_birth  text CHECK (sex_at_birth IN ('female', 'male', 'unknown')),
  is_synthetic  boolean NOT NULL DEFAULT true,   -- true throughout development (D-009)
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.encounter (
  encounter_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id        uuid NOT NULL REFERENCES clinical.patient(patient_id),
  org_id            uuid NOT NULL,
  setting           text NOT NULL CHECK (setting IN ('inpatient', 'outpatient', 'community')),
  pathway           text NOT NULL,               -- e.g. 'adult_general' (in-scope pathways only)
  age_months_at_encounter integer NOT NULL CHECK (age_months_at_encounter >= 0),
  started_at        timestamptz NOT NULL DEFAULT now(),
  created_by        uuid NOT NULL REFERENCES iam.app_user(user_id)
);

CREATE TABLE clinical.care_relationship (
  user_id     uuid NOT NULL REFERENCES iam.app_user(user_id),
  patient_id  uuid NOT NULL REFERENCES clinical.patient(patient_id),
  starts_at   timestamptz NOT NULL DEFAULT now(),
  ends_at     timestamptz,
  PRIMARY KEY (user_id, patient_id, starts_at)
);

CREATE TABLE clinical.data_element_def (
  element_code     text PRIMARY KEY,             -- e.g. 'anthro.weight'
  domain           text NOT NULL,                -- anthropometry | biochemical | clinical | intake | functional | medication | history | social
  label            text NOT NULL,
  value_type       text NOT NULL CHECK (value_type IN ('numeric', 'text', 'boolean', 'coded')),
  canonical_unit   text,
  plausible_min    numeric,
  plausible_max    numeric,
  staleness_days   integer,                      -- rule-defined validity window
  version          integer NOT NULL DEFAULT 1,
  CHECK (plausible_min IS NULL OR plausible_max IS NULL OR plausible_min <= plausible_max)
);

-- Core safety structure: explicit status; values only when status = 'present'.
CREATE TABLE clinical.observation (
  observation_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id     uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  element_code     text NOT NULL REFERENCES clinical.data_element_def(element_code),
  status           clinical.element_status NOT NULL DEFAULT 'not_assessed',
  value_numeric    numeric,
  value_text       text,
  value_bool       boolean,
  unit_entered     text,
  value_canonical  numeric,                      -- converted by deterministic code, never by the LLM
  observed_at      timestamptz,
  source           clinical.obs_source NOT NULL DEFAULT 'clinician_entry',
  confirmed        boolean NOT NULL DEFAULT false,  -- proposals from notes stay false until a clinician confirms
  recorded_by      uuid NOT NULL REFERENCES iam.app_user(user_id),
  recorded_at      timestamptz NOT NULL DEFAULT now(),
  supersedes_id    uuid REFERENCES clinical.observation(observation_id),
  declared_by      uuid REFERENCES iam.app_user(user_id),  -- required for declared_normal_by_clinician
  confirmed_by     uuid REFERENCES iam.app_user(user_id),  -- clinician who confirmed a proposed extraction
  -- A value may exist only when status = 'present'.
  CONSTRAINT value_only_when_present CHECK (
    (status = 'present' AND (value_numeric IS NOT NULL OR value_text IS NOT NULL OR value_bool IS NOT NULL))
    OR
    (status <> 'present' AND value_numeric IS NULL AND value_text IS NULL AND value_bool IS NULL AND value_canonical IS NULL)
  ),
  CONSTRAINT declared_normal_needs_clinician CHECK (
    status <> 'declared_normal_by_clinician' OR declared_by IS NOT NULL
  ),
  -- Extractions proposed from free text count as confirmed only when a clinician confirmed them.
  CONSTRAINT proposals_need_clinician_confirmation CHECK (
    source <> 'proposed_from_note' OR confirmed = false OR confirmed_by IS NOT NULL
  )
);
CREATE INDEX observation_encounter_idx ON clinical.observation (encounter_id, element_code, recorded_at DESC);

CREATE TABLE clinical.calculation (
  calculation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id    uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  formula_id      text NOT NULL,                 -- e.g. 'bmi', 'pct_weight_change'
  formula_version text NOT NULL,
  reference_standard text,
  input_observation_ids uuid[] NOT NULL,
  result_numeric  numeric,
  result_text     text,
  assumptions     jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.finding (
  finding_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id   uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  kind           clinical.finding_kind NOT NULL,
  rule_id        text NOT NULL,
  rule_version   text NOT NULL,
  criticality    clinical.criticality NOT NULL,
  element_codes  text[] NOT NULL DEFAULT '{}',
  observation_ids uuid[] NOT NULL DEFAULT '{}',
  message        text NOT NULL,                  -- what is missing/conflicting and why it matters
  resolved_by    uuid REFERENCES iam.app_user(user_id),
  resolved_at    timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- kb (curated knowledge base; read-only at runtime)
-- ---------------------------------------------------------------------------
CREATE TABLE kb.source (
  source_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title          text NOT NULL,
  publisher      text NOT NULL,
  edition        text,
  version_label  text NOT NULL,
  licence        text NOT NULL,                  -- must be confirmed before ingestion (D-013)
  licence_verified boolean NOT NULL DEFAULT false,
  url            text,
  content_hash   bytea NOT NULL,
  review_by      date,
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'retired'))
);

CREATE TABLE kb.chunk (
  chunk_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id    uuid NOT NULL REFERENCES kb.source(source_id),
  locator      text NOT NULL,                    -- section/page reference
  applicability jsonb NOT NULL DEFAULT '{}'::jsonb,  -- population, setting, conditions, contraindications
  text         text NOT NULL,
  embedding    vector(1024),                     -- dimension set by chosen embedding model
  chunk_hash   bytea NOT NULL
);

CREATE TABLE kb.snapshot (
  snapshot_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label        text NOT NULL UNIQUE,
  created_at   timestamptz NOT NULL DEFAULT now(),
  approved_by  uuid REFERENCES iam.app_user(user_id)
);

CREATE TABLE kb.snapshot_chunk (
  snapshot_id  uuid NOT NULL REFERENCES kb.snapshot(snapshot_id),
  chunk_id     uuid NOT NULL REFERENCES kb.chunk(chunk_id),
  PRIMARY KEY (snapshot_id, chunk_id)
);

-- ---------------------------------------------------------------------------
-- ai (runs, drafts, evidence)
-- ---------------------------------------------------------------------------
CREATE TABLE ai.run (
  run_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id      uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  requested_by      uuid NOT NULL REFERENCES iam.app_user(user_id),
  workflow_version  text NOT NULL,
  rules_version     text NOT NULL,
  model_id          text,
  model_digest      text,                        -- SHA-256 of weights/artifact
  prompt_template_version text,
  kb_snapshot_id    uuid REFERENCES kb.snapshot(snapshot_id),
  input_hash        bytea NOT NULL,
  output_hash       bytea,
  status            text NOT NULL CHECK (status IN ('running', 'completed', 'failed_safe', 'verifier_rejected')),
  started_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz
);

CREATE TABLE ai.draft (
  draft_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id   uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  run_id         uuid NOT NULL REFERENCES ai.run(run_id),
  kind           ai.draft_kind NOT NULL,
  version        integer NOT NULL DEFAULT 1,
  state          ai.draft_state NOT NULL DEFAULT 'draft',
  supersedes_id  uuid REFERENCES ai.draft(draft_id),
  content_hash   bytea NOT NULL,                 -- hash of the exact reviewable content
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (encounter_id, kind, version)
);

CREATE TABLE ai.draft_item (
  item_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  draft_id       uuid NOT NULL REFERENCES ai.draft(draft_id),
  section        ai.adime_section NOT NULL,
  position       integer NOT NULL,
  content        jsonb NOT NULL,                 -- structured statement; numbers injected from DB, not generated
  flag           ai.uncertainty_flag NOT NULL,
  flag_reason    text NOT NULL,
  is_candidate_diagnosis boolean NOT NULL DEFAULT false,
  UNIQUE (draft_id, section, position),
  -- No field exists for medical diagnoses or orders; candidates must be marked.
  CONSTRAINT candidate_only_in_diagnosis_section CHECK (
    (section = 'diagnosis_candidate') = is_candidate_diagnosis
  )
);

-- Every item must be evidence-linked, or flagged needs_clinician_input (enforced by constraint trigger in Phase 1).
CREATE TABLE ai.item_evidence (
  item_id         uuid NOT NULL REFERENCES ai.draft_item(item_id),
  observation_id  uuid REFERENCES clinical.observation(observation_id),
  calculation_id  uuid REFERENCES clinical.calculation(calculation_id),
  chunk_id        uuid REFERENCES kb.chunk(chunk_id),
  CHECK (observation_id IS NOT NULL OR calculation_id IS NOT NULL OR chunk_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- review (human approval)
-- ---------------------------------------------------------------------------
CREATE TABLE review.decision (
  decision_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  draft_id         uuid NOT NULL REFERENCES ai.draft(draft_id),
  reviewer_id      uuid NOT NULL REFERENCES iam.app_user(user_id),
  decision         review.decision_kind NOT NULL,
  reviewed_content_hash bytea NOT NULL,          -- must equal ai.draft.content_hash at decision time
  reason           text,
  override_blocking_gaps boolean NOT NULL DEFAULT false,
  override_reason  text,
  decided_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT override_blocking_gaps OR override_reason IS NOT NULL),
  CHECK (decision NOT IN ('reject', 'return_to_reviewer') OR reason IS NOT NULL)
);

CREATE TABLE review.item_edit (
  edit_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id      uuid NOT NULL REFERENCES ai.draft_item(item_id),
  reviewer_id  uuid NOT NULL REFERENCES iam.app_user(user_id),
  before_content jsonb NOT NULL,
  after_content  jsonb NOT NULL,
  reason       text,
  edited_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- audit (append-only, hash-chained)
-- ---------------------------------------------------------------------------
CREATE TABLE audit.event (
  event_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at   timestamptz NOT NULL DEFAULT now(),
  actor_id      uuid,
  actor_role    text,
  action        text NOT NULL,
  resource_type text NOT NULL,
  resource_id   text,
  patient_id    uuid,
  outcome       text NOT NULL,
  source_ip     inet,
  request_id    text,
  details       jsonb NOT NULL DEFAULT '{}'::jsonb,  -- never clinical free text
  prev_hash     bytea,
  hash          bytea NOT NULL
);

CREATE FUNCTION audit.block_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit.event is append-only';
END;
$$;

CREATE TRIGGER audit_event_no_update_delete
  BEFORE UPDATE OR DELETE ON audit.event
  FOR EACH ROW EXECUTE FUNCTION audit.block_mutation();

-- ---------------------------------------------------------------------------
-- eval (synthetic evaluation results)
-- ---------------------------------------------------------------------------
CREATE TABLE eval.run (
  eval_run_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id     text NOT NULL,
  model_digest text NOT NULL,
  quantisation text,
  dataset_version text NOT NULL,
  prompt_template_version text NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval.case_result (
  eval_run_id  uuid NOT NULL REFERENCES eval.run(eval_run_id),
  case_id      text NOT NULL,
  metric       text NOT NULL,
  value_numeric numeric,
  critical_failure boolean NOT NULL DEFAULT false,
  notes        text,
  PRIMARY KEY (eval_run_id, case_id, metric)
);

-- ---------------------------------------------------------------------------
-- Privileges and row-level security (illustrative)
-- ---------------------------------------------------------------------------
-- Separate roles per service; none is a superuser or table owner.
-- CREATE ROLE svc_case NOLOGIN; CREATE ROLE svc_rules NOLOGIN; CREATE ROLE svc_orchestrator NOLOGIN;
-- CREATE ROLE svc_review NOLOGIN; CREATE ROLE svc_audit NOLOGIN; CREATE ROLE svc_kb_readonly NOLOGIN;

REVOKE UPDATE, DELETE, TRUNCATE ON audit.event FROM PUBLIC;
-- GRANT INSERT ON audit.event TO svc_audit;  -- insert-only

ALTER TABLE clinical.patient     ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.patient     FORCE  ROW LEVEL SECURITY;
ALTER TABLE clinical.encounter   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.encounter   FORCE  ROW LEVEL SECURITY;
ALTER TABLE clinical.observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.observation FORCE  ROW LEVEL SECURITY;
ALTER TABLE ai.draft             ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai.draft             FORCE  ROW LEVEL SECURITY;

-- The application sets per-request context: SET LOCAL app.user_id = '<uuid>'; SET LOCAL app.org_id = '<uuid>';
CREATE POLICY patient_by_care_relationship ON clinical.patient
  USING (
    org_id = current_setting('app.org_id')::uuid
    AND EXISTS (
      SELECT 1 FROM clinical.care_relationship cr
      WHERE cr.patient_id = clinical.patient.patient_id
        AND cr.user_id = current_setting('app.user_id')::uuid
        AND cr.starts_at <= now()
        AND (cr.ends_at IS NULL OR cr.ends_at > now())
    )
  );

CREATE POLICY encounter_by_care_relationship ON clinical.encounter
  USING (
    org_id = current_setting('app.org_id')::uuid
    AND EXISTS (
      SELECT 1 FROM clinical.care_relationship cr
      WHERE cr.patient_id = clinical.encounter.patient_id
        AND cr.user_id = current_setting('app.user_id')::uuid
        AND cr.starts_at <= now()
        AND (cr.ends_at IS NULL OR cr.ends_at > now())
    )
  );

CREATE POLICY observation_by_encounter ON clinical.observation
  USING (
    EXISTS (SELECT 1 FROM clinical.encounter e WHERE e.encounter_id = clinical.observation.encounter_id)
  );

CREATE POLICY draft_by_encounter ON ai.draft
  USING (
    EXISTS (SELECT 1 FROM clinical.encounter e WHERE e.encounter_id = ai.draft.encounter_id)
  );

-- Phase 1+ additions (not shown): constraint trigger for evidence-or-flag on ai.draft_item;
-- trigger blocking edits to approved drafts; trigger verifying review.decision hash equals ai.draft.content_hash;
-- audit hash-chain computation function and verifier; remaining RLS policies and role grants.
