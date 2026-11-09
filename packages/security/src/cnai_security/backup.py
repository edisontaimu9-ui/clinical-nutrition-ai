"""Encrypted backup and restore drill (NFR-SEC-02, NFR-REL-01).

A drill proves a backup is usable, not merely present: dump, encrypt, delete the plaintext, decrypt, restore into a fresh
database, then compare row counts, migration checksums, policies and the audit chain with the source, and time it against
the recovery targets. Needs pg_dump and pg_restore on PATH. Synthetic or throwaway databases only: the drill database
name must contain test, dev, ci, synthetic or drill.
"""
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import psycopg
import psycopg.sql
import yaml
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from . import audit
from .crypto import decrypt_file, encrypt_file

SAFE_NAME_PARTS = ("test", "dev", "ci", "synthetic", "drill")
SCHEMAS = ("iam", "identity", "clinical", "ops", "audit")


class DrillError(RuntimeError):
    pass


@dataclass
class DrillReport:
    ok: bool
    seconds: float
    rpo_minutes: Optional[float]
    problems: List[str] = field(default_factory=list)
    tables_compared: int = 0
    audit_events: int = 0
    encrypted_backup_bytes: int = 0
    targets: dict = field(default_factory=dict)


def have_tools() -> bool:
    return bool(shutil.which("pg_dump") and shutil.which("pg_restore"))


def _safe(name: str) -> bool:
    return any(p in name for p in SAFE_NAME_PARTS)


def _fingerprint(conn) -> dict:
    tables = conn.execute("SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                          "WHERE c.relkind = 'r' AND n.nspname = ANY(%s) ORDER BY 1, 2", (list(SCHEMAS),)).fetchall()
    counts = {f"{s}.{t}": conn.execute(psycopg.sql.SQL("SELECT count(*) FROM {}.{}").format(
        psycopg.sql.Identifier(s), psycopg.sql.Identifier(t))).fetchone()[0] for s, t in tables}
    return {
        "counts": counts,
        "migrations": conn.execute("SELECT name, sha256 FROM public.schema_migrations ORDER BY name").fetchall()
        if conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] else [],
        "policies": conn.execute("SELECT count(*) FROM pg_policies WHERE schemaname = ANY(%s)", (list(SCHEMAS),)).fetchone()[0],
        "rls_tables": conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                   "WHERE c.relrowsecurity AND n.nspname = ANY(%s)", (list(SCHEMAS),)).fetchone()[0],
        "head": audit.verify_chain(conn),
    }


def run_restore_drill(source_url: str, provider, *, drill_db: str, targets_file: Optional[str] = None,
                      workdir: Optional[str] = None) -> DrillReport:
    if not have_tools():
        raise DrillError("pg_dump and pg_restore are required")
    src_name = conninfo_to_dict(source_url).get("dbname", "")
    if not _safe(drill_db) or not _safe(src_name):
        raise DrillError("the drill refuses databases whose names do not contain test, dev, ci, synthetic or drill")
    if drill_db == src_name:
        raise DrillError("the drill database must differ from the source")
    targets = yaml.safe_load(Path(targets_file).read_text()) if targets_file else {}
    own_tmp = workdir is None
    work = Path(workdir or tempfile.mkdtemp(prefix="cnai-drill-"))
    started = time.monotonic()
    problems: List[str] = []
    admin_url = make_conninfo(source_url, dbname="postgres")
    drill_url = make_conninfo(source_url, dbname=drill_db)
    try:
        with psycopg.connect(source_url) as src:
            before = _fingerprint(src)
            newest = src.execute("SELECT max(occurred_at) FROM audit.event").fetchone()[0]
        dump_started = time.time()
        plain, enc, restored = work / "db.dump", work / "db.dump.enc", work / "db.restored"
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(plain), source_url], check=True, capture_output=True)
        encrypt_file(provider, str(plain), str(enc), context=b"backup:" + src_name.encode())
        plain.unlink()                                          # the plaintext dump must not outlive encryption
        size = enc.stat().st_size
        decrypt_file(provider, str(enc), str(restored), context=b"backup:" + src_name.encode())
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(psycopg.sql.Identifier(drill_db)))
            admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(drill_db)))
        r = subprocess.run(["pg_restore", "--no-owner", "--dbname", drill_url, str(restored)], capture_output=True, text=True)
        if r.returncode != 0:
            problems.append(f"pg_restore reported errors: {r.stderr.strip()[:300]}")
        with psycopg.connect(drill_url) as dst:
            after = _fingerprint(dst)
        if before["counts"] != after["counts"]:
            diff = sorted(k for k in set(before["counts"]) | set(after["counts"]) if before["counts"].get(k) != after["counts"].get(k))
            problems.append(f"row counts differ in: {', '.join(diff)}")
        if before["migrations"] != after["migrations"]:
            problems.append("migration checksums differ")
        if before["policies"] != after["policies"] or before["rls_tables"] != after["rls_tables"]:
            problems.append("row-level security policies were not restored")
        if not after["head"].ok:
            problems.append(f"restored audit chain is broken at event {after['head'].bad_event_id}")
        elif after["head"].head_hash != before["head"].head_hash:
            problems.append("restored audit chain head differs from the source")
        seconds = time.monotonic() - started
        rpo = None
        if newest is not None:
            rpo = max(0.0, (dump_started - newest.timestamp()) / 60.0)
        if targets.get("rto_minutes") is not None and seconds / 60.0 > targets["rto_minutes"]:
            problems.append(f"restore took {seconds / 60:.1f} min, over the {targets['rto_minutes']} min target")
        return DrillReport(not problems, seconds, rpo, problems, len(after["counts"]), after["head"].checked, size, targets)
    finally:
        try:
            with psycopg.connect(admin_url, autocommit=True) as admin:
                admin.execute(psycopg.sql.SQL("DROP DATABASE IF EXISTS {}").format(psycopg.sql.Identifier(drill_db)))
        except psycopg.Error:
            pass
        if own_tmp:
            shutil.rmtree(work, ignore_errors=True)
