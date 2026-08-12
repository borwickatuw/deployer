"""Lambda function to create a database and users on a shared RDS instance.

Security model: **shared instance**. Other tenants live on the same RDS
instance, so users get CONNECT on exactly one database and nothing else at
creation time; schema and table privileges are granted in a second pass from
*inside* that database. Granting schema-wide privileges at user-creation time
(as db-users does, where the instance is dedicated) would be wrong here.

This module enables multiple applications to share a single RDS instance
while maintaining complete data isolation. Each application gets:
- Its own database (CREATE DATABASE)
- Its own users with privileges scoped to that database only

PostgreSQL's permission model ensures that users can only access the
database they're granted CONNECT on - they cannot see other databases.

Creates two users:
- App user: DML only (SELECT, INSERT, UPDATE, DELETE)
- Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)

The shared vocabulary (escaping, secrets, connections, individual GRANTs) lives
in ``db_common``, copied into this bundle at apply time from
``modules/lambda-shared/db_common.py``. The *composition* of those grants stays
here: db-users deliberately composes them differently.

"""

import json
import logging

from db_common import (
    DbCredentials,
    DbUser,
    connect,
    create_user,
    escape_identifier,
    grant_all_on_existing,
    grant_dml_on_existing,
    handle_create_extensions,
    set_default_privileges,
    transfer_ownership,
    update_user_password,
    user_exists,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def create_app_user(conn, user: DbUser, db_name: str) -> None:
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

    Table and sequence privileges are NOT granted here -- see
    ``setup_schema_privileges``, which runs against the app's own database.
    """
    create_user(conn, user)

    # Grant connect ONLY on this specific database
    conn.run(f"GRANT CONNECT ON DATABASE {escape_identifier(db_name)} TO {user.username}")

    logger.info(f"Created app user '{user.username}' with CONNECT on database '{db_name}'")


def create_migrate_user(conn, user: DbUser, db_name: str) -> None:
    """Create the migrate user with DDL + DML privileges.

    The migrate user can:
    - Everything the app user can do
    - CREATE, ALTER, DROP tables
    - Manage indexes
    - TRUNCATE tables

    This user is only used for running migrations.
    """
    create_user(conn, user)

    # Grant connect and create ONLY on this specific database
    # CREATE needed for CREATE EXTENSION in migrations
    conn.run(
        f"GRANT CONNECT, CREATE ON DATABASE {escape_identifier(db_name)} TO {user.username}"
    )

    logger.info(
        f"Created migrate user '{user.username}' with CONNECT, CREATE on database '{db_name}'"
    )


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

    # Grant DML on existing tables and sequences to app user
    grant_dml_on_existing(conn, app_username)

    # Grant full privileges on existing tables and sequences to migrate user
    grant_all_on_existing(conn, migrate_username)

    # Make future migrate-user objects accessible to the app user
    set_default_privileges(conn, master_username, migrate_username, app_username)

    logger.info(
        f"Set up schema privileges: {migrate_username} owns, {app_username} has DML access"
    )


# pysmelly: ignore dict-as-dataclass — Lambda handler return must be dict for JSON serialization  (re-evaluate-by: 2026-11 review)
def handle_setup_database() -> dict:
    """Handle the setup_database action (default behavior)."""
    creds = DbCredentials.from_environment()
    app, migrate, db_name = creds.app, creds.migrate, creds.db_name

    logger.info(
        f"Setting up database '{db_name}' on shared RDS "
        f"at {creds.master['host']}:{creds.master['port']}"
    )

    # Step 1: Connect to 'postgres' database to create database and users
    # (We can't connect to a database that doesn't exist yet)
    conn_admin = connect(creds.master, "postgres")

    db_created = False
    try:
        # Create database if it doesn't exist
        result = conn_admin.run(
            "SELECT 1 FROM pg_database WHERE datname = :db_name", db_name=db_name
        )
        if result:
            logger.info(f"Database '{db_name}' already exists")
        else:
            conn_admin.run(f"CREATE DATABASE {escape_identifier(db_name)}")
            logger.info(f"Created database '{db_name}'")
            db_created = True

        # Create or update app user
        if user_exists(conn_admin, app.username):
            logger.info(f"App user '{app.username}' already exists, updating password")
            update_user_password(conn_admin, app)
        else:
            create_app_user(conn_admin, app, db_name)

        # Create or update migrate user
        if user_exists(conn_admin, migrate.username):
            logger.info(
                f"Migrate user '{migrate.username}' already exists, updating password"
            )
            update_user_password(conn_admin, migrate)
        else:
            create_migrate_user(conn_admin, migrate, db_name)

        # Ensure migrate user has CREATE on the database (for CREATE EXTENSION)
        # This is idempotent and handles existing users that were created
        # before this privilege was included in create_migrate_user()
        conn_admin.run(
            f"GRANT CREATE ON DATABASE {escape_identifier(db_name)} TO {migrate.username}"
        )

    finally:
        conn_admin.close()

    # Step 2: Connect to the application database to set up schema privileges
    logger.info(f"Connecting to database '{db_name}' to set up schema privileges")

    conn_app = connect(creds.master, db_name)

    try:
        # Transfer ownership of any existing tables to migrate user
        transfer_ownership(conn_app, migrate.username)

        # Set up schema privileges
        setup_schema_privileges(
            conn_app, creds.master["username"], migrate.username, app.username
        )

    finally:
        conn_app.close()

    return {
        "status": "success",
        "database": db_name,
        "database_created": db_created,
        "app_user": app.username,
        "migrate_user": migrate.username,
    }


def handler(event, context):  # pysmelly: ignore vestigial-params — context required by Lambda handler signature  (re-evaluate-by: 2026-11 review)
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
