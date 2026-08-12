"""Lambda function to create database users with appropriate privileges.

Security model: **dedicated instance**. This module owns the whole RDS instance,
so a user's privileges are granted once, at creation time, across the database
and the public schema together.

Creates two users:
- App user: DML only (SELECT, INSERT, UPDATE, DELETE)
- Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)

This reduces blast radius if the application is compromised - attackers
cannot drop tables or alter schema with the app user.

The shared vocabulary (escaping, secrets, connections, individual GRANTs) lives
in ``db_common``, copied into this bundle at apply time from
``modules/lambda-shared/db_common.py``. The *composition* of those grants stays
here: db-on-shared-rds deliberately composes them differently.

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
    """
    create_user(conn, user)

    # Grant connect
    conn.run(f"GRANT CONNECT ON DATABASE {escape_identifier(db_name)} TO {user.username}")

    # Grant schema usage
    conn.run(f"GRANT USAGE ON SCHEMA public TO {user.username}")

    # Grant DML on existing tables and sequence usage (for auto-increment columns)
    grant_dml_on_existing(conn, user.username)

    logger.info(f"Created app user '{user.username}' with DML-only privileges")


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

    # Grant connect and create (CREATE needed for CREATE EXTENSION)
    conn.run(
        f"GRANT CONNECT, CREATE ON DATABASE {escape_identifier(db_name)} TO {user.username}"
    )

    # Grant full schema privileges (DDL + DML)
    conn.run(f"GRANT ALL PRIVILEGES ON SCHEMA public TO {user.username}")

    # Grant full privileges on existing tables and sequences
    grant_all_on_existing(conn, user.username)

    logger.info(f"Created migrate user '{user.username}' with DDL + DML privileges")


def handle_create_users() -> dict:
    """Handle the create_users action (default behavior)."""
    creds = DbCredentials.from_environment()
    app, migrate, db_name = creds.app, creds.migrate, creds.db_name

    logger.info(
        f"Connecting to database {db_name} at {creds.master['host']}:{creds.master['port']}"
    )

    conn = connect(creds.master, db_name)

    try:
        # Create or update app user
        if user_exists(conn, app.username):
            logger.info(f"App user '{app.username}' already exists, updating password")
            update_user_password(conn, app)
        else:
            create_app_user(conn, app, db_name)

        # Create or update migrate user
        if user_exists(conn, migrate.username):
            logger.info(
                f"Migrate user '{migrate.username}' already exists, updating password"
            )
            update_user_password(conn, migrate)
        else:
            create_migrate_user(conn, migrate, db_name)

        # Ensure migrate user has CREATE on the database (for CREATE EXTENSION)
        # This is idempotent and handles existing users that were created
        # before this privilege was included in create_migrate_user()
        conn.run(
            f"GRANT CREATE ON DATABASE {escape_identifier(db_name)} TO {migrate.username}"
        )

        # Transfer ownership of existing tables/sequences to migrate user
        # This handles migration from single-user to two-account model
        transfer_ownership(conn, migrate.username)

        # Set up default privileges so tables created by migrate user
        # are automatically accessible by app user
        set_default_privileges(
            conn, creds.master["username"], migrate.username, app.username
        )

        # Grant app user access to any existing tables
        # (handles tables created before defaults were configured)
        grant_dml_on_existing(conn, app.username)

        return {
            "status": "success",
            "app_user": app.username,
            "migrate_user": migrate.username,
        }

    except Exception as e:
        logger.error(f"Error creating users: {e}")
        raise

    finally:
        conn.close()


def handler(event, context):  # pysmelly: ignore vestigial-params — context required by Lambda handler signature  (re-evaluate-by: 2026-11 review)
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
