"""Lambda function to create a database and users on a shared RDS instance.

This module enables multiple applications to share a single RDS instance
while maintaining complete data isolation. Each application gets:
- Its own database (CREATE DATABASE)
- Its own users with privileges scoped to that database only

PostgreSQL's permission model ensures that users can only access the
database they're granted CONNECT on - they cannot see other databases.

Creates two users:
- App user: DML only (SELECT, INSERT, UPDATE, DELETE)
- Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)
"""

import json
import logging
import os

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


def _escape_identifier(value: str) -> str:
    """Escape a string for use as a PostgreSQL identifier (database/user name).

    Double-quote the identifier and escape any internal double quotes.
    """
    return '"' + value.replace('"', '""') + '"'


def get_secret(secret_arn: str) -> dict:
    """Retrieve a secret from Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def user_exists(conn, username: str) -> bool:
    """Check if a PostgreSQL user already exists."""
    result = conn.run(
        "SELECT 1 FROM pg_roles WHERE rolname = :username", username=username
    )
    return len(result) > 0


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
    - Connect to other databases (no CONNECT grant on other DBs)
    """
    # Create user (DDL doesn't support parameters, so we escape the password)
    conn.run(f"CREATE USER {username} WITH PASSWORD {escape_literal(password)}")

    # Grant connect ONLY on this specific database
    conn.run(f"GRANT CONNECT ON DATABASE {_escape_identifier(db_name)} TO {username}")

    logger.info(f"Created app user '{username}' with CONNECT on database '{db_name}'")


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

    # Grant connect and create ONLY on this specific database
    # CREATE needed for CREATE EXTENSION in migrations
    conn.run(f"GRANT CONNECT, CREATE ON DATABASE {_escape_identifier(db_name)} TO {username}")

    logger.info(
        f"Created migrate user '{username}' with CONNECT, CREATE on database '{db_name}'"
    )


def update_user_password(conn, username: str, password: str) -> None:
    """Update an existing user's password."""
    conn.run(f"ALTER USER {username} WITH PASSWORD {escape_literal(password)}")
    logger.info(f"Updated password for user '{username}'")


def setup_schema_privileges(
    conn, master_username: str, migrate_username: str, app_username: str
) -> None:
    """Set up schema and table privileges for the users.

    This function runs while connected to the application database (not postgres).
    It grants:
    - Schema usage/ownership to users
    - DML privileges to app user
    - Full privileges to migrate user
    - Default privileges so future objects are accessible
    """
    # Grant schema usage to both users
    conn.run(f"GRANT USAGE ON SCHEMA public TO {app_username}")
    conn.run(f"GRANT ALL PRIVILEGES ON SCHEMA public TO {migrate_username}")

    # Grant DML on existing tables to app user
    conn.run(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_username}"
    )

    # Grant sequence usage to app user (for auto-increment columns)
    conn.run(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_username}"
    )

    # Grant full privileges on existing tables to migrate user
    conn.run(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {migrate_username}")
    conn.run(
        f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {migrate_username}"
    )

    # Master must be a member of migrate role to set its default privileges
    # GRANT ... TO ... is idempotent (no error if already granted)
    conn.run(f"GRANT {migrate_username} TO {master_username}")

    # Set up default privileges so tables created by migrate user
    # are automatically accessible by app user
    conn.run(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrate_username} IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_username}"
    )

    conn.run(
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrate_username} IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {app_username}"
    )

    logger.info(
        f"Set up schema privileges: {migrate_username} owns, {app_username} has DML access"
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
            f"Transferred ownership of table '{tablename}' from '{old_owner}' to '{migrate_username}'"
        )

    if tables:
        logger.info(
            f"Transferred ownership of {len(tables)} table(s) to '{migrate_username}'"
        )
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
            f"Transferred ownership of sequence '{seqname}' from '{old_owner}' to '{migrate_username}'"
        )

    if sequences:
        logger.info(
            f"Transferred ownership of {len(sequences)} sequence(s) to '{migrate_username}'"
        )


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

    Connects to the application database as master user and creates
    requested extensions.
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


def handle_setup_database() -> dict:
    """Handle the setup_database action (default behavior)."""
    master = get_secret(os.environ["MASTER_SECRET_ARN"])
    app = get_secret(os.environ["APP_SECRET_ARN"])
    migrate = get_secret(os.environ["MIGRATE_SECRET_ARN"])
    db_name = os.environ["DB_NAME"]

    logger.info(
        f"Setting up database '{db_name}' on shared RDS at {master['host']}:{master['port']}"
    )

    # Step 1: Connect to 'postgres' database to create database and users
    # (We can't connect to a database that doesn't exist yet)
    conn_admin = pg8000.native.Connection(
        host=master["host"],
        port=int(master["port"]),
        database="postgres",  # Connect to default admin database
        user=master["username"],
        password=master["password"],
    )

    db_created = False
    try:
        # Create database if it doesn't exist
        result = conn_admin.run(
            "SELECT 1 FROM pg_database WHERE datname = :db_name", db_name=db_name
        )
        if result:
            logger.info(f"Database '{db_name}' already exists")
        else:
            conn_admin.run(f"CREATE DATABASE {_escape_identifier(db_name)}")
            logger.info(f"Created database '{db_name}'")
            db_created = True

        # Create or update app user
        if user_exists(conn_admin, app["username"]):
            logger.info(
                f"App user '{app['username']}' already exists, updating password"
            )
            update_user_password(conn_admin, app["username"], app["password"])
        else:
            create_app_user(conn_admin, app["username"], app["password"], db_name)

        # Create or update migrate user
        if user_exists(conn_admin, migrate["username"]):
            logger.info(
                f"Migrate user '{migrate['username']}' already exists, updating password"
            )
            update_user_password(conn_admin, migrate["username"], migrate["password"])
        else:
            create_migrate_user(
                conn_admin, migrate["username"], migrate["password"], db_name
            )

        # Ensure migrate user has CREATE on the database (for CREATE EXTENSION)
        # This is idempotent and handles existing users that were created
        # before this privilege was included in create_migrate_user()
        conn_admin.run(
            f"GRANT CREATE ON DATABASE {_escape_identifier(db_name)} TO {migrate['username']}"
        )

    finally:
        conn_admin.close()

    # Step 2: Connect to the application database to set up schema privileges
    logger.info(f"Connecting to database '{db_name}' to set up schema privileges")

    conn_app = pg8000.native.Connection(
        host=master["host"],
        port=int(master["port"]),
        database=db_name,  # Connect to the app's database
        user=master["username"],
        password=master["password"],
    )

    try:
        # Transfer ownership of any existing tables to migrate user
        transfer_ownership(conn_app, migrate["username"])

        # Set up schema privileges
        setup_schema_privileges(
            conn_app,
            master["username"],
            migrate["username"],
            app["username"],
        )

    finally:
        conn_app.close()

    return {
        "status": "success",
        "database": db_name,
        "database_created": db_created,
        "app_user": app["username"],
        "migrate_user": migrate["username"],
    }


def handler(event, context):  # pysmelly: ignore vestigial-params — context required by Lambda handler signature
    """Lambda handler to create database/users or extensions on shared RDS.

    Dispatches on event["action"]:
    - "create_extensions": Create PostgreSQL extensions (requires rds_superuser)
    - "setup_database" or default: Create database and users (original behavior)

    Expects environment variables:
    - MASTER_SECRET_ARN: ARN of the master credentials secret
    - APP_SECRET_ARN: ARN of the app credentials secret (setup_database only)
    - MIGRATE_SECRET_ARN: ARN of the migrate credentials secret (setup_database only)
    - DB_NAME: Database name
    """
    logger.info(f"Event: {json.dumps(event)}")

    action = event.get("action", "setup_database")

    if action == "create_extensions":
        return handle_create_extensions(event)
    else:
        return handle_setup_database()
