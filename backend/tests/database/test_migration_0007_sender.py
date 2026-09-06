"""The 0007 backfill, exercised against a real database rather than reasoned about.

A data migration that is never run is a guess. This builds the *old* shape of
the column, writes the value forms that existed in it, runs the migration's own
`upgrade()` body, and asserts each row landed correctly — including the exact
string that motivated the change.

SQLite is used because the suite must run with no services (CLAUDE.md §7). The
migration branches on dialect for the string functions and this covers the
SQLite branch; the Postgres branch is the same logic in that dialect's spelling.

The migration is loaded by path because `alembic/versions` is a directory of
scripts, not an importable package.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROBERT_DISPLAY = "Robert Keenan <Robert.Keenan@sunradia.com>"
ROBERT_ADDRESS = "Robert.Keenan@sunradia.com"

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0007_assessment_sender_identity.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0007", _MIGRATION)

    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def _old_table(engine: sa.Engine) -> sa.Table:
    """The pre-0007 shape: one flattened `sender` column."""

    metadata = sa.MetaData()
    table = sa.Table(
        "email_assessment",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("sender", sa.String(512), nullable=True),
    )
    metadata.create_all(engine)

    return table


def _run_upgrade(connection: sa.Connection) -> None:
    """Run the migration body against this connection.

    `_install_proxy` is how alembic itself binds the module-level `op` used
    inside a migration script, so the script runs exactly as it would in a real
    upgrade rather than through a reimplementation of it.
    """

    migration = _load_migration()
    operations = Operations(MigrationContext.configure(connection))

    Operations._install_proxy(operations)  # noqa: SLF001 - alembic's own binding
    try:
        migration.upgrade()
    finally:
        operations._remove_proxy()  # noqa: SLF001


def test_the_backfill_splits_every_stored_form_correctly() -> None:
    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        table = _old_table(engine)
        connection.execute(
            table.insert(),
            [
                {"id": 1, "user_id": "u", "sender": ROBERT_DISPLAY},
                {"id": 2, "user_id": "u", "sender": ROBERT_ADDRESS},
                {"id": 3, "user_id": "u", "sender": None},
                {"id": 4, "user_id": "u", "sender": "  spaced@example.com  "},
            ],
        )

        _run_upgrade(connection)

        rows = {
            row.id: (row.sender_name, row.sender_address)
            for row in connection.execute(
                sa.text("SELECT id, sender_name, sender_address FROM email_assessment")
            )
        }

    # The case the whole change exists for.
    assert rows[1] == ("Robert Keenan", ROBERT_ADDRESS)
    # A bare address gains no invented name.
    assert rows[2] == (None, ROBERT_ADDRESS)
    # Nothing in, nothing out — no empty strings standing in for absence.
    assert rows[3] == (None, None)
    # Surrounding whitespace is not carried into an address.
    assert rows[4] == (None, "spaced@example.com")


def test_the_flattened_column_is_gone_afterwards() -> None:
    """The old column is dropped, so nothing can keep reading it by habit."""

    engine = sa.create_engine("sqlite://")

    with engine.begin() as connection:
        _old_table(engine)
        _run_upgrade(connection)

        columns = {
            row[1]
            for row in connection.execute(
                sa.text("PRAGMA table_info(email_assessment)")
            )
        }

    assert "sender" not in columns
    assert {"sender_name", "sender_address"} <= columns
