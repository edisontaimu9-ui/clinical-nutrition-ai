-- 0001_foundation: schemas, enumerated types, users, identity vault, instance configuration and governance roles.
-- Phase 1. Synthetic data only. Design reference: docs/data/schema-design.sql.
-- Note: ai, review, kb, audit and eval schemas are created in later phases (ROADMAP.md).

CREATE SCHEMA iam;
CREATE SCHEMA identity;
CREATE SCHEMA clinical;
CREATE SCHEMA ops;

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

CREATE TYPE clinical.review_status AS ENUM ('draft', 'author_approved', 'independently_reviewed');

-- ---------------------------------------------------------------------------
-- iam (authentication and authorisation arrive in Phase 2; tables exist so governance can reference users)
-- ---------------------------------------------------------------------------
CREATE TABLE iam.app_user (
  user_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  external_subject text NOT NULL UNIQUE,
  display_name     text NOT NULL,
  org_id           uuid NOT NULL,
  is_active        boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE iam.role_grant (
  user_id    uuid NOT NULL REFERENCES iam.app_user(user_id),
  role       iam.role_name NOT NULL,
  granted_by uuid NOT NULL REFERENCES iam.app_user(user_id),
  granted_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz,
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
-- identity vault: direct identifiers live ONLY here (encryption arrives in Phase 2; unused while data is synthetic)
-- ---------------------------------------------------------------------------
CREATE TABLE identity.patient_identity (
  patient_id            uuid PRIMARY KEY,
  org_id                uuid NOT NULL,
  encrypted_identifiers bytea NOT NULL,
  key_id                text NOT NULL,
  created_at            timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- ops: instance configuration and governance roles (D-017, D-019, D-020)
-- ---------------------------------------------------------------------------
CREATE TABLE ops.instance_config (
  singleton  boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  data_class text NOT NULL CHECK (data_class IN ('synthetic', 'real')),
  mode       text NOT NULL CHECK (mode IN ('development', 'shadow', 'clinical')),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- Real data is possible only in shadow or clinical mode.
  CHECK (data_class = 'synthetic' OR mode IN ('shadow', 'clinical'))
);
INSERT INTO ops.instance_config (data_class, mode) VALUES ('synthetic', 'development');

CREATE TABLE ops.governance_role (
  role_key         text PRIMARY KEY CHECK (role_key IN (
                     'product_clinical_requirements_lead',
                     'independent_clinical_safety_reviewer',
                     'privacy_security_lead')),
  status           text NOT NULL DEFAULT 'vacant' CHECK (status IN ('assigned', 'vacant')),
  assigned_user_id uuid REFERENCES iam.app_user(user_id),
  assigned_at      timestamptz,
  decision_ref     text,
  CHECK ((status = 'assigned') = (assigned_user_id IS NOT NULL)),
  -- An appointment must be traceable to a decision-log entry.
  CHECK (status = 'vacant' OR decision_ref IS NOT NULL)
);
INSERT INTO ops.governance_role (role_key) VALUES
  ('product_clinical_requirements_lead'),
  ('independent_clinical_safety_reviewer'),
  ('privacy_security_lead');
