-- 0002_clinical_core: pathway registry, reference standards, patients, encounters, element catalogue,
-- observations (five-state model), calculations, findings, rules and rule reviews.
-- Phase 1. Synthetic data only.

-- ---------------------------------------------------------------------------
-- Pathways and reference standards (population-ready model; D-018)
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.pathway (
  pathway_code          text PRIMARY KEY,
  label                 text NOT NULL,
  status                text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'active', 'retired')),
  min_age_months        integer NOT NULL CHECK (min_age_months >= 0),
  max_age_months        integer,
  activated_by_decision text,
  CHECK (max_age_months IS NULL OR max_age_months >= min_age_months),
  CHECK (status <> 'active' OR activated_by_decision IS NOT NULL)
);

CREATE TABLE clinical.reference_standard (
  standard_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  publisher     text NOT NULL,
  version_label text NOT NULL,
  population    text NOT NULL,
  source_url    text,
  licence       text,
  content_hash  bytea,
  status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'retired')),
  UNIQUE (name, version_label)
);

CREATE TABLE clinical.reference_lms (
  standard_id uuid NOT NULL REFERENCES clinical.reference_standard(standard_id),
  sex         text NOT NULL CHECK (sex IN ('female', 'male')),
  measure     text NOT NULL,
  index_value numeric NOT NULL,
  l           numeric NOT NULL,
  m           numeric NOT NULL,
  s           numeric NOT NULL,
  PRIMARY KEY (standard_id, sex, measure, index_value)
);

-- ---------------------------------------------------------------------------
-- Patients (pseudonymised) and encounters
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.patient (
  patient_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL,
  pseudonym    text NOT NULL UNIQUE,
  sex_at_birth text CHECK (sex_at_birth IN ('female', 'male', 'unknown')),
  is_synthetic boolean NOT NULL,                -- must match ops.instance_config.data_class (trigger)
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.encounter (
  encounter_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  patient_id              uuid NOT NULL REFERENCES clinical.patient(patient_id),
  org_id                  uuid NOT NULL,
  setting                 text NOT NULL CHECK (setting IN ('inpatient', 'outpatient', 'community')),
  pathway                 text NOT NULL REFERENCES clinical.pathway(pathway_code),
  age_months_at_encounter integer NOT NULL CHECK (age_months_at_encounter >= 0),
  started_at              timestamptz NOT NULL DEFAULT now(),
  created_by              uuid NOT NULL REFERENCES iam.app_user(user_id)
);

CREATE TABLE clinical.care_relationship (
  user_id    uuid NOT NULL REFERENCES iam.app_user(user_id),
  patient_id uuid NOT NULL REFERENCES clinical.patient(patient_id),
  starts_at  timestamptz NOT NULL DEFAULT now(),
  ends_at    timestamptz,
  PRIMARY KEY (user_id, patient_id, starts_at)
);

-- ---------------------------------------------------------------------------
-- Data-element catalogue
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.data_element_def (
  element_code       text PRIMARY KEY,
  domain             text NOT NULL CHECK (domain IN
                       ('anthropometry', 'biochemical', 'clinical', 'intake', 'functional', 'medication', 'history', 'social')),
  label              text NOT NULL,
  value_type         text NOT NULL CHECK (value_type IN ('numeric', 'text', 'boolean', 'coded')),
  canonical_unit     text,
  plausible_min      numeric,
  plausible_max      numeric,
  staleness_days     integer CHECK (staleness_days IS NULL OR staleness_days > 0),
  conflict_tolerance numeric CHECK (conflict_tolerance IS NULL OR conflict_tolerance >= 0),
  allowed_values     text[],
  undated_ok         boolean NOT NULL DEFAULT false,
  enabled            boolean NOT NULL DEFAULT true,
  pathways           text[] NOT NULL DEFAULT '{}',
  min_age_months     integer,
  max_age_months     integer,
  version            integer NOT NULL DEFAULT 1,
  CHECK (plausible_min IS NULL OR plausible_max IS NULL OR plausible_min <= plausible_max),
  CHECK ((value_type = 'coded') = (allowed_values IS NOT NULL)),
  CHECK (value_type <> 'numeric' OR canonical_unit IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- Observations: the core safety structure (D-003). Append-only; values only when status = 'present'.
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.observation (
  observation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id    uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  element_code    text NOT NULL REFERENCES clinical.data_element_def(element_code),
  status          clinical.element_status NOT NULL DEFAULT 'not_assessed',
  value_numeric   numeric,
  value_text      text,
  value_bool      boolean,
  unit_entered    text,
  value_canonical numeric,
  observed_at     timestamptz,
  source          clinical.obs_source NOT NULL DEFAULT 'clinician_entry',
  confirmed       boolean NOT NULL DEFAULT false,
  recorded_by     uuid NOT NULL REFERENCES iam.app_user(user_id),
  recorded_at     timestamptz NOT NULL DEFAULT now(),
  supersedes_id   uuid REFERENCES clinical.observation(observation_id),
  declared_by     uuid REFERENCES iam.app_user(user_id),
  confirmed_by    uuid REFERENCES iam.app_user(user_id),
  CONSTRAINT value_only_when_present CHECK (
    (status = 'present' AND (value_numeric IS NOT NULL OR value_text IS NOT NULL OR value_bool IS NOT NULL))
    OR
    (status <> 'present' AND value_numeric IS NULL AND value_text IS NULL AND value_bool IS NULL
       AND value_canonical IS NULL AND unit_entered IS NULL)
  ),
  CONSTRAINT declared_normal_needs_clinician CHECK (
    status <> 'declared_normal_by_clinician' OR declared_by IS NOT NULL
  ),
  CONSTRAINT proposals_need_clinician_confirmation CHECK (
    source <> 'proposed_from_note' OR confirmed = false OR confirmed_by IS NOT NULL
  )
);
CREATE INDEX observation_encounter_idx ON clinical.observation (encounter_id, element_code, recorded_at DESC);
CREATE UNIQUE INDEX observation_supersedes_once ON clinical.observation (supersedes_id) WHERE supersedes_id IS NOT NULL;

CREATE TABLE clinical.calculation (
  calculation_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id          uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  formula_id            text NOT NULL,
  formula_version       text NOT NULL,
  reference_standard_id uuid REFERENCES clinical.reference_standard(standard_id),
  input_observation_ids uuid[] NOT NULL CHECK (cardinality(input_observation_ids) > 0),
  result_numeric        numeric,
  result_text           text,
  assumptions           jsonb NOT NULL DEFAULT '{}'::jsonb,
  computed_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.finding (
  finding_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  encounter_id    uuid NOT NULL REFERENCES clinical.encounter(encounter_id),
  kind            clinical.finding_kind NOT NULL,
  rule_id         text NOT NULL,
  rule_version    text NOT NULL,
  criticality     clinical.criticality NOT NULL,
  element_codes   text[] NOT NULL DEFAULT '{}',
  observation_ids uuid[] NOT NULL DEFAULT '{}',
  message         text NOT NULL,
  resolved_by     uuid REFERENCES iam.app_user(user_id),
  resolved_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK ((resolved_by IS NULL) = (resolved_at IS NULL))
);

-- ---------------------------------------------------------------------------
-- Clinical rules and review status (D-019). Rules are immutable once written; a change is a new version.
-- ---------------------------------------------------------------------------
CREATE TABLE clinical.rule (
  rule_id       text NOT NULL,
  version       text NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('completeness', 'conflict', 'staleness', 'red_flag', 'scope_exclusion', 'template')),
  pathway_code  text NOT NULL REFERENCES clinical.pathway(pathway_code),
  definition    jsonb NOT NULL,
  author_id     uuid NOT NULL REFERENCES iam.app_user(user_id),
  review_status clinical.review_status NOT NULL DEFAULT 'draft',
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE clinical.rule_review (
  review_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_id     text NOT NULL,
  version     text NOT NULL,
  level       clinical.review_status NOT NULL CHECK (level IN ('author_approved', 'independently_reviewed')),
  reviewer_id uuid NOT NULL REFERENCES iam.app_user(user_id),
  outcome     text NOT NULL CHECK (outcome IN ('approved', 'changes_requested')),
  comment     text,
  reviewed_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (rule_id, version) REFERENCES clinical.rule(rule_id, version)
);
