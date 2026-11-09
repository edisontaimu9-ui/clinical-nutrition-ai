-- 0006_model_run_audit: audit vocabulary for the LLM gateway (FR-AUD-02, NFR-SEC-09). Phase 3. Decision: D-025.
-- Model runs and gate refusals are written to the hash-chained audit log with identifiers and hashes only, never prompt or
-- output text (SECURITY.md 7.3 item 8).
INSERT INTO audit.action_def (action, description) VALUES
  ('model.run', 'Model run completed: backend, model, version, prompt version, input and output hashes'),
  ('model.refused', 'Model call refused by a gate before any outbound request'),
  ('model.failed', 'Model call failed or its output was rejected; no draft was produced');
