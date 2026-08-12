"""Shared PostgreSQL user/privilege vocabulary for the db-* Lambda bundles.

This is the single tracked copy. Each module's ``null_resource.lambda_dependencies``
copies it to ``<module>/lambda/db_common.py`` at apply time, alongside the
pip-installed dependencies, so it lands in ``data.archive_file.lambda``'s zip and
``import db_common`` resolves from the bundle root. Those copies are build
artifacts and are gitignored -- edit this file, never a copy.

Keep this module import-light: **boto3 and pg8000 only**. Anything else has to be
added to every consuming module's ``lambda/requirements.txt``.

What belongs here: the shared vocabulary -- escaping, secret retrieval,
connection construction, and the individual GRANT statements. What does *not*
belong here: the composition of those grants into a user-creation policy. The two
consumers encode deliberately different security models (dedicated instance vs.
shared instance) and each keeps its own ``create_*_user`` / ``setup_*`` functions.
Merging those is how a shared-RDS tenant silently gains access it should not have.
"""

import json
import logging
import os
from dataclasses import dataclass

import boto3
import pg8000.native

logger = logging.getLogger()
logger.setLevel(logging.INFO)


@dataclass(frozen=True)
class DbUser:
    """A database role and the password to create or rotate it with."""

    username: str
    password: str

    @classmethod
    def from_secret(cls, secret: dict) -> "DbUser":
        """Build from a Secrets Manager credentials payload."""
        return cls(username=secret["username"], password=secret["password"])


@dataclass(frozen=True)
class DbCredentials:
    """Everything a db-* handler needs before it can open a connection.

    The environment-variable contract is identical in both modules -- both
    ``main.tf`` files set the same four variables on the Lambda -- so it lives
    here. Reading the wrong ARN into the wrong role is precisely the drift the
    twin-file duplication used to invite.

    ``master`` stays a raw secret dict because it carries the connection
    endpoint (host/port) as well as credentials; ``app`` and ``migrate`` are
    only ever created or rotated.
    """

    master: dict
    app: DbUser
    migrate: DbUser
    db_name: str

    @classmethod
    def from_environment(cls) -> "DbCredentials":
        """Resolve the three secrets and the database name from the environment."""
        return cls(
            master=get_secret(os.environ["MASTER_SECRET_ARN"]),
            app=DbUser.from_secret(get_secret(os.environ["APP_SECRET_ARN"])),
            migrate=DbUser.from_secret(get_secret(os.environ["MIGRATE_SECRET_ARN"])),
            db_name=os.environ["DB_NAME"],
        )


def escape_literal(value: str) -> str:
    """Escape a string for use as a PostgreSQL literal.

    PostgreSQL DDL statements (CREATE USER, ALTER USER) don't support
    parameterized queries, so we must escape values manually.
    """
    # Replace single quotes with two single quotes, wrap in quotes
    return "'" + value.replace("'", "''") + "'"


def escape_identifier(value: str) -> str:
    """Escape a string for use as a PostgreSQL identifier (database/user name).

    Double-quote the identifier and escape any internal double quotes.
    """
    return '"' + value.replace('"', '""') + '"'


def get_secret(secret_arn: str) -> dict:
    """Retrieve a secret from Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def connect(secret: dict, database: str):
    """Open a pg8000 connection to ``database`` using a credentials secret."""
    return pg8000.native.Connection(
        host=secret["host"],
        port=int(secret["port"]),
        database=database,
        user=secret["username"],
        password=secret["password"],
    )


def user_exists(conn, username: str) -> bool:
    """Check if a PostgreSQL user already exists."""
    result = conn.run("SELECT 1 FROM pg_roles WHERE rolname = :username", username=username)
    return len(result) > 0


def create_user(conn, user: DbUser) -> None:
    """Create a role with a password.

    Callers grant privileges afterwards -- which privileges is the caller's
    security model, not this module's.
    """
    # Usernames are deliberately NOT identifier-quoted: existing roles were
    # created unquoted, so PostgreSQL folded them to lowercase. Quoting now would
    # retarget CREATE USER/GRANT on any environment whose name_prefix has
    # uppercase. See claude-meta PLAN.md Phase 53a.
    conn.run(f"CREATE USER {user.username} WITH PASSWORD {escape_literal(user.password)}")


def update_user_password(conn, user: DbUser) -> None:
    """Update an existing user's password."""
    conn.run(f"ALTER USER {user.username} WITH PASSWORD {escape_literal(user.password)}")
    logger.info(f"Updated password for user '{user.username}'")


def grant_dml_on_existing(conn, username: str) -> None:
    """Grant DML on every existing table and sequence in ``public``.

    Idempotent -- safe to run on every invocation. Covers objects created before
    default privileges were configured.
    """
    conn.run(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {username}")
    conn.run(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {username}")


def grant_all_on_existing(conn, username: str) -> None:
    """Grant full privileges on every existing table and sequence in ``public``."""
    conn.run(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {username}")
    conn.run(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {username}")


def set_default_privileges(
    conn, master_username: str, migrate_username: str, app_username: str
) -> None:
    """Make future objects created by the migrate user readable/writable by the app user.

    Uses ALTER DEFAULT PRIVILEGES FOR ROLE, which sets defaults for objects created
    by the migrate user rather than by the current user (master). That requires
    master to be a member of the migrate role, granted below.

    Idempotent -- safe to run on every invocation.
    """
    # GRANT ... TO ... is idempotent (no error if already granted)
    conn.run(f"GRANT {migrate_username} TO {master_username}")

    # Tables created by migrate user should be accessible by app user
    conn.run(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrate_username} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_username}"
    )

    # Sequences created by migrate user should be accessible by app user
    conn.run(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrate_username} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {app_username}"
    )

    logger.info(
        f"Set up default privileges: {migrate_username}'s objects grant access to {app_username}"
    )


def transfer_ownership(conn, migrate_username: str) -> None:
    """Transfer ownership of existing tables and sequences to the migrate user.

    This is needed when migrating existing databases to the two-account model.
    Tables created by the old master user need to be owned by the migrate user
    so that migrations can ALTER them.
    """
    # Get tables not owned by migrate user
    tables = conn.run(
        """
        SELECT tablename, tableowner
        FROM pg_tables
        WHERE schemaname = 'public' AND tableowner != :username
        """,
        username=migrate_username,
    )

    for table_row in tables:
        tablename = table_row[0]
        old_owner = table_row[1]
        conn.run(f'ALTER TABLE public."{tablename}" OWNER TO {migrate_username}')
        logger.info(
            f"Transferred ownership of table '{tablename}' from '{old_owner}' "
            f"to '{migrate_username}'"
        )

    if tables:
        logger.info(f"Transferred ownership of {len(tables)} table(s) to '{migrate_username}'")
    else:
        logger.info("No tables need ownership transfer")

    # Get sequences not owned by migrate user
    sequences = conn.run(
        """
        SELECT sequencename, sequenceowner
        FROM pg_sequences
        WHERE schemaname = 'public' AND sequenceowner != :username
        """,
        username=migrate_username,
    )

    for seq_row in sequences:
        seqname = seq_row[0]
        old_owner = seq_row[1]
        conn.run(f'ALTER SEQUENCE public."{seqname}" OWNER TO {migrate_username}')
        logger.info(
            f"Transferred ownership of sequence '{seqname}' from '{old_owner}' "
            f"to '{migrate_username}'"
        )

    if sequences:
        logger.info(
            f"Transferred ownership of {len(sequences)} sequence(s) to '{migrate_username}'"
        )


def handle_create_extensions(event) -> dict:
    """Handle the create_extensions action.

    Connects to the database named by DB_NAME **as the master user** and creates
    the requested extensions: extensions like pg_bigm require rds_superuser,
    which the migrate user deliberately does not have.

    Identical in both modules -- creating an extension is not part of either
    module's user-privilege policy.
    """
    extensions = event.get("extensions", [])
    if not extensions:
        logger.info("No extensions requested")
        return {"status": "success", "extensions": []}

    master = get_secret(os.environ["MASTER_SECRET_ARN"])
    db_name = os.environ["DB_NAME"]

    logger.info(f"Creating extensions {extensions} in database {db_name}")

    conn = connect(master, db_name)

    try:
        for ext in extensions:
            conn.run(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
            logger.info(f"Ensured extension '{ext}' exists")
        return {"status": "success", "extensions": extensions}
    except Exception as e:
        logger.error(f"Error creating extensions: {e}")
        raise
    finally:
        conn.close()
