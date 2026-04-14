"""Lambda function to create database users with appropriate privileges.

Creates two users:
- App user: DML only (SELECT, INSERT, UPDATE, DELETE)
- Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)

This reduces blast radius if the application is compromised - attackers
cannot drop tables or alter schema with the app user.

# pysmelly: ignore duplicate-blocks — create_app_user and create_migrate_user share
# structure but have intentionally different GRANT statements (DML vs ALL).
# Security-critical privilege grants must be explicit for auditability.
"""

import json
import os
import logging

import boto3
import pg8000.native

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def escape_literal(value: str) -> str:
    """Escape a string for use as a PostgreSQL literal.

    PostgreSQL DDL statements (CREATE USER, ALTER USER) don't support
    parameterized queries, so we must escape values manually.
    """
    # Replace single quotes with two single quotes, wrap in quotes
    return "'" + value.replace("'", "''") + "'"


def get_secret(secret_arn: str) -> dict:
    """Retrieve a secret from Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def user_exists(conn, username: str) -> bool:
    """Check if a PostgreSQL user already exists."""
    result = conn.run(
        "SELECT 1 FROM pg_roles WHERE rolname = :username",
        username=username
    )
    return len(result) > 0


# pysmelly: ignore duplicate-blocks — different GRANT statements (DML vs ALL) for security
def create_app_user(conn, username: str, password: str, db_name: str) -> None:
    """Create the app user with DML-only privileges.

    The app user can:
    - Connect to the database
    - SELECT, INSERT, UPDATE, DELETE on all tables
    - Use sequences (for auto-increment columns)

    The app user cannot:
    - CREATE, ALTER, DROP tables
    - TRUNCATE tables
    - Manage other users
    """
    # Create user (DDL doesn't support parameters, so we escape the password)
    conn.run(f"CREATE USER {username} WITH PASSWORD {escape_literal(password)}")

    # Grant connect
    conn.run(f"GRANT CONNECT ON DATABASE {db_name} TO {username}")

    # Grant schema usage
    conn.run(f"GRANT USAGE ON SCHEMA public TO {username}")

    # Grant DML on existing tables
    conn.run(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {username}"
    )

    # Grant sequence usage (for auto-increment columns)
    conn.run(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {username}"
    )

    logger.info(f"Created app user '{username}' with DML-only privileges")


def create_migrate_user(conn, username: str, password: str, db_name: str) -> None:
    """Create the migrate user with DDL + DML privileges.

    The migrate user can:
    - Everything the app user can do
    - CREATE, ALTER, DROP tables
    - Manage indexes
    - TRUNCATE tables

    This user is only used for running migrations.
    """
    # Create user (DDL doesn't support parameters, so we escape the password)
    conn.run(f"CREATE USER {username} WITH PASSWORD {escape_literal(password)}")

    # Grant connect and create (CREATE needed for CREATE EXTENSION)
    conn.run(f"GRANT CONNECT, CREATE ON DATABASE {db_name} TO {username}")

    # Grant full schema privileges (DDL + DML)
    conn.run(f"GRANT ALL PRIVILEGES ON SCHEMA public TO {username}")

    # Grant full privileges on existing tables
    conn.run(
        f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {username}"
    )

    # Grant full privileges on sequences
    conn.run(
        f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {username}"
    )

    logger.info(f"Created migrate user '{username}' with DDL + DML privileges")


def update_user_password(conn, username: str, password: str) -> None:
    """Update an existing user's password."""
    conn.run(f"ALTER USER {username} WITH PASSWORD {escape_literal(password)}")
    logger.info(f"Updated password for user '{username}'")


def setup_default_privileges(
    conn, master_username: str, migrate_username: str, app_username: str
) -> None:
    """Set up default privileges so app user can access tables created by migrate user.

    This uses ALTER DEFAULT PRIVILEGES FOR ROLE which sets the defaults for objects
    created by the migrate user, not the current user (master).

    Requires master to be a member of migrate role (granted below).

    This is idempotent and should be run on every Lambda invocation.
    """
    # Master must be a member of migrate role to set its default privileges
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


def grant_app_permissions(conn, app_username: str) -> None:
    """Grant app user permissions on all existing tables and sequences.

    This handles tables that may have been created before default privileges
    were properly configured. Idempotent - safe to run multiple times.
    """
    conn.run(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_username}"
    )
    conn.run(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_username}"
    )
    logger.info(f"Granted permissions on existing objects to {app_username}")


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
        username=migrate_username
    )

    for table_row in tables:
        tablename = table_row[0]
        old_owner = table_row[1]
        conn.run(f'ALTER TABLE public."{tablename}" OWNER TO {migrate_username}')
        logger.info(f"Transferred ownership of table '{tablename}' from '{old_owner}' to '{migrate_username}'")

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
        username=migrate_username
    )

    for seq_row in sequences:
        seqname = seq_row[0]
        old_owner = seq_row[1]
        conn.run(f'ALTER SEQUENCE public."{seqname}" OWNER TO {migrate_username}')
        logger.info(f"Transferred ownership of sequence '{seqname}' from '{old_owner}' to '{migrate_username}'")

    if sequences:
        logger.info(f"Transferred ownership of {len(sequences)} sequence(s) to '{migrate_username}'")


def create_extensions(conn, extensions: list[str]) -> None:
    """Create PostgreSQL extensions using the master (rds_superuser) connection.

    Extensions like pg_bigm require rds_superuser privileges which the migrate
    user does not have. This function runs as the master user.
    """
    for ext in extensions:
        conn.run(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
        logger.info(f"Ensured extension '{ext}' exists")


def handle_create_extensions(event) -> dict:
    """Handle the create_extensions action.

    Connects to the database as master user and creates requested extensions.
    """
    extensions = event.get("extensions", [])
    if not extensions:
        logger.info("No extensions requested")
        return {"status": "success", "extensions": []}

    master = get_secret(os.environ["MASTER_SECRET_ARN"])
    db_name = os.environ["DB_NAME"]

    logger.info(f"Creating extensions {extensions} in database {db_name}")

    conn = pg8000.native.Connection(
        host=master["host"],
        port=int(master["port"]),
        database=db_name,
        user=master["username"],
        password=master["password"],
    )

    try:
        create_extensions(conn, extensions)
        return {"status": "success", "extensions": extensions}
    except Exception as e:
        logger.error(f"Error creating extensions: {e}")
        raise
    finally:
        conn.close()


def handle_create_users() -> dict:
    """Handle the create_users action (default behavior)."""
    master = get_secret(os.environ["MASTER_SECRET_ARN"])
    app = get_secret(os.environ["APP_SECRET_ARN"])
    migrate = get_secret(os.environ["MIGRATE_SECRET_ARN"])
    db_name = os.environ["DB_NAME"]

    logger.info(f"Connecting to database {db_name} at {master['host']}:{master['port']}")

    conn = pg8000.native.Connection(
        host=master["host"],
        port=int(master["port"]),
        database=db_name,
        user=master["username"],
        password=master["password"],
    )

    try:
        # Create or update app user
        if user_exists(conn, app["username"]):
            logger.info(f"App user '{app['username']}' already exists, updating password")
            update_user_password(conn, app["username"], app["password"])
        else:
            create_app_user(conn, app["username"], app["password"], db_name)

        # Create or update migrate user
        if user_exists(conn, migrate["username"]):
            logger.info(f"Migrate user '{migrate['username']}' already exists, updating password")
            update_user_password(conn, migrate["username"], migrate["password"])
        else:
            create_migrate_user(conn, migrate["username"], migrate["password"], db_name)

        # Ensure migrate user has CREATE on the database (for CREATE EXTENSION)
        # This is idempotent and handles existing users that were created
        # before this privilege was included in create_migrate_user()
        conn.run(f"GRANT CREATE ON DATABASE {db_name} TO {migrate['username']}")

        # Transfer ownership of existing tables/sequences to migrate user
        # This handles migration from single-user to two-account model
        transfer_ownership(conn, migrate["username"])

        # Set up default privileges so tables created by migrate user
        # are automatically accessible by app user
        setup_default_privileges(
            conn, master["username"], migrate["username"], app["username"]
        )

        # Grant app user access to any existing tables
        # (handles tables created before defaults were configured)
        grant_app_permissions(conn, app["username"])

        return {
            "status": "success",
            "app_user": app["username"],
            "migrate_user": migrate["username"],
        }

    except Exception as e:
        logger.error(f"Error creating users: {e}")
        raise

    finally:
        conn.close()


def handler(event, context):  # pysmelly: ignore vestigial-params — context required by Lambda handler signature
    """Lambda handler to create database users or extensions.

    Dispatches on event["action"]:
    - "create_extensions": Create PostgreSQL extensions (requires rds_superuser)
    - "create_users" or default: Create/update database users (original behavior)

    Expects environment variables:
    - MASTER_SECRET_ARN: ARN of the master credentials secret
    - APP_SECRET_ARN: ARN of the app credentials secret (create_users only)
    - MIGRATE_SECRET_ARN: ARN of the migrate credentials secret (create_users only)
    - DB_NAME: Database name
    """
    logger.info(f"Event: {json.dumps(event)}")

    action = event.get("action", "create_users")

    if action == "create_extensions":
        return handle_create_extensions(event)
    else:
        return handle_create_users()
