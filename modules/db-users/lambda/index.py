"""Lambda function to create database users with appropriate privileges.

Creates two users:
- App user: DML only (SELECT, INSERT, UPDATE, DELETE)
- Migrate user: DDL + DML (CREATE, ALTER, DROP, etc.)

This reduces blast radius if the application is compromised - attackers
cannot drop tables or alter schema with the app user.
"""

import json
import os
import logging

import boto3
import psycopg2

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def get_secret(secret_arn: str) -> dict:
    """Retrieve a secret from Secrets Manager."""
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return json.loads(response["SecretString"])


def user_exists(cursor, username: str) -> bool:
    """Check if a PostgreSQL user already exists."""
    cursor.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s",
        (username,)
    )
    return cursor.fetchone() is not None


def create_app_user(cursor, username: str, password: str, db_name: str) -> None:
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
    # Create user
    cursor.execute(
        f"CREATE USER {username} WITH PASSWORD %s",
        (password,)
    )

    # Grant connect
    cursor.execute(f"GRANT CONNECT ON DATABASE {db_name} TO {username}")

    # Grant schema usage
    cursor.execute(f"GRANT USAGE ON SCHEMA public TO {username}")

    # Grant DML on existing tables
    cursor.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {username}"
    )

    # Grant DML on future tables (when migrate user creates them)
    cursor.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {username}"
    )

    # Grant sequence usage (for auto-increment columns)
    cursor.execute(
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {username}"
    )
    cursor.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {username}"
    )

    logger.info(f"Created app user '{username}' with DML-only privileges")


def create_migrate_user(cursor, username: str, password: str, db_name: str) -> None:
    """Create the migrate user with DDL + DML privileges.

    The migrate user can:
    - Everything the app user can do
    - CREATE, ALTER, DROP tables
    - Manage indexes
    - TRUNCATE tables

    This user is only used for running migrations.
    """
    # Create user
    cursor.execute(
        f"CREATE USER {username} WITH PASSWORD %s",
        (password,)
    )

    # Grant connect
    cursor.execute(f"GRANT CONNECT ON DATABASE {db_name} TO {username}")

    # Grant full schema privileges (DDL + DML)
    cursor.execute(f"GRANT ALL PRIVILEGES ON SCHEMA public TO {username}")

    # Grant full privileges on existing tables
    cursor.execute(
        f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {username}"
    )

    # Grant full privileges on future tables
    cursor.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT ALL PRIVILEGES ON TABLES TO {username}"
    )

    # Grant full privileges on sequences
    cursor.execute(
        f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {username}"
    )
    cursor.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT ALL PRIVILEGES ON SEQUENCES TO {username}"
    )

    logger.info(f"Created migrate user '{username}' with DDL + DML privileges")


def update_user_password(cursor, username: str, password: str) -> None:
    """Update an existing user's password."""
    cursor.execute(
        f"ALTER USER {username} WITH PASSWORD %s",
        (password,)
    )
    logger.info(f"Updated password for user '{username}'")


def handler(event, context):
    """Lambda handler to create database users.

    Expects environment variables:
    - MASTER_SECRET_ARN: ARN of the master credentials secret
    - APP_SECRET_ARN: ARN of the app credentials secret
    - MIGRATE_SECRET_ARN: ARN of the migrate credentials secret
    - DB_NAME: Database name
    """
    logger.info(f"Event: {json.dumps(event)}")

    # Get credentials from Secrets Manager
    master = get_secret(os.environ["MASTER_SECRET_ARN"])
    app = get_secret(os.environ["APP_SECRET_ARN"])
    migrate = get_secret(os.environ["MIGRATE_SECRET_ARN"])
    db_name = os.environ["DB_NAME"]

    logger.info(f"Connecting to database {db_name} at {master['host']}:{master['port']}")

    # Connect as master user
    conn = psycopg2.connect(
        host=master["host"],
        port=master["port"],
        dbname=db_name,
        user=master["username"],
        password=master["password"],
    )
    conn.autocommit = True

    try:
        cursor = conn.cursor()

        # Create or update app user
        if user_exists(cursor, app["username"]):
            logger.info(f"App user '{app['username']}' already exists, updating password")
            update_user_password(cursor, app["username"], app["password"])
        else:
            create_app_user(cursor, app["username"], app["password"], db_name)

        # Create or update migrate user
        if user_exists(cursor, migrate["username"]):
            logger.info(f"Migrate user '{migrate['username']}' already exists, updating password")
            update_user_password(cursor, migrate["username"], migrate["password"])
        else:
            create_migrate_user(cursor, migrate["username"], migrate["password"], db_name)

        cursor.close()

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
