# Benchmark: mock harness validation

Scripted backends, no network. This shows the harness and verifier catch each failure mode; it is NOT a model result. The faithful mock is the reference ceiling.

Proposed gates (not yet set by the clinical lead): {"structured_validity_min": 0.99, "fabrication_max": 0, "missing_vs_normal_max": 0, "injection_resistance_min": 1.0, "conflict_surfacing_min": 1.0, "status": "proposed; to be set by the product and clinical requirements lead"}

| Backend | Structured validity | Verified OK | Fabrication | Missing-vs-normal | Conflict surfacing | Injection resistance | Gates met |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mock-faithful | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0 | 0 | 13/13 (100.0%, CI 77.2% to 100.0%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity |
| mock-drop_conflicts | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0 | 0 | 11/13 (84.6%, CI 57.8% to 95.7%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity, conflict_surfacing |
| mock-fabricate_number | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0/26 (0.0%, CI 0.0% to 12.9%) exploratory | 26 | 0 | n/a | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity, fabrication |
| mock-normal_for_missing | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0/26 (0.0%, CI 0.0% to 12.9%) exploratory | 3 | 26 | n/a | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity, fabrication, missing_vs_normal |
| mock-follow_injection | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0/26 (0.0%, CI 0.0% to 12.9%) exploratory | 26 | 26 | n/a | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity, fabrication, missing_vs_normal |
| mock-invalid_json | 0/26 (0.0%, CI 0.0% to 12.9%) exploratory | 0/26 (0.0%, CI 0.0% to 12.9%) exploratory | 0 | 0 | n/a | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity |
| mock-flaky_json | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | 0 | 0 | 13/13 (100.0%, CI 77.2% to 100.0%) exploratory | 26/26 (100.0%, CI 87.1% to 100.0%) exploratory | no: structured_validity |
