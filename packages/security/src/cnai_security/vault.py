"""Identity vault: direct identifiers are stored only here, encrypted, apart from clinical data (FR-INT-01, NFR-SEC-02).

Run these as the cnai_identity_service role with the request context bound (cnai_security.session.bind); row-level
security then limits access to patients the session user may use. Phase 2 stores and retrieves; nothing in the
repository holds real identifiers (D-009, D-020).
"""
import json
import uuid

from .crypto import decrypt_field, encrypt_field

ALLOWED_FIELDS = frozenset({"full_name", "date_of_birth", "national_id", "phone", "address", "next_of_kin", "facility_number"})


def _context(patient_id, org_id) -> bytes:
    return f"identity:{org_id}:{patient_id}".encode()


def store_identifiers(conn, patient_id, org_id, identifiers: dict, provider) -> None:
    unknown = set(identifiers) - ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"unsupported identity fields: {sorted(unknown)}")
    blob = encrypt_field(provider, json.dumps(identifiers, sort_keys=True).encode(), _context(patient_id, org_id))
    conn.execute(
        "INSERT INTO identity.patient_identity (patient_id, org_id, encrypted_identifiers, key_id) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (patient_id) DO UPDATE SET encrypted_identifiers = EXCLUDED.encrypted_identifiers, key_id = EXCLUDED.key_id",
        (patient_id, org_id, blob, provider.key_id))


def fetch_identifiers(conn, patient_id, providers) -> dict:
    row = conn.execute("SELECT org_id, encrypted_identifiers FROM identity.patient_identity WHERE patient_id = %s", (patient_id,)).fetchone()
    if row is None:
        raise LookupError("no identity record visible for this patient")
    org_id, blob = row
    return json.loads(decrypt_field(providers, bytes(blob), _context(patient_id, org_id)))


def rotate_key(conn, patient_id, old_providers, new_provider) -> None:
    """Re-wrap the data key of one record under a new key-encryption key."""
    from .crypto import rewrap
    row = conn.execute("SELECT org_id, encrypted_identifiers FROM identity.patient_identity WHERE patient_id = %s", (patient_id,)).fetchone()
    if row is None:
        raise LookupError("no identity record visible for this patient")
    org_id, blob = row
    new_blob = rewrap(old_providers, new_provider, bytes(blob), _context(patient_id, org_id))
    conn.execute("UPDATE identity.patient_identity SET encrypted_identifiers = %s, key_id = %s WHERE patient_id = %s",
                 (new_blob, new_provider.key_id, patient_id))
