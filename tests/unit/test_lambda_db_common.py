"""Tests for the shared Lambda module ``modules/lambda-shared/db_common.py``.

This code creates PostgreSQL users and grants them privileges; it had no test
signal at all before Phase 53a extracted it from two near-identical copies. The
assertions here are deliberately on the **SQL text** each helper emits, because
that text is the security contract -- a silently rewritten GRANT is exactly the
failure mode the twin-file duplication was hiding.

``import db_common`` resolves via ``[tool.pytest.ini_options] pythonpath``, which
lists ``modules/lambda-shared`` -- the same flat import shape the Lambda bundle
has, where the module sits next to ``index.py`` at the bundle root.
"""

import ast
import json
import logging
from pathlib import Path

import boto3
import db_common
import pytest
from db_common import (
    DbUser,
    create_user,
    escape_identifier,
    escape_literal,
    get_secret,
    grant_all_on_existing,
    grant_dml_on_existing,
    set_default_privileges,
    transfer_ownership,
    update_user_password,
    user_exists,
)

MODULES_DIR = Path(__file__).resolve().parents[2] / "modules"
TWIN_INDEXES = [
    MODULES_DIR / "db-users" / "lambda" / "index.py",
    MODULES_DIR / "db-on-shared-rds" / "lambda" / "index.py",
]


class FakeConn:
    """Record every ``conn.run(sql, **params)`` call; replay canned results.

    ``results`` maps a substring of the SQL to the rows that query should
    return, so a test can stage what ``transfer_ownership`` finds without
    modelling a query planner.
    """

    def __init__(self, results: dict[str, list] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self._results = results or {}

    def close(self) -> None:
        self.closed = True

    def run(self, sql: str, **params):
        self.calls.append((sql, params))
        for needle, rows in self._results.items():
            if needle in sql:
                return rows
        return []

    @property
    def sql(self) -> list[str]:
        """Just the SQL text of each call, in order."""
        return [sql for sql, _ in self.calls]


@pytest.fixture
def master_env(mocked_aws, monkeypatch):
    """Stage a master credentials secret plus DB_NAME, as the Lambda env provides."""
    client = boto3.client("secretsmanager", region_name="us-west-2")
    arn = client.create_secret(
        Name="deployer-test-master",
        SecretString=json.dumps(
            {
                "username": "master_user",
                "password": "master_pw",  # pragma: allowlist secret
                "host": "db.example.com",
                "port": "5432",
            }
        ),
    )["ARN"]
    monkeypatch.setenv("MASTER_SECRET_ARN", arn)
    monkeypatch.setenv("DB_NAME", "appdb")
    return arn


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hunter2", "'hunter2'"),
        ("it's", "'it''s'"),
        ("''", "''''''"),
        ('has "double"', "'has \"double\"'"),
        ("", "''"),
    ],
)
def test_escape_literal_doubles_single_quotes(value, expected):
    assert escape_literal(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("mydb", '"mydb"'),
        ("my-db", '"my-db"'),  # hyphens are a syntax error unquoted
        ("MixedCase", '"MixedCase"'),
        ('we"ird', '"we""ird"'),
        ("has'quote", '"has\'quote"'),
    ],
)
def test_escape_identifier_doubles_double_quotes(value, expected):
    assert escape_identifier(value) == expected


# ---------------------------------------------------------------------------
# DbUser
# ---------------------------------------------------------------------------


def test_db_user_from_secret_takes_only_credentials():
    user = DbUser.from_secret(
        {
            "username": "app_user",
            "password": "s3cret",  # pragma: allowlist secret
            "host": "db.example.com",
            "port": "5432",
        }
    )
    assert user == DbUser(username="app_user", password="s3cret")  # pragma: allowlist secret


def test_db_user_from_secret_requires_both_fields():
    with pytest.raises(KeyError):
        DbUser.from_secret({"username": "app_user"})


# ---------------------------------------------------------------------------
# User creation / password rotation
# ---------------------------------------------------------------------------


def test_create_user_escapes_password_and_leaves_username_bare():
    conn = FakeConn()
    create_user(conn, DbUser(username="app_user", password="it's"))

    # Username unquoted is deliberate -- see db_common.create_user.
    assert conn.sql == ["CREATE USER app_user WITH PASSWORD 'it''s'"]


def test_update_user_password_escapes_password():
    conn = FakeConn()
    update_user_password(conn, DbUser(username="app_user", password="pa'ss"))

    assert conn.sql == ["ALTER USER app_user WITH PASSWORD 'pa''ss'"]


def test_user_exists_uses_a_bound_parameter():
    conn = FakeConn(results={"pg_roles": [(1,)]})
    assert user_exists(conn, "app_user") is True

    sql, params = conn.calls[0]
    assert params == {"username": "app_user"}
    # The role name must never be interpolated into the text.
    assert "app_user" not in sql


def test_user_exists_false_on_empty_result():
    assert user_exists(FakeConn(), "nobody") is False


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def _creds(db_name: str = "appdb") -> "db_common.DbCredentials":
    """A credentials bundle with no AWS behind it."""
    return db_common.DbCredentials(
        master={
            "username": "master_user",
            "password": "master_pw",  # pragma: allowlist secret
            "host": "db.example.com",
            "port": "5432",
        },
        app=DbUser("app_user", "app_pw"),  # pragma: allowlist secret
        migrate=DbUser("migrate_user", "migrate_pw"),  # pragma: allowlist secret
        db_name=db_name,
    )


def test_connect_as_master_opens_the_named_database_with_the_master_secret(monkeypatch):
    """The database is the argument, never ``creds.db_name`` -- db-on-shared-rds
    opens 'postgres' first, before its own database exists."""
    conn = FakeConn()
    seen = {}
    monkeypatch.setattr(
        db_common,
        "connect",
        lambda secret, database: seen.update(secret=secret, database=database) or conn,
    )

    assert db_common.connect_as_master(_creds(), "postgres") is conn
    assert seen["database"] == "postgres"
    assert seen["secret"]["username"] == "master_user"


def test_connect_as_master_logs_the_endpoint_it_is_about_to_open(monkeypatch, caplog):
    monkeypatch.setattr(db_common, "connect", lambda *_args: FakeConn())

    with caplog.at_level(logging.INFO, logger="db_common"):
        db_common.connect_as_master(_creds(), "appdb")

    assert "'appdb'" in caplog.text
    assert "db.example.com:5432" in caplog.text


def test_connect_as_master_leaves_the_connection_open_for_the_caller(monkeypatch):
    """Lifetime stays in the handler's try/finally; the helper only opens."""
    conn = FakeConn()
    monkeypatch.setattr(db_common, "connect", lambda *_args: conn)

    db_common.connect_as_master(_creds(), "appdb")

    assert not conn.closed


# ---------------------------------------------------------------------------
# Grant helpers
# ---------------------------------------------------------------------------


def test_grant_dml_on_existing_covers_tables_then_sequences():
    conn = FakeConn()
    grant_dml_on_existing(conn, "app_user")

    assert conn.sql == [
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user",
    ]


def test_grant_dml_on_existing_grants_no_ddl():
    conn = FakeConn()
    grant_dml_on_existing(conn, "app_user")

    joined = " ".join(conn.sql)
    assert "ALL PRIVILEGES" not in joined
    assert "TRUNCATE" not in joined


def test_grant_all_on_existing_covers_tables_then_sequences():
    conn = FakeConn()
    grant_all_on_existing(conn, "migrate_user")

    assert conn.sql == [
        "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO migrate_user",
        "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO migrate_user",
    ]


def test_set_default_privileges_grants_migrate_role_to_master_first():
    conn = FakeConn()
    set_default_privileges(conn, "master", "migrate_user", "app_user")

    # ALTER DEFAULT PRIVILEGES FOR ROLE only works once master is a member.
    assert conn.sql[0] == "GRANT migrate_user TO master"
    assert conn.sql[1:] == [
        "ALTER DEFAULT PRIVILEGES FOR ROLE migrate_user IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user",
        "ALTER DEFAULT PRIVILEGES FOR ROLE migrate_user IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO app_user",
    ]


# ---------------------------------------------------------------------------
# Ownership transfer
# ---------------------------------------------------------------------------


def test_transfer_ownership_reassigns_tables_and_sequences():
    conn = FakeConn(
        results={
            "pg_tables": [("orders", "master"), ("Weird Name", "master")],
            "pg_sequences": [("orders_id_seq", "master")],
        }
    )
    transfer_ownership(conn, "migrate_user")

    alters = [sql for sql in conn.sql if sql.startswith("ALTER")]
    assert alters == [
        'ALTER TABLE public."orders" OWNER TO migrate_user',
        'ALTER TABLE public."Weird Name" OWNER TO migrate_user',
        'ALTER SEQUENCE public."orders_id_seq" OWNER TO migrate_user',
    ]


def test_transfer_ownership_binds_the_owner_filter():
    conn = FakeConn()
    transfer_ownership(conn, "migrate_user")

    selects = [(sql, params) for sql, params in conn.calls if "SELECT" in sql]
    assert len(selects) == 2
    for sql, params in selects:
        assert params == {"username": "migrate_user"}
        assert "migrate_user" not in sql


def test_transfer_ownership_issues_no_alters_when_nothing_to_transfer():
    conn = FakeConn()
    transfer_ownership(conn, "migrate_user")

    assert not [sql for sql in conn.sql if sql.startswith("ALTER")]


# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------


def test_handle_create_extensions_quotes_each_name(monkeypatch, master_env):
    conn = FakeConn()
    monkeypatch.setattr(db_common, "connect", lambda *_args: conn)

    result = db_common.handle_create_extensions({"extensions": ["pg_bigm", "pg_trgm"]})

    assert result == {"status": "success", "extensions": ["pg_bigm", "pg_trgm"]}
    assert conn.sql == [
        'CREATE EXTENSION IF NOT EXISTS "pg_bigm"',
        'CREATE EXTENSION IF NOT EXISTS "pg_trgm"',
    ]
    assert conn.closed


def test_handle_create_extensions_closes_the_connection_on_failure(monkeypatch, master_env):
    class Boom(FakeConn):
        def run(self, sql: str, **params):
            raise RuntimeError("extension not available")

    conn = Boom()
    monkeypatch.setattr(db_common, "connect", lambda *_args: conn)

    with pytest.raises(RuntimeError, match="extension not available"):
        db_common.handle_create_extensions({"extensions": ["pg_bigm"]})

    assert conn.closed


def test_handle_create_extensions_connects_to_db_name_as_master(monkeypatch, master_env):
    """Extensions need rds_superuser, so this must run as master, not migrate."""
    seen = {}
    monkeypatch.setattr(
        db_common,
        "connect",
        lambda secret, database: seen.update(secret=secret, database=database) or FakeConn(),
    )

    db_common.handle_create_extensions({"extensions": ["pg_bigm"]})

    assert seen["database"] == "appdb"
    assert seen["secret"]["username"] == "master_user"


def test_handle_create_extensions_short_circuits_without_extensions(monkeypatch):
    # No env vars set: proof it never reaches Secrets Manager or a connection.
    monkeypatch.delenv("MASTER_SECRET_ARN", raising=False)

    assert db_common.handle_create_extensions({}) == {"status": "success", "extensions": []}


# ---------------------------------------------------------------------------
# DbCredentials -- the environment-variable contract shared by both modules
# ---------------------------------------------------------------------------


def test_from_environment_maps_each_arn_to_the_right_role(mocked_aws, monkeypatch):
    client = boto3.client("secretsmanager", region_name="us-west-2")
    for role in ("master", "app", "migrate"):
        arn = client.create_secret(
            Name=f"deployer-test-{role}",
            SecretString=json.dumps(
                {
                    "username": f"{role}_user",
                    "password": f"{role}_pw",  # pragma: allowlist secret
                    "host": "db.example.com",
                    "port": "5432",
                }
            ),
        )["ARN"]
        monkeypatch.setenv(f"{role.upper()}_SECRET_ARN", arn)
    monkeypatch.setenv("DB_NAME", "appdb")

    creds = db_common.DbCredentials.from_environment()

    assert creds.db_name == "appdb"
    assert creds.master["username"] == "master_user"
    assert creds.app == DbUser("app_user", "app_pw")  # pragma: allowlist secret
    assert creds.migrate == DbUser("migrate_user", "migrate_pw")  # pragma: allowlist secret


def test_from_environment_fails_fast_on_a_missing_variable(monkeypatch):
    monkeypatch.delenv("MASTER_SECRET_ARN", raising=False)

    with pytest.raises(KeyError, match="MASTER_SECRET_ARN"):
        db_common.DbCredentials.from_environment()


# ---------------------------------------------------------------------------
# Secrets Manager
# ---------------------------------------------------------------------------


def test_get_secret_parses_the_secret_string(mocked_aws):
    payload = {
        "username": "app_user",
        "password": "s3cret",  # pragma: allowlist secret
        "host": "db.example.com",
        "port": "5432",
    }
    client = boto3.client("secretsmanager", region_name="us-west-2")
    arn = client.create_secret(Name="deployer-test-app", SecretString=json.dumps(payload))["ARN"]

    assert get_secret(arn) == payload


def test_get_secret_raises_on_missing_secret(mocked_aws):
    client = boto3.client("secretsmanager", region_name="us-west-2")
    with pytest.raises(client.exceptions.ResourceNotFoundException):
        get_secret("deployer-test-does-not-exist")


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


def _imported_from_db_common(path: Path) -> set[str]:
    """Names each twin pulls in via ``from db_common import ...``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "db_common"
        for alias in node.names
    }


@pytest.mark.parametrize("index_path", TWIN_INDEXES, ids=lambda p: p.parent.parent.name)
def test_twin_imports_resolve_against_the_canonical_module(index_path):
    """Every name a bundle imports from db_common must actually exist there.

    Catches a half-finished extraction (renamed or dropped helper) without
    needing AWS, a database, or a built bundle.
    """
    imported = _imported_from_db_common(index_path)
    assert imported, f"{index_path} imports nothing from db_common -- did the extraction revert?"

    missing = sorted(name for name in imported if not hasattr(db_common, name))
    assert not missing, f"{index_path} imports names absent from db_common: {missing}"


@pytest.mark.parametrize("index_path", TWIN_INDEXES, ids=lambda p: p.parent.parent.name)
def test_twin_imports_stay_within_the_declared_vocabulary(index_path):
    """A twin may only import what ``db_common.__all__`` offers.

    ``__all__`` is the machine-readable form of the module docstring's
    "what belongs here" contract. Importing past it means a twin has reached
    into db_common's internals -- the first step back toward the divergence
    the extraction removed.
    """
    undeclared = sorted(_imported_from_db_common(index_path) - set(db_common.__all__))
    assert not undeclared, f"{index_path} imports db_common internals: {undeclared}"


def test_declared_vocabulary_is_all_defined():
    """Every name in ``__all__`` exists -- a rename must update the contract."""
    missing = sorted(name for name in db_common.__all__ if not hasattr(db_common, name))
    assert not missing, f"db_common.__all__ names nothing defines: {missing}"


@pytest.mark.parametrize("index_path", TWIN_INDEXES, ids=lambda p: p.parent.parent.name)
def test_twin_opens_master_connections_through_connect_as_master(index_path):
    """A twin reaching for bare ``connect`` skips the endpoint log line.

    ``connect_as_master`` exists so the announced host/port and the opened
    connection can never disagree; importing ``connect`` directly is how that
    pairing came apart in the first place (each twin logged its own line, in
    its own words, and one of db-on-shared-rds' two connections logged no
    endpoint at all).
    """
    imported = _imported_from_db_common(index_path)
    assert "connect_as_master" in imported
    assert "connect" not in imported


@pytest.mark.parametrize("index_path", TWIN_INDEXES, ids=lambda p: p.parent.parent.name)
def test_twins_do_not_redefine_shared_helpers(index_path):
    """A twin that re-declares a shared helper has started diverging again."""
    tree = ast.parse(index_path.read_text(encoding="utf-8"), filename=str(index_path))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    shared = {name for name in vars(db_common) if not name.startswith("_")}
    # The per-module security policy is *meant* to be defined in each twin.
    policy = {"create_app_user", "create_migrate_user", "handler"}

    assert not (defined & shared) - policy


@pytest.mark.parametrize("index_path", TWIN_INDEXES, ids=lambda p: p.parent.parent.name)
def test_twin_handler_takes_event_and_underscored_context(index_path):
    """The AWS runtime calls ``index.handler(event, context)`` positionally.

    Both twins ignore the context object, so it is named ``_context``: the
    underscore is the convention that says "deliberately unused" to ruff's ARG
    rules and to pysmelly's vestigial-params check, which is why neither twin
    needs a suppression comment here. Renaming it back to ``context`` would
    silently re-introduce a finding; dropping the parameter would break every
    invocation.
    """
    tree = ast.parse(index_path.read_text(encoding="utf-8"), filename=str(index_path))
    handlers = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "handler"
    ]
    assert len(handlers) == 1, f"{index_path} should define exactly one handler()"

    args = handlers[0].args
    assert not args.posonlyargs and not args.kwonlyargs
    assert [a.arg for a in args.args] == ["event", "_context"]


def test_shared_module_stays_import_light():
    """db_common ships in a bundle whose only pip deps are boto3 and pg8000."""
    tree = ast.parse(
        (MODULES_DIR / "lambda-shared" / "db_common.py").read_text(encoding="utf-8"),
        filename="db_common.py",
    )
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    stdlib = {"json", "logging", "os", "dataclasses"}
    assert roots - stdlib == {"boto3", "pg8000"}
