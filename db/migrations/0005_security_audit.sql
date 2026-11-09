-- 0005_security_audit: least-privilege database roles, row-level security, break-glass access, and an append-only
-- hash-chained audit log. Phase 2. Synthetic data only.
-- Requirements: FR-AUD-01, FR-AUD-03, FR-ADM-01, NFR-SEC-02, NFR-SEC-03.  Decision: D-024.
--
-- Model
--   * Coarse privileges: NOLOGIN roles (cnai_clinician, cnai_safety_lead, cnai_admin, cnai_auditor, cnai_identity_service).
--     The application connects as a login role that is a member of these and runs SET LOCAL ROLE per request.
--     No app role is a table owner, superuser or BYPASSRLS.
--   * Fine privileges: row-level security. The request context is app.user_id (set per request); the organisation is NEVER
--     taken from the client, it is looked up from the active user (sec.app_org()). Unset or unknown context sees nothing.
--   * Patient data is reachable only through an active care relationship, or a time-boxed, second-person-approved
--     break-glass grant (read only). Admins and auditors have no access to patient content.
--   * Tables are owned by the migration role, which is not used by the application. RLS is ENABLEd (not FORCEd) because the
--     SECURITY DEFINER guard and helper functions run as the owner and must see all rows (D-024).

-- ---------------------------------------------------------------------------
-- Roles (cluster-wide, so created idempotently)
-- ---------------------------------------------------------------------------
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['cnai_clinician', 'cnai_safety_lead', 'cnai_admin', 'cnai_auditor', 'cnai_identity_service', 'cnai_auth_service'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT', r);
    END IF;
  END LOOP;
END $$;

CREATE SCHEMA audit;
CREATE SCHEMA sec;

-- Nothing by default.
REVOKE ALL ON SCHEMA iam, identity, clinical, ops, audit, sec FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA iam, identity, clinical, ops FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Guard functions run as the owner so that the invoking role needs no privileges on the tables they inspect.
-- ---------------------------------------------------------------------------
ALTER FUNCTION clinical.guard_patient()        SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION ops.guard_instance_config()     SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION clinical.guard_encounter()      SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION clinical.guard_observation()    SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION clinical.guard_rule()           SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION clinical.guard_rule_review()    SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION clinical.apply_rule_review()    SECURITY DEFINER SET search_path = pg_catalog, public;
ALTER FUNCTION ops.guard_governance_role()     SECURITY DEFINER SET search_path = pg_catalog, public;

-- ---------------------------------------------------------------------------
-- Sign-in support: the authentication service maps a verified token subject to a database user and roles. Roles always
-- come from iam.role_grant, never from the token.
-- ---------------------------------------------------------------------------
CREATE FUNCTION sec.resolve_subject(p_subject text) RETURNS TABLE (user_id uuid, org_id uuid, roles text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT u.user_id, u.org_id,
         coalesce((SELECT array_agg(g.role::text ORDER BY g.role::text) FROM iam.role_grant g
                   WHERE g.user_id = u.user_id AND (g.expires_at IS NULL OR g.expires_at > now())), ARRAY[]::text[])
  FROM iam.app_user u WHERE u.external_subject = p_subject AND u.is_active
$$;

-- ---------------------------------------------------------------------------
-- Break-glass grants (created before the helper functions that read them)
-- ---------------------------------------------------------------------------
CREATE TABLE ops.break_glass (
  grant_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES iam.app_user(user_id),
  patient_id    uuid NOT NULL REFERENCES clinical.patient(patient_id),
  justification text NOT NULL CHECK (length(btrim(justification)) >= 20),
  approved_by   uuid NOT NULL REFERENCES iam.app_user(user_id),
  starts_at     timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  CHECK (approved_by <> user_id),
  CHECK (expires_at > starts_at AND expires_at <= starts_at + interval '24 hours')
);
CREATE FUNCTION ops.block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'CNAI-HIST: % is append-only', TG_TABLE_NAME USING ERRCODE = '23514';
END $$;
CREATE TRIGGER break_glass_append_only BEFORE UPDATE OR DELETE ON ops.break_glass
  FOR EACH ROW EXECUTE FUNCTION ops.block_mutation();

-- ---------------------------------------------------------------------------
-- Session context and authorisation helpers (schema sec)
-- ---------------------------------------------------------------------------
CREATE FUNCTION sec.app_user() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

-- The organisation of the active session user, read from the database, never from the client.
CREATE FUNCTION sec.app_org() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT org_id FROM iam.app_user WHERE user_id = sec.app_user() AND is_active
$$;

CREATE FUNCTION sec.has_role(p_roles text[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
  SET search_path = pg_catalog, public AS $$
  SELECT EXISTS (
    SELECT 1 FROM iam.role_grant g JOIN iam.app_user u ON u.user_id = g.user_id
    WHERE g.user_id = sec.app_user() AND u.is_active AND g.role::text = ANY (p_roles)
      AND (g.expires_at IS NULL OR g.expires_at > now()))
$$;

-- p_write = true requires a care relationship; false also accepts an active break-glass grant (read only).
CREATE FUNCTION sec.can_access_patient(p_patient uuid, p_write boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT sec.app_org() IS NOT NULL
     AND sec.has_role(ARRAY['clinician', 'trainee', 'supervisor', 'clinical_safety_lead'])
     AND EXISTS (SELECT 1 FROM clinical.patient p WHERE p.patient_id = p_patient AND p.org_id = sec.app_org())
     AND (
       EXISTS (SELECT 1 FROM clinical.care_relationship c
               WHERE c.patient_id = p_patient AND c.user_id = sec.app_user()
                 AND c.starts_at <= now() AND (c.ends_at IS NULL OR c.ends_at > now()))
       OR (NOT p_write AND EXISTS (SELECT 1 FROM ops.break_glass b
               WHERE b.patient_id = p_patient AND b.user_id = sec.app_user()
                 AND b.starts_at <= now() AND b.expires_at > now()))
     )
$$;

CREATE FUNCTION sec.can_access_encounter(p_encounter uuid, p_write boolean DEFAULT false) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
  SELECT EXISTS (SELECT 1 FROM clinical.encounter e WHERE e.encounter_id = p_encounter
                   AND e.org_id = sec.app_org() AND sec.can_access_patient(e.patient_id, p_write))
$$;

-- Creating a patient also creates the creator's care relationship; direct inserts are not permitted to app roles.
CREATE FUNCTION sec.register_patient(p_pseudonym text, p_sex text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_pid uuid; v_cls text;
BEGIN
  IF sec.app_org() IS NULL OR NOT sec.has_role(ARRAY['clinician', 'trainee', 'supervisor']) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: not permitted to register patients' USING ERRCODE = '42501';
  END IF;
  SELECT data_class INTO v_cls FROM ops.instance_config;
  INSERT INTO clinical.patient (org_id, pseudonym, sex_at_birth, is_synthetic)
    VALUES (sec.app_org(), p_pseudonym, p_sex, v_cls = 'synthetic') RETURNING patient_id INTO v_pid;
  INSERT INTO clinical.care_relationship (user_id, patient_id) VALUES (sec.app_user(), v_pid);
  RETURN v_pid;
END $$;

-- A clinician with a care relationship can add a colleague of the same organisation (hand-over, shared care).
CREATE FUNCTION sec.grant_care(p_patient uuid, p_user uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NOT sec.can_access_patient(p_patient, true) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: no care relationship with this patient' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM iam.app_user u JOIN iam.role_grant g ON g.user_id = u.user_id
                 WHERE u.user_id = p_user AND u.is_active AND u.org_id = sec.app_org()
                   AND g.role IN ('clinician', 'trainee', 'supervisor') AND (g.expires_at IS NULL OR g.expires_at > now())) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: target user is not an active clinician of this organisation' USING ERRCODE = '42501';
  END IF;
  INSERT INTO clinical.care_relationship (user_id, patient_id) VALUES (p_user, p_patient);
END $$;

-- Break-glass: a second person (supervisor or clinical safety lead, same organisation) approves a time-boxed read grant.
-- The caller is the APPROVER; p_user is the person who needs access.
CREATE FUNCTION sec.grant_break_glass(p_user uuid, p_patient uuid, p_justification text, p_hours integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_id uuid;
BEGIN
  IF sec.app_org() IS NULL OR NOT sec.has_role(ARRAY['supervisor', 'clinical_safety_lead']) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: only a supervisor or clinical safety lead can approve break-glass access' USING ERRCODE = '42501';
  END IF;
  IF p_user = sec.app_user() THEN
    RAISE EXCEPTION 'CNAI-ACCESS: break-glass needs a second person; approve for someone else' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM iam.app_user u JOIN iam.role_grant g ON g.user_id = u.user_id
                 WHERE u.user_id = p_user AND u.is_active AND u.org_id = sec.app_org()
                   AND g.role IN ('clinician', 'trainee', 'supervisor', 'clinical_safety_lead')
                   AND (g.expires_at IS NULL OR g.expires_at > now())) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: beneficiary is not an active clinical user of this organisation' USING ERRCODE = '42501';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM clinical.patient WHERE patient_id = p_patient AND org_id = sec.app_org()) THEN
    RAISE EXCEPTION 'CNAI-ACCESS: unknown patient' USING ERRCODE = '42501';
  END IF;
  IF p_hours IS NULL OR p_hours < 1 OR p_hours > 24 THEN
    RAISE EXCEPTION 'CNAI-ACCESS: break-glass lasts 1 to 24 hours' USING ERRCODE = '23514';
  END IF;
  INSERT INTO ops.break_glass (user_id, patient_id, justification, approved_by, expires_at)
    VALUES (p_user, p_patient, p_justification, sec.app_user(), now() + make_interval(hours => p_hours))
    RETURNING grant_id INTO v_id;
  RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------
-- Audit log: append-only and hash-chained (FR-AUD-01, FR-AUD-03)
-- ---------------------------------------------------------------------------
CREATE SEQUENCE audit.event_seq;

CREATE TABLE audit.event (
  event_id      bigint PRIMARY KEY,
  occurred_at   timestamptz NOT NULL,
  actor_id      uuid,
  actor_role    text,
  action        text NOT NULL,
  resource_type text NOT NULL,
  resource_id   text,
  patient_id    uuid,
  outcome       text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
  source_ip     text,
  request_id    text,
  details       jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object' AND pg_column_size(details) < 4096),
  prev_hash     bytea,
  hash          bytea NOT NULL
);

CREATE FUNCTION audit.block_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'CNAI-AUDIT: audit.event is append-only' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER audit_event_no_update_delete BEFORE UPDATE OR DELETE ON audit.event
  FOR EACH ROW EXECUTE FUNCTION audit.block_mutation();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit.event
  FOR EACH STATEMENT EXECUTE FUNCTION audit.block_mutation();

-- The chain formula. Fields are joined with the unit separator; NULL becomes the empty string; the timestamp is
-- rendered in UTC with microseconds. The Python verifier (cnai_security.audit) implements the same formula independently.
CREATE FUNCTION audit.ts_text(p_ts timestamptz) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
$$;

CREATE FUNCTION audit.compute_hash(p_id bigint, p_ts timestamptz, p_actor uuid, p_role text, p_action text, p_rtype text,
                                   p_rid text, p_patient uuid, p_outcome text, p_ip text, p_req text, p_details jsonb,
                                   p_prev bytea) RETURNS bytea LANGUAGE sql IMMUTABLE AS $$
  SELECT sha256(convert_to(concat_ws(E'\x1f',
    p_id::text, audit.ts_text(p_ts), coalesce(p_actor::text, ''), coalesce(p_role, ''), p_action, p_rtype,
    coalesce(p_rid, ''), coalesce(p_patient::text, ''), p_outcome, coalesce(p_ip, ''), coalesce(p_req, ''),
    p_details::text, coalesce(encode(p_prev, 'hex'), '')), 'UTF8'))
$$;

-- Internal writer. Not executable by application roles. The actor always comes from the session context.
CREATE FUNCTION audit.write(p_action text, p_rtype text, p_rid text, p_patient uuid, p_outcome text, p_req text,
                            p_details jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_prev bytea; v_id bigint; v_ts timestamptz := clock_timestamp(); v_actor uuid := sec.app_user();
        v_role text := nullif(current_setting('app.role', true), ''); v_ip text := nullif(current_setting('app.source_ip', true), '');
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('cnai.audit.chain'));   -- one writer at a time keeps the chain linear
  SELECT hash INTO v_prev FROM audit.event ORDER BY event_id DESC LIMIT 1;
  v_id := nextval('audit.event_seq');
  INSERT INTO audit.event (event_id, occurred_at, actor_id, actor_role, action, resource_type, resource_id, patient_id,
                           outcome, source_ip, request_id, details, prev_hash, hash)
  VALUES (v_id, v_ts, v_actor, v_role, p_action, p_rtype, p_rid, p_patient, p_outcome, v_ip, p_req,
          coalesce(p_details, '{}'::jsonb), v_prev,
          audit.compute_hash(v_id, v_ts, v_actor, v_role, p_action, p_rtype, p_rid, p_patient, p_outcome, v_ip, p_req,
                             coalesce(p_details, '{}'::jsonb), v_prev));
  RETURN v_id;
END $$;

-- Controlled vocabulary for application-reported events (reads, exports, authentication, kill switch, ...).
CREATE TABLE audit.action_def (
  action      text PRIMARY KEY CHECK (action ~ '^[a-z_]+(\.[a-z_]+)+$'),
  description text NOT NULL
);
INSERT INTO audit.action_def VALUES
  ('auth.login', 'Successful sign-in'), ('auth.login_failed', 'Failed sign-in'), ('auth.mfa_failed', 'Failed multi-factor check'),
  ('auth.session_revoked', 'Session revoked'), ('auth.token_rejected', 'Access token rejected'),
  ('patient.read', 'Patient record opened'), ('patient.search', 'Patient search (counts only)'),
  ('patient.export', 'Patient data exported'), ('analysis.run', 'Deterministic analysis run'),
  ('review.approve', 'Clinician approved'), ('review.reject', 'Clinician rejected'), ('review.edit', 'Clinician edited'),
  ('review.cosign', 'Supervisor co-signed'), ('config.change', 'Rule, prompt, model or KB version changed'),
  ('system.kill_switch', 'AI generation kill switch used'), ('system.startup_check', 'Start-up governance check'),
  ('egress.blocked', 'Outbound request blocked'), ('egress.hosted_gate', 'Hosted LLM gate decision'),
  ('access.denied', 'Authorisation refused');
CREATE FUNCTION audit.block_action_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'CNAI-AUDIT: audit.action_def changes only by migration' USING ERRCODE = '23514'; END $$;
CREATE TRIGGER action_def_immutable BEFORE UPDATE OR DELETE ON audit.action_def
  FOR EACH ROW EXECUTE FUNCTION audit.block_action_mutation();

-- Public writer for application events. The caller cannot choose the actor, and the action must be in the vocabulary.
CREATE FUNCTION sec.record_event(p_action text, p_rtype text, p_rid text, p_patient uuid, p_outcome text, p_req text,
                                 p_details jsonb DEFAULT '{}'::jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM audit.action_def WHERE action = p_action) THEN
    RAISE EXCEPTION 'CNAI-AUDIT: unknown audit action %', p_action USING ERRCODE = '23514';
  END IF;
  RETURN audit.write(p_action, p_rtype, p_rid, p_patient, p_outcome, p_req, p_details);
END $$;

-- Verification in SQL: returns the first broken event, or no rows when the whole chain is intact.
CREATE FUNCTION audit.verify_chain() RETURNS TABLE (bad_event_id bigint, reason text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE r audit.event%ROWTYPE; v_prev bytea := NULL; v_first boolean := true;
BEGIN
  FOR r IN SELECT * FROM audit.event ORDER BY event_id LOOP
    IF r.prev_hash IS DISTINCT FROM v_prev THEN
      bad_event_id := r.event_id; reason := CASE WHEN v_first THEN 'first event has a previous hash' ELSE 'link to previous event is broken (event removed, inserted or reordered)' END;
      RETURN NEXT; RETURN;
    END IF;
    IF r.hash <> audit.compute_hash(r.event_id, r.occurred_at, r.actor_id, r.actor_role, r.action, r.resource_type,
         r.resource_id, r.patient_id, r.outcome, r.source_ip, r.request_id, r.details, r.prev_hash) THEN
      bad_event_id := r.event_id; reason := 'event content does not match its hash'; RETURN NEXT; RETURN;
    END IF;
    v_prev := r.hash; v_first := false;
  END LOOP;
END $$;

-- Generic change-audit trigger. TG_ARGV: [0] primary-key column, [1] comma list of non-clinical columns to copy into details.
CREATE FUNCTION audit.log_change() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_row jsonb := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END); v_details jsonb := jsonb_build_object('op', TG_OP);
        c text;
BEGIN
  IF TG_NARGS > 1 AND TG_ARGV[1] <> '' THEN
    FOREACH c IN ARRAY string_to_array(TG_ARGV[1], ',') LOOP
      v_details := v_details || jsonb_build_object(c, v_row -> c);
    END LOOP;
  END IF;
  PERFORM audit.write(TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME || '.' || lower(TG_OP), TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
                      v_row ->> TG_ARGV[0], nullif(v_row ->> 'patient_id', '')::uuid, 'success', NULL, v_details);
  RETURN NULL;
END $$;

-- Patient content: who created what (never the values). Authorisation and governance: who changed what.
CREATE TRIGGER audit_patient         AFTER INSERT OR UPDATE OR DELETE ON clinical.patient           FOR EACH ROW EXECUTE FUNCTION audit.log_change('patient_id', '');
CREATE TRIGGER audit_encounter       AFTER INSERT OR UPDATE OR DELETE ON clinical.encounter         FOR EACH ROW EXECUTE FUNCTION audit.log_change('encounter_id', 'pathway');
CREATE TRIGGER audit_observation     AFTER INSERT OR UPDATE OR DELETE ON clinical.observation       FOR EACH ROW EXECUTE FUNCTION audit.log_change('observation_id', 'encounter_id,element_code,status,supersedes_id');
CREATE TRIGGER audit_calculation     AFTER INSERT OR UPDATE OR DELETE ON clinical.calculation       FOR EACH ROW EXECUTE FUNCTION audit.log_change('calculation_id', 'encounter_id,formula_id,formula_version');
CREATE TRIGGER audit_finding         AFTER INSERT OR UPDATE OR DELETE ON clinical.finding           FOR EACH ROW EXECUTE FUNCTION audit.log_change('finding_id', 'encounter_id,rule_id,kind,criticality');
CREATE TRIGGER audit_care            AFTER INSERT OR UPDATE OR DELETE ON clinical.care_relationship FOR EACH ROW EXECUTE FUNCTION audit.log_change('patient_id', 'user_id');
CREATE TRIGGER audit_rule            AFTER INSERT OR UPDATE OR DELETE ON clinical.rule              FOR EACH ROW EXECUTE FUNCTION audit.log_change('rule_id', 'version,review_status');
CREATE TRIGGER audit_rule_review     AFTER INSERT OR UPDATE OR DELETE ON clinical.rule_review       FOR EACH ROW EXECUTE FUNCTION audit.log_change('review_id', 'rule_id,version,level,outcome,reviewer_id');
CREATE TRIGGER audit_role_grant      AFTER INSERT OR UPDATE OR DELETE ON iam.role_grant             FOR EACH ROW EXECUTE FUNCTION audit.log_change('user_id', 'user_id,role,granted_by,expires_at');
CREATE TRIGGER audit_app_user        AFTER INSERT OR UPDATE OR DELETE ON iam.app_user               FOR EACH ROW EXECUTE FUNCTION audit.log_change('user_id', 'org_id,is_active');
CREATE TRIGGER audit_governance      AFTER INSERT OR UPDATE OR DELETE ON ops.governance_role        FOR EACH ROW EXECUTE FUNCTION audit.log_change('role_key', 'status,assigned_user_id,decision_ref');
CREATE TRIGGER audit_instance_config AFTER UPDATE ON ops.instance_config                            FOR EACH ROW EXECUTE FUNCTION audit.log_change('singleton', 'data_class,mode');
CREATE TRIGGER audit_break_glass     AFTER INSERT OR UPDATE OR DELETE ON ops.break_glass            FOR EACH ROW EXECUTE FUNCTION audit.log_change('grant_id', 'user_id,approved_by,expires_at');
CREATE TRIGGER audit_identity        AFTER INSERT OR UPDATE OR DELETE ON identity.patient_identity  FOR EACH ROW EXECUTE FUNCTION audit.log_change('patient_id', 'key_id');

-- ---------------------------------------------------------------------------
-- Row-level security
-- ---------------------------------------------------------------------------
ALTER TABLE clinical.patient           ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.encounter         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.observation       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.calculation       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.finding           ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.care_relationship ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.patient_identity  ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam.app_user               ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam.role_grant             ENABLE ROW LEVEL SECURITY;
ALTER TABLE iam.cosign_relationship    ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.governance_role        ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.instance_config        ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.break_glass            ENABLE ROW LEVEL SECURITY;

-- Patient content (clinician and safety lead read; only clinicians write; safety lead reads only through break-glass)
CREATE POLICY patient_read ON clinical.patient FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (sec.can_access_patient(patient_id));
CREATE POLICY encounter_read ON clinical.encounter FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (org_id = sec.app_org() AND sec.can_access_patient(patient_id));
CREATE POLICY encounter_write ON clinical.encounter FOR INSERT TO cnai_clinician
  WITH CHECK (org_id = sec.app_org() AND created_by = sec.app_user() AND sec.can_access_patient(patient_id, true));
CREATE POLICY observation_read ON clinical.observation FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (sec.can_access_encounter(encounter_id));
CREATE POLICY observation_write ON clinical.observation FOR INSERT TO cnai_clinician
  WITH CHECK (recorded_by = sec.app_user() AND sec.can_access_encounter(encounter_id, true));
CREATE POLICY calculation_read ON clinical.calculation FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (sec.can_access_encounter(encounter_id));
CREATE POLICY calculation_write ON clinical.calculation FOR INSERT TO cnai_clinician
  WITH CHECK (sec.can_access_encounter(encounter_id, true));
CREATE POLICY finding_read ON clinical.finding FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (sec.can_access_encounter(encounter_id));
CREATE POLICY finding_write ON clinical.finding FOR INSERT TO cnai_clinician
  WITH CHECK (sec.can_access_encounter(encounter_id, true));
CREATE POLICY finding_resolve ON clinical.finding FOR UPDATE TO cnai_clinician
  USING (sec.can_access_encounter(encounter_id, true)) WITH CHECK (sec.can_access_encounter(encounter_id, true));
CREATE POLICY care_read ON clinical.care_relationship FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (user_id = sec.app_user());

-- Identity vault: only the identity service role, and only for patients the session user may write to.
CREATE POLICY identity_read ON identity.patient_identity FOR SELECT TO cnai_identity_service
  USING (org_id = sec.app_org() AND sec.can_access_patient(patient_id));
CREATE POLICY identity_write ON identity.patient_identity FOR INSERT TO cnai_identity_service
  WITH CHECK (org_id = sec.app_org() AND sec.can_access_patient(patient_id, true));
CREATE POLICY identity_update ON identity.patient_identity FOR UPDATE TO cnai_identity_service
  USING (org_id = sec.app_org() AND sec.can_access_patient(patient_id, true))
  WITH CHECK (org_id = sec.app_org() AND sec.can_access_patient(patient_id, true));

-- User administration (same organisation only; an admin cannot grant roles to themselves)
CREATE POLICY user_read_admin ON iam.app_user FOR SELECT TO cnai_admin USING (org_id = sec.app_org());
CREATE POLICY user_write_admin ON iam.app_user FOR INSERT TO cnai_admin WITH CHECK (org_id = sec.app_org());
CREATE POLICY user_update_admin ON iam.app_user FOR UPDATE TO cnai_admin
  USING (org_id = sec.app_org()) WITH CHECK (org_id = sec.app_org());
CREATE POLICY user_read_clinical ON iam.app_user FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (org_id = sec.app_org() AND is_active);
CREATE POLICY grant_read_admin ON iam.role_grant FOR SELECT TO cnai_admin
  USING (EXISTS (SELECT 1 FROM iam.app_user u WHERE u.user_id = role_grant.user_id AND u.org_id = sec.app_org()));
CREATE POLICY grant_write_admin ON iam.role_grant FOR INSERT TO cnai_admin
  WITH CHECK (user_id <> sec.app_user() AND granted_by = sec.app_user()
              AND EXISTS (SELECT 1 FROM iam.app_user u WHERE u.user_id = role_grant.user_id AND u.org_id = sec.app_org()));
CREATE POLICY grant_update_admin ON iam.role_grant FOR UPDATE TO cnai_admin
  USING (user_id <> sec.app_user()
         AND EXISTS (SELECT 1 FROM iam.app_user u WHERE u.user_id = role_grant.user_id AND u.org_id = sec.app_org()))
  WITH CHECK (user_id <> sec.app_user());
CREATE POLICY cosign_admin ON iam.cosign_relationship FOR ALL TO cnai_admin
  USING (EXISTS (SELECT 1 FROM iam.app_user u WHERE u.user_id = trainee_id AND u.org_id = sec.app_org()))
  WITH CHECK (EXISTS (SELECT 1 FROM iam.app_user u WHERE u.user_id = trainee_id AND u.org_id = sec.app_org()));

-- Governance: everyone signed in can read the status; admin can record appointments but not appoint themselves.
CREATE POLICY gov_read ON ops.governance_role FOR SELECT TO cnai_admin, cnai_clinician, cnai_safety_lead, cnai_auditor USING (true);
CREATE POLICY gov_update ON ops.governance_role FOR UPDATE TO cnai_admin
  USING (true) WITH CHECK (assigned_user_id IS DISTINCT FROM sec.app_user());
CREATE POLICY config_read ON ops.instance_config FOR SELECT TO cnai_admin, cnai_clinician, cnai_safety_lead, cnai_auditor USING (true);
-- Break-glass rows: visible to the beneficiary, the approver and auditors' metadata via the audit log only.
CREATE POLICY break_glass_read ON ops.break_glass FOR SELECT TO cnai_clinician, cnai_safety_lead
  USING (user_id = sec.app_user() OR approved_by = sec.app_user());

-- ---------------------------------------------------------------------------
-- Privileges (RLS narrows the rows; these narrow the tables and columns)
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA sec TO cnai_clinician, cnai_safety_lead, cnai_admin, cnai_auditor, cnai_identity_service, cnai_auth_service;
GRANT USAGE ON SCHEMA clinical TO cnai_clinician, cnai_safety_lead;
GRANT USAGE ON SCHEMA iam TO cnai_clinician, cnai_safety_lead, cnai_admin;
GRANT USAGE ON SCHEMA ops TO cnai_clinician, cnai_safety_lead, cnai_admin, cnai_auditor;
GRANT USAGE ON SCHEMA identity TO cnai_identity_service;
GRANT USAGE ON SCHEMA audit TO cnai_auditor;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA sec, audit FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sec.app_user(), sec.app_org(), sec.has_role(text[]), sec.can_access_patient(uuid, boolean),
  sec.can_access_encounter(uuid, boolean)
  TO cnai_clinician, cnai_safety_lead, cnai_admin, cnai_auditor, cnai_identity_service;
GRANT EXECUTE ON FUNCTION sec.register_patient(text, text), sec.grant_care(uuid, uuid) TO cnai_clinician;
GRANT EXECUTE ON FUNCTION sec.grant_break_glass(uuid, uuid, text, integer) TO cnai_clinician, cnai_safety_lead;
GRANT EXECUTE ON FUNCTION sec.record_event(text, text, text, uuid, text, text, jsonb)
  TO cnai_clinician, cnai_safety_lead, cnai_admin, cnai_auditor, cnai_identity_service;
GRANT EXECUTE ON FUNCTION sec.resolve_subject(text) TO cnai_auth_service;
GRANT EXECUTE ON FUNCTION sec.record_event(text, text, text, uuid, text, text, jsonb) TO cnai_auth_service;
GRANT EXECUTE ON FUNCTION audit.verify_chain() TO cnai_auditor;
GRANT EXECUTE ON FUNCTION audit.compute_hash(bigint, timestamptz, uuid, text, text, text, text, uuid, text, text, text, jsonb, bytea),
  audit.ts_text(timestamptz) TO cnai_auditor;

-- Reference data and rules: readable by clinical roles; rules and reviews are written by clinicians (triggers enforce governance).
GRANT SELECT ON clinical.pathway, clinical.reference_standard, clinical.reference_lms, clinical.data_element_def,
  clinical.rule, clinical.rule_review TO cnai_clinician, cnai_safety_lead;
GRANT INSERT ON clinical.rule, clinical.rule_review TO cnai_clinician;
GRANT INSERT ON clinical.rule_review TO cnai_safety_lead;

GRANT SELECT ON clinical.patient, clinical.encounter, clinical.observation, clinical.calculation, clinical.finding,
  clinical.care_relationship TO cnai_clinician, cnai_safety_lead;
GRANT INSERT ON clinical.encounter, clinical.observation, clinical.calculation, clinical.finding TO cnai_clinician;
GRANT UPDATE (resolved_by, resolved_at) ON clinical.finding TO cnai_clinician;
GRANT SELECT ON ops.break_glass TO cnai_clinician, cnai_safety_lead;
GRANT SELECT ON ops.instance_config, ops.governance_role TO cnai_admin, cnai_clinician, cnai_safety_lead, cnai_auditor;

GRANT SELECT (user_id, display_name, org_id, is_active) ON iam.app_user TO cnai_clinician, cnai_safety_lead;
GRANT SELECT, INSERT, UPDATE ON iam.app_user TO cnai_admin;
GRANT SELECT, INSERT, UPDATE ON iam.role_grant TO cnai_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON iam.cosign_relationship TO cnai_admin;
GRANT UPDATE (status, assigned_user_id, assigned_at, decision_ref) ON ops.governance_role TO cnai_admin;

GRANT SELECT, INSERT, UPDATE ON identity.patient_identity TO cnai_identity_service;
GRANT SELECT ON audit.event TO cnai_auditor;
REVOKE UPDATE, DELETE, TRUNCATE ON audit.event FROM PUBLIC;
