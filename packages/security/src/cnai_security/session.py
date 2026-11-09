"""Per-request database session: become the right least-privilege role and set the request context.

Everything after bind() runs under row-level security. Call inside a transaction; the settings are transaction-local so
they cannot leak to the next request on a pooled connection.
"""
import psycopg
import psycopg.sql

from .permissions import DB_ROLE

_ALLOWED_DB_ROLES = frozenset(DB_ROLE.values()) | {"cnai_identity_service", "cnai_auth_service"}


def bind(conn, user_id, db_role: str, app_role: str = "", source_ip: str = "") -> None:
    if db_role not in _ALLOWED_DB_ROLES:                       # allow-list: never interpolate an arbitrary name
        raise ValueError(f"unknown database role {db_role!r}")
    if conn.autocommit:
        raise RuntimeError("bind() needs a transaction (autocommit is on); SET LOCAL would have no effect")
    conn.execute("RESET ROLE")
    conn.execute(psycopg.sql.SQL("SET LOCAL ROLE {}").format(psycopg.sql.Identifier(db_role)))
    conn.execute("SELECT set_config('app.user_id', %s, true), set_config('app.role', %s, true), set_config('app.source_ip', %s, true)",
                 (str(user_id) if user_id else "", app_role or db_role, source_ip))


def unbind(conn) -> None:
    conn.execute("RESET ROLE")
