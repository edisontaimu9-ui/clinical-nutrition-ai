#!/usr/bin/env python3
"""Minimal, dependency-light migration runner.

Applies db/migrations/*.sql in filename order, each in its own transaction, recording a SHA-256 checksum in
public.schema_migrations. Refuses to continue if an already-applied migration file has changed.

Usage: DATABASE_URL=postgresql://... python scripts/migrate.py [--dir db/migrations]

Refuses to run against any database whose name does not look like a development or test database unless
CNAI_ALLOW_ANY_DB=1 is set (guard against pointing Phase 1 tooling at the wrong server).
"""
import hashlib
import os
import sys
from pathlib import Path

import psycopg
import psycopg.conninfo

ROOT = Path(__file__).resolve().parent.parent


def apply_migrations(conninfo: str, directory: Path) -> list[str]:
    applied_now = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
            " name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0]: r[1] for r in conn.execute("SELECT name, sha256 FROM public.schema_migrations")}
        for path in sorted(directory.glob("*.sql")):
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in done:
                if done[path.name] != digest:
                    raise RuntimeError(f"migration {path.name} changed after it was applied; add a new migration instead")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO public.schema_migrations (name, sha256) VALUES (%s, %s)", (path.name, digest))
            applied_now.append(path.name)
    return applied_now


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    dbname = psycopg.conninfo.conninfo_to_dict(url).get("dbname", "")
    if not any(t in dbname for t in ("dev", "test", "ci", "synthetic")) and os.environ.get("CNAI_ALLOW_ANY_DB") != "1":
        print(f"refusing to migrate database '{dbname}': name must contain dev, test, ci or synthetic", file=sys.stderr)
        return 2
    directory = Path(sys.argv[sys.argv.index("--dir") + 1]) if "--dir" in sys.argv else ROOT / "db" / "migrations"
    for name in apply_migrations(url, directory):
        print("applied", name)
    print("migrations up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
