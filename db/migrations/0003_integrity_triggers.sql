-- 0003_integrity_triggers: database-level enforcement of the safety rules that must not depend on application code.
-- Every violation raises check_violation (SQLSTATE 23514) with a stable message prefix used by tests:
--   CNAI-POP    population / pathway guard           (FR-POP-02)
--   CNAI-STATE  data-element state and type guard    (FR-STATE-01/02)
--   CNAI-HIST   append-only history                  (FR-INT-05)
--   CNAI-GOV    governance and review independence   (FR-GOV-01/02, D-019)
--   CNAI-DATA   synthetic / real data-class guard    (D-009, D-020)

-- ---------------------------------------------------------------------------
-- Data-class guard (D-020): patients must match the instance data class; flag and class are immutable
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.guard_patient() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cls text;
BEGIN
  SELECT data_class INTO cls FROM ops.instance_config;
  IF TG_OP = 'INSERT' THEN
    IF NEW.is_synthetic IS DISTINCT FROM (cls = 'synthetic') THEN
      RAISE EXCEPTION 'CNAI-DATA: patient is_synthetic=% does not match instance data class %', NEW.is_synthetic, cls
        USING ERRCODE = '23514';
    END IF;
  ELSE
    IF NEW.is_synthetic IS DISTINCT FROM OLD.is_synthetic THEN
      RAISE EXCEPTION 'CNAI-DATA: is_synthetic is immutable' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER patient_guard BEFORE INSERT OR UPDATE ON clinical.patient
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_patient();

CREATE FUNCTION ops.guard_instance_config() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'CNAI-DATA: instance configuration cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF NEW.data_class IS DISTINCT FROM OLD.data_class AND EXISTS (SELECT 1 FROM clinical.patient) THEN
    RAISE EXCEPTION 'CNAI-DATA: data class cannot change while patient records exist' USING ERRCODE = '23514';
  END IF;
  NEW.updated_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER instance_config_guard BEFORE UPDATE OR DELETE ON ops.instance_config
  FOR EACH ROW EXECUTE FUNCTION ops.guard_instance_config();

-- ---------------------------------------------------------------------------
-- Pathway guard (FR-POP-02): encounters only for active pathways and ages inside the band
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.guard_encounter() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p clinical.pathway%ROWTYPE;
BEGIN
  SELECT * INTO p FROM clinical.pathway WHERE pathway_code = NEW.pathway;
  IF p.status <> 'active' THEN
    RAISE EXCEPTION 'CNAI-POP: pathway % is not active (status: %)', NEW.pathway, p.status USING ERRCODE = '23514';
  END IF;
  IF NEW.age_months_at_encounter < p.min_age_months
     OR (p.max_age_months IS NOT NULL AND NEW.age_months_at_encounter > p.max_age_months) THEN
    RAISE EXCEPTION 'CNAI-POP: age % months is outside the % band (% to % months)',
      NEW.age_months_at_encounter, NEW.pathway, p.min_age_months, COALESCE(p.max_age_months::text, 'no upper bound')
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER encounter_guard BEFORE INSERT OR UPDATE ON clinical.encounter
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_encounter();

-- ---------------------------------------------------------------------------
-- Observation guard (FR-STATE-01/02, FR-POP-03): element enabled and applicable, value type matches element,
-- numeric values carry their entered unit and a canonical value, coded values come from the allowed list
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.guard_observation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d clinical.data_element_def%ROWTYPE; e clinical.encounter%ROWTYPE; s clinical.observation%ROWTYPE;
BEGIN
  SELECT * INTO d FROM clinical.data_element_def WHERE element_code = NEW.element_code;
  SELECT * INTO e FROM clinical.encounter WHERE encounter_id = NEW.encounter_id;

  IF NOT d.enabled THEN
    RAISE EXCEPTION 'CNAI-POP: data element % is disabled (its pathway is not active)', NEW.element_code USING ERRCODE = '23514';
  END IF;
  IF cardinality(d.pathways) > 0 AND NOT (e.pathway = ANY (d.pathways)) THEN
    RAISE EXCEPTION 'CNAI-POP: data element % does not apply to pathway %', NEW.element_code, e.pathway USING ERRCODE = '23514';
  END IF;
  IF (d.min_age_months IS NOT NULL AND e.age_months_at_encounter < d.min_age_months)
     OR (d.max_age_months IS NOT NULL AND e.age_months_at_encounter > d.max_age_months) THEN
    RAISE EXCEPTION 'CNAI-POP: data element % does not apply at age % months', NEW.element_code, e.age_months_at_encounter
      USING ERRCODE = '23514';
  END IF;

  IF NEW.status = 'present' THEN
    IF d.value_type = 'numeric' THEN
      IF NEW.value_numeric IS NULL OR NEW.value_text IS NOT NULL OR NEW.value_bool IS NOT NULL THEN
        RAISE EXCEPTION 'CNAI-STATE: % requires a numeric value only', NEW.element_code USING ERRCODE = '23514';
      END IF;
      IF NEW.unit_entered IS NULL OR NEW.value_canonical IS NULL THEN
        RAISE EXCEPTION 'CNAI-STATE: numeric value for % requires unit_entered and a canonical value (no silent unit assumption)',
          NEW.element_code USING ERRCODE = '23514';
      END IF;
    ELSIF d.value_type = 'boolean' THEN
      IF NEW.value_bool IS NULL OR NEW.value_numeric IS NOT NULL OR NEW.value_text IS NOT NULL THEN
        RAISE EXCEPTION 'CNAI-STATE: % requires a boolean value only', NEW.element_code USING ERRCODE = '23514';
      END IF;
    ELSE
      IF NEW.value_text IS NULL OR NEW.value_numeric IS NOT NULL OR NEW.value_bool IS NOT NULL THEN
        RAISE EXCEPTION 'CNAI-STATE: % requires a text value only', NEW.element_code USING ERRCODE = '23514';
      END IF;
      IF d.value_type = 'coded' AND NOT (NEW.value_text = ANY (d.allowed_values)) THEN
        RAISE EXCEPTION 'CNAI-STATE: "%" is not an allowed value for %', NEW.value_text, NEW.element_code USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;

  IF NEW.supersedes_id IS NOT NULL THEN
    SELECT * INTO s FROM clinical.observation WHERE observation_id = NEW.supersedes_id;
    IF s.encounter_id IS DISTINCT FROM NEW.encounter_id OR s.element_code IS DISTINCT FROM NEW.element_code THEN
      RAISE EXCEPTION 'CNAI-HIST: a correction must supersede an observation of the same element in the same encounter'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER observation_guard BEFORE INSERT ON clinical.observation
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_observation();

-- ---------------------------------------------------------------------------
-- Append-only history (FR-INT-05): observations, rule reviews and rules are never updated or deleted in place
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'CNAI-HIST: % is append-only; record a correction as a new version', TG_TABLE_NAME USING ERRCODE = '23514';
END $$;
CREATE TRIGGER observation_append_only BEFORE UPDATE OR DELETE ON clinical.observation
  FOR EACH ROW EXECUTE FUNCTION clinical.block_mutation();
CREATE TRIGGER observation_no_truncate BEFORE TRUNCATE ON clinical.observation
  FOR EACH STATEMENT EXECUTE FUNCTION clinical.block_mutation();
CREATE TRIGGER rule_review_append_only BEFORE UPDATE OR DELETE ON clinical.rule_review
  FOR EACH ROW EXECUTE FUNCTION clinical.block_mutation();

-- ---------------------------------------------------------------------------
-- Rules: content is immutable; review_status changes only through clinical.rule_review (FR-GOV-02)
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.guard_rule() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'CNAI-HIST: rules are never deleted; retire by publishing a new version' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.review_status <> 'draft' THEN
      RAISE EXCEPTION 'CNAI-GOV: a new rule must start as draft' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.rule_id <> OLD.rule_id OR NEW.version <> OLD.version OR NEW.kind <> OLD.kind
     OR NEW.pathway_code <> OLD.pathway_code OR NEW.definition <> OLD.definition OR NEW.author_id <> OLD.author_id THEN
    RAISE EXCEPTION 'CNAI-HIST: rule content is immutable; publish a new version' USING ERRCODE = '23514';
  END IF;
  IF NEW.review_status <> OLD.review_status AND pg_trigger_depth() < 2 THEN
    RAISE EXCEPTION 'CNAI-GOV: review_status can change only by recording a review' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER rule_guard BEFORE INSERT OR UPDATE OR DELETE ON clinical.rule
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_rule();

CREATE FUNCTION clinical.guard_rule_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r clinical.rule%ROWTYPE; needed_role text;
BEGIN
  SELECT * INTO r FROM clinical.rule WHERE rule_id = NEW.rule_id AND version = NEW.version;
  needed_role := CASE NEW.level WHEN 'independently_reviewed' THEN 'independent_clinical_safety_reviewer'
                                ELSE 'product_clinical_requirements_lead' END;
  IF NOT EXISTS (SELECT 1 FROM ops.governance_role g
                 WHERE g.role_key = needed_role AND g.status = 'assigned' AND g.assigned_user_id = NEW.reviewer_id) THEN
    RAISE EXCEPTION 'CNAI-GOV: reviewer does not hold the assigned % role', needed_role USING ERRCODE = '23514';
  END IF;
  IF NEW.level = 'independently_reviewed' THEN
    IF NEW.reviewer_id = r.author_id THEN
      RAISE EXCEPTION 'CNAI-GOV: an author cannot independently review their own rule' USING ERRCODE = '23514';
    END IF;
    IF r.review_status = 'draft' THEN
      RAISE EXCEPTION 'CNAI-GOV: a rule must be author_approved before independent review' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER rule_review_guard BEFORE INSERT ON clinical.rule_review
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_rule_review();

CREATE FUNCTION clinical.apply_rule_review() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE clinical.rule
     SET review_status = CASE NEW.outcome WHEN 'approved' THEN NEW.level ELSE 'draft'::clinical.review_status END
   WHERE rule_id = NEW.rule_id AND version = NEW.version;
  RETURN NEW;
END $$;
CREATE TRIGGER rule_review_apply AFTER INSERT ON clinical.rule_review
  FOR EACH ROW EXECUTE FUNCTION clinical.apply_rule_review();

-- ---------------------------------------------------------------------------
-- Governance roles: the independent reviewer must be a different person from the product and clinical lead (D-019)
-- ---------------------------------------------------------------------------
CREATE FUNCTION ops.guard_governance_role() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'assigned'
     AND NEW.role_key IN ('independent_clinical_safety_reviewer', 'product_clinical_requirements_lead')
     AND EXISTS (SELECT 1 FROM ops.governance_role g
                 WHERE g.role_key IN ('independent_clinical_safety_reviewer', 'product_clinical_requirements_lead')
                   AND g.role_key <> NEW.role_key AND g.assigned_user_id = NEW.assigned_user_id) THEN
    RAISE EXCEPTION 'CNAI-GOV: the independent reviewer and the product and clinical lead must be different people'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER governance_role_guard BEFORE INSERT OR UPDATE ON ops.governance_role
  FOR EACH ROW EXECUTE FUNCTION ops.guard_governance_role();

-- ---------------------------------------------------------------------------
-- Pathways are never deleted; reference data is written by migrations only
-- ---------------------------------------------------------------------------
CREATE FUNCTION clinical.guard_pathway() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'CNAI-HIST: pathways are never deleted; set status retired' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER pathway_no_delete BEFORE DELETE ON clinical.pathway
  FOR EACH ROW EXECUTE FUNCTION clinical.guard_pathway();
