"""Does the migrated schema actually match the models?

The question this module exists to answer, and the reason it has to run against
PostgreSQL: **SQLite ignores `VARCHAR` lengths.** The rest of the suite creates
its tables with `create_all`, which by construction matches the models, so a
migration that has drifted from them is invisible everywhere else. That is not
hypothetical — it shipped twice.

`sa.Enum(..., native_enum=False)` renders as `VARCHAR(n)` sized to the longest
member *when the migration was written*. Migration 0011 added `in_progress` to
the task vocabulary without widening the column, so pressing Start returned a
500 on every PostgreSQL deployment while the whole suite stayed green.
`documents.status` had the same defect for `unsupported`. Both are fixed in
0013; this module is what stops the next vocabulary change from repeating it.

Skipped when no database is reachable, so `pytest` stays green with nothing
running. To include it:

    docker compose up -d
    pytest -m postgres
"""

import logging
import uuid
from collections.abc import Iterator

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from alembic import command
from app.config.settings import settings
from app.database.base import Base

pytestmark = pytest.mark.postgres

# Resolved at import, before any fixture runs a migration. `fileConfig` with
# `disable_existing_loggers=True` only silences loggers that already exist, so
# resolving these inside the test would create them afterwards and assert
# nothing at all.
_WATCHED_LOGGERS = [
    logging.getLogger(name)
    for name in ("app.main", "app.services.features.sync.onedrive_sync_service")
]


def _enum_members(column) -> list[str] | None:
    """The vocabulary behind a column, when it has one."""

    enum = getattr(column.type, "enum_class", None)

    if enum is None:
        return None

    return [member.value for member in enum]


@pytest.fixture
def migrated() -> Iterator[Engine]:
    """A throwaway database with every migration applied, in order.

    A separate database rather than a schema: Alembic's version table and
    `op.create_index` calls are not schema-qualified, so running the real
    migrations inside a temporary schema of the development database would
    write to it.
    """

    admin = create_engine(settings.DATABASE_URL, isolation_level="AUTOCOMMIT")

    try:
        connection = admin.connect()
    except SQLAlchemyError as exc:
        admin.dispose()
        pytest.skip(f"no Postgres reachable at DATABASE_URL: {exc.__class__.__name__}")

    name = f"migration_fidelity_{uuid.uuid4().hex[:8]}"

    try:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    except SQLAlchemyError as exc:
        connection.close()
        admin.dispose()
        pytest.skip(f"cannot create a test database: {exc.__class__.__name__}")

    url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/" + name
    engine = create_engine(url)

    try:
        with engine.begin() as setup:
            has_pgvector = setup.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
            ).first()

            if has_pgvector is None:
                pytest.skip("the 'vector' extension is not available")

            setup.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "head")

        yield engine
    finally:
        engine.dispose()
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{name}'"
            )
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        connection.close()
        admin.dispose()


class TestColumnWidths:
    def test_every_string_column_is_as_wide_as_the_model_says(self, migrated: Engine):
        """The check that would have caught the Start 500 before a user did."""

        inspector = inspect(migrated)
        narrow: list[str] = []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue

            live = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }

            for column in table.columns:
                # Every type that carries a length is one PostgreSQL will
                # enforce a length on. Nothing else needs checking.
                wanted = getattr(column.type, "length", None)

                if wanted is None:
                    continue

                found = live.get(column.name)

                if found is None:
                    continue

                actual = getattr(found["type"], "length", None)

                if actual is not None and actual < wanted:
                    narrow.append(
                        f"{table.name}.{column.name}: migrated VARCHAR({actual}), "
                        f"model needs VARCHAR({wanted})"
                    )

        assert not narrow, (
            "The migrated schema is narrower than the models. On PostgreSQL "
            "these columns reject the values the application already writes, "
            "and SQLite hides it because it ignores VARCHAR lengths:\n  "
            + "\n  ".join(narrow)
        )

    def test_every_enum_column_can_hold_its_longest_member(self, migrated: Engine):
        """The same failure stated in the terms that cause it.

        A vocabulary grows, the column does not, and the longest new word
        cannot be stored. Named separately from the width check because this is
        the sentence a reader needs when the test fails.
        """

        inspector = inspect(migrated)
        problems: list[str] = []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue

            live = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }

            for column in table.columns:
                members = _enum_members(column)

                if not members:
                    continue

                found = live.get(column.name)
                actual = getattr(found["type"], "length", None) if found else None

                if actual is None:
                    continue

                too_long = [value for value in members if len(value) > actual]

                if too_long:
                    problems.append(
                        f"{table.name}.{column.name} is VARCHAR({actual}) but "
                        f"cannot hold {too_long}"
                    )

        assert not problems, "\n  ".join(["Enum columns too narrow:", *problems])


class TestStaleDefaults:
    def test_no_column_defaults_to_a_value_its_vocabulary_no_longer_has(
        self, migrated: Engine
    ):
        """`task.status` defaulted to `'pending'` long after 0011 removed it.

        A server default naming a status the application cannot read back is a
        row waiting to raise on the next select. Harmless while every insert
        supplies the column — and this codebase's do — which is exactly why it
        survived a vocabulary change unnoticed.
        """

        inspector = inspect(migrated)
        stale: list[str] = []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue

            live = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }

            for column in table.columns:
                members = _enum_members(column)
                found = live.get(column.name)

                if not members or found is None:
                    continue

                default = found.get("default")

                if not default:
                    continue

                # "'todo'::character varying" — the literal is what matters.
                literal = str(default).split("::")[0].strip().strip("'")

                if literal and literal not in members:
                    stale.append(
                        f"{table.name}.{column.name} defaults to {literal!r}, "
                        f"which is not one of {members}"
                    )

        assert not stale, "\n  ".join(["Stale enum defaults:", *stale])


class TestSingleHead:
    def test_the_migrated_database_is_at_one_head(self, migrated: Engine):
        with migrated.connect() as connection:
            heads = (
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )

        assert len(heads) == 1, f"expected one head, found {heads}"


class TestMigrationsDoNotMuteTheApplication:
    def test_running_a_migration_leaves_application_logging_intact(
        self, migrated: Engine
    ) -> None:
        """`fileConfig` defaults to `disable_existing_loggers=True`.

        From the command line that is invisible — nothing else has been built
        yet. In-process it switches off every logger the application already
        created, so a migration silently mutes the rest of the run.

        This was found as a scheduler test that passed alone and failed after
        the migration tests, but only on a machine with Postgres reachable,
        because that is the only place these tests do anything at all. The
        assertion lives here rather than there because here is where the cause
        is, and it fails the moment somebody drops the flag.

        `_WATCHED_LOGGERS` is built at module import, which is the whole point:
        `disable_existing_loggers` only disables loggers that already exist, so
        a version of this test that called `getLogger` in the body would create
        them *after* the migration and pass no matter what.
        """

        for logger in _WATCHED_LOGGERS:
            assert logger.disabled is False, (
                f"{logger.name} was disabled by running a migration"
            )


class TestIntegerWidths:
    """The same class of bug as the VARCHAR widths, one type over.

    `INTEGER` stops at 2,147,483,647. SQLite's `INTEGER` is a variable-width
    signed value up to 64 bits, so it stores far more than that without
    complaint — which is exactly why the whole suite passed while PostgreSQL
    could not write the row.
    """

    def test_every_integer_column_is_as_wide_as_the_model_says(
        self, migrated: Engine
    ) -> None:
        """The check that would have caught the 6.8 GB overflow before a sync did.

        A model declaring `BigInteger` against a migrated `INTEGER` is a column
        that rejects values the application already writes, and no SQLite test
        can see it.
        """

        inspector = inspect(migrated)
        narrow: list[str] = []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue

            live = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }

            for column in table.columns:
                wanted = type(column.type).__name__

                if wanted != "BigInteger":
                    continue

                found = live.get(column.name)

                if found is None:
                    continue

                actual = type(found["type"]).__name__

                if actual != "BIGINT":
                    narrow.append(
                        f"{table.name}.{column.name}: migrated {actual}, "
                        f"model needs BIGINT"
                    )

        assert not narrow, (
            "The migrated schema is narrower than the models. On PostgreSQL "
            "these columns raise `integer out of range` for values the "
            "application already writes:\n  " + "\n  ".join(narrow)
        )

    def test_a_file_larger_than_two_gigabytes_can_be_recorded(
        self, migrated: Engine
    ) -> None:
        """The actual row that ended the first real sync.

        `CFTC Debrief _ Microsoft Teams 2024-09-05 08-58-41.mp4`, 6,788,146,857
        bytes, in SunRadia's Knowledge Base folder. It is rejected for its size
        and recorded as rejected — the size stored is what OneDrive reported,
        not something this application accepted, so it is bounded by the drive
        rather than by `MAX_UPLOAD_SIZE_BYTES`.

        Asserted by reading the value back rather than by the insert not
        raising: a column that silently truncated would pass the weaker test.
        """

        oversized = 6_788_146_857
        document_id = uuid.uuid4()

        with migrated.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO documents (id, user_id, filename, content_type, "
                    "file_size_bytes, status, chunk_count, created_at, updated_at) "
                    "VALUES (:id, 'mvp-user', :filename, 'video/mp4', :size, "
                    "'unsupported', 0, now(), now())"
                ),
                {
                    "id": document_id,
                    "filename": (
                        "CFTC Debrief _ Microsoft Teams 2024-09-05 08-58-41.mp4"
                    ),
                    "size": oversized,
                },
            )

        with migrated.connect() as connection:
            stored = connection.execute(
                text("SELECT file_size_bytes FROM documents WHERE id = :id"),
                {"id": document_id},
            ).scalar_one()

        assert stored == oversized


class TestEnumCheckConstraints:
    """Widening the column is only half of adding an enum member.

    `sa.Enum(..., native_enum=False)` renders a `CHECK (col IN (...))`
    constraint as well, and it lists the members that existed when the
    migration was written. Add a member to the Python enum and forget the
    constraint, and PostgreSQL rejects the row with
    `violates check constraint "<name>"` — while the whole suite passes,
    because the suite builds its schema with `create_all` from the *current*
    models, whose constraint already lists the new value.

    That is precisely what happened adding `ocr_required` in migration 0015,
    and it is the third instance of the same shape: enum vocabularies drift
    from the migrated schema in a way only a real PostgreSQL can see. So this
    asks the general question rather than that one instance — every member of
    every enum column has to be insertable.
    """

    def test_every_enum_member_is_accepted_by_the_migrated_constraint(
        self, migrated: Engine
    ):
        inspector = inspect(migrated)
        rejected: list[str] = []

        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue

            stored = " ".join(
                str(check.get("sqltext", ""))
                for check in inspector.get_check_constraints(table.name)
            )

            if not stored:
                continue

            for column in table.columns:
                members = _enum_members(column)

                if members is None:
                    continue

                # Only columns this constraint actually mentions: a table may
                # carry checks about entirely unrelated columns.
                if column.name not in stored:
                    continue

                for member in members:
                    if f"'{member}'" not in stored:
                        rejected.append(
                            f"{table.name}.{column.name}: the migrated CHECK "
                            f"constraint does not allow {member!r}"
                        )

        assert not rejected, (
            "The migrated schema's CHECK constraints are narrower than the "
            "models' vocabularies. PostgreSQL rejects these values outright, "
            "and the test suite cannot see it because it builds its schema "
            "from the models rather than from the migrations:\n  "
            + "\n  ".join(rejected)
        )
