from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# Importing the models package registers every table on Base.metadata, which
# is what makes `alembic revision --autogenerate` see them.
import app.models  # noqa: F401
from alembic import context
from app.config.settings import settings
from app.database.base import Base

config = context.config

# `settings.DATABASE_URL` is the default, not an override. A caller that has
# already put a URL on the config — `command.upgrade` against a throwaway
# database in `tests/database/test_migration_fidelity.py`, or `alembic -x` from
# a shell — means it, and clobbering it here would silently migrate the
# development database instead. `alembic.ini` deliberately omits the key, so
# the ordinary path is unchanged.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

#: Resolved once, so offline and online mode cannot disagree about which
#: database they are describing.
database_url = config.get_main_option("sqlalchemy.url")

if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which switches off every
    # logger that already exists rather than only reconfiguring the ones this
    # file names. In a migration run from the command line nothing else has
    # been created yet, so the default is harmless and invisible. In-process it
    # is neither: the application's own loggers are already built, and running
    # a migration silently mutes them for the rest of the process.
    #
    # The test suite is where that showed up — a scheduler test asserting on a
    # logged warning passed alone and failed after the migration tests had run,
    # but only on a machine with Postgres reachable, because that is the only
    # place those tests do anything.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
