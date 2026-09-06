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
