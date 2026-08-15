"""Give every Digital Twin an owner.

Migration 0002 shipped one profile and one memory store for one implicit user,
identified by the string `mvp-user`. This introduces `users` and rewrites both
Digital Twin tables to point at a row in it.

**Existing data is preserved, not dropped.** A deterministic default user is
inserted and every existing profile and memory is assigned to it, so a database
that already holds a single-user Digital Twin keeps it. The default user's id
is a literal rather than a generated UUID: the migration has to be repeatable
and has to produce the same database on every deployment, and a `gen_random_uuid()`
here would make the id depend on when it ran.

The column cannot be altered in place — `mvp-user` is not a UUID and no cast
exists — so the change is add, backfill, drop, rename, which is also what
leaves a working column at every point in between.

Before touching anything, a pre-flight check counts the distinct legacy owners
in each table. Every row is about to be handed to *one* user, so two legacy
owners would collapse into one and violate the profile's unique constraint —
after the old column had already been dropped, which is a bad place to fail.
More than one owner aborts the migration with an explanation instead.

The company knowledge base is untouched. `documents.user_id` and
`document_chunks.user_id` remain the strings they were: the corpus is shared,
and only the twin is private.

**Offline mode is not supported for this revision.** `alembic upgrade --sql`
emits SQL without a database to read, and this migration reads before it
writes: the pre-flight check and the backfill are both data-dependent. Run it
online, against the database it is migrating.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed so that the same migration produces the same database everywhere. Not
# a real person: this is the owner existing single-user data is handed to.
DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_USER_NAME = "Default User"
DEFAULT_USER_EMAIL = "default@ai-shadow.local"
DEFAULT_USER_ROLE = "CEO"


# Both twin tables, checked before either is altered.
LEGACY_TABLES = ("digital_twin_profile", "digital_twin_memory")


def _assert_one_legacy_owner() -> None:
    """Refuse to run if the data cannot be handed to a single user.

    Every existing row is about to become the default user's. That is correct
    for the single-owner database 0002 produced, and wrong for any other — so
    rather than discovering it as a unique-constraint violation halfway
    through, find out first and say what to do about it.
    """

    connection = op.get_bind()

    for table in LEGACY_TABLES:
        rows, owners = connection.execute(
            sa.text(  # noqa: S608 - table names are the module's own constants
                f"SELECT count(*), count(DISTINCT user_id) FROM {table}"
            )
        ).one()

        if owners > 1:
            raise RuntimeError(
                f"{table} holds {rows} rows under {owners} different legacy "
                "owners, and this migration can only hand them to one user. "
                "It assigns every row to the deterministic default user, which "
                "would collapse those owners together and violate "
                "uq_digital_twin_profile_user. Migrate per-owner instead: "
                "create one row in `users` for each distinct legacy user_id, "
                "and backfill each row to its own owner."
            )


def upgrade() -> None:
    # Read before writing. Nothing below this line is reversible without a
    # downgrade, and the check is what keeps the failure in front of it.
    _assert_one_legacy_owner()

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # Inserted unconditionally: it is the owner of whatever 0002 left behind,
    # and on an empty database it is simply the first user, which is a
    # convenient thing for a fresh deployment to have.
    # The id is cast explicitly rather than left to Postgres to coerce from an
    # untyped literal. The coercion does work, but the cast says what is meant
    # and does not depend on how the driver binds a string.
    op.execute(
        sa.text(
            "INSERT INTO users (id, name, email, role) "
            "VALUES (CAST(:default_user_id AS uuid), :name, :email, :role)"
        ).bindparams(
            default_user_id=DEFAULT_USER_ID,
            name=DEFAULT_USER_NAME,
            email=DEFAULT_USER_EMAIL,
            role=DEFAULT_USER_ROLE,
        )
    )

    _adopt_owner("digital_twin_profile")
    _adopt_owner("digital_twin_memory")

    # Re-created after the rename, because both referred to the dropped column.
    op.create_unique_constraint(
        "uq_digital_twin_profile_user", "digital_twin_profile", ["user_id"]
    )
    op.create_index(
        "ix_digital_twin_profile_user_id", "digital_twin_profile", ["user_id"]
    )
    op.create_foreign_key(
        "fk_digital_twin_profile_user",
        "digital_twin_profile",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_digital_twin_memory_user_id", "digital_twin_memory", ["user_id"]
    )
    # The hot query: one user's active memories, most important first.
    op.create_index(
        "ix_digital_twin_memory_user_active",
        "digital_twin_memory",
        ["user_id", "active", "importance"],
    )
    op.create_foreign_key(
        "fk_digital_twin_memory_user",
        "digital_twin_memory",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def _adopt_owner(table: str) -> None:
    """Replace `table.user_id` (a string) with a UUID pointing at `users`."""

    if table == "digital_twin_profile":
        op.drop_constraint("uq_digital_twin_profile_user", table, type_="unique")
        op.drop_index("ix_digital_twin_profile_user_id", table_name=table)
    else:
        # The index on `type` alone does not mention user_id, so it survives
        # the column swap untouched.
        op.drop_index("ix_digital_twin_memory_user_active", table_name=table)
        op.drop_index("ix_digital_twin_memory_user_id", table_name=table)

    op.add_column(table, sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.execute(
        sa.text(  # noqa: S608 - table names are the module's own constants
            f"UPDATE {table} SET owner_id = CAST(:default_user_id AS uuid)"
        ).bindparams(default_user_id=DEFAULT_USER_ID)
    )
    # Nullable only for the length of the backfill above. `existing_type` is
    # not required by Postgres for a nullability change, but stating it keeps
    # the operation unambiguous on any backend.
    op.alter_column(table, "owner_id", existing_type=sa.Uuid(), nullable=False)

    op.drop_column(table, "user_id")
    op.alter_column(
        table, "owner_id", existing_type=sa.Uuid(), new_column_name="user_id"
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_digital_twin_memory_user", "digital_twin_memory", type_="foreignkey"
    )
    op.drop_index(
        "ix_digital_twin_memory_user_active", table_name="digital_twin_memory"
    )
    op.drop_index("ix_digital_twin_memory_user_id", table_name="digital_twin_memory")

    op.drop_constraint(
        "fk_digital_twin_profile_user", "digital_twin_profile", type_="foreignkey"
    )
    op.drop_index("ix_digital_twin_profile_user_id", table_name="digital_twin_profile")
    op.drop_constraint(
        "uq_digital_twin_profile_user", "digital_twin_profile", type_="unique"
    )

    # Back to the single implicit owner 0002 assumed. Every twin becomes
    # `mvp-user`'s again, which is lossy when more than one user has one — the
    # only honest reversal, since the earlier schema cannot express two.
    for table in ("digital_twin_profile", "digital_twin_memory"):
        op.add_column(table, sa.Column("owner", sa.String(length=255), nullable=True))
        op.execute(f"UPDATE {table} SET owner = 'mvp-user'")  # noqa: S608
        op.alter_column(
            table, "owner", existing_type=sa.String(length=255), nullable=False
        )
        op.drop_column(table, "user_id")
        op.alter_column(
            table,
            "owner",
            existing_type=sa.String(length=255),
            new_column_name="user_id",
        )

    op.create_unique_constraint(
        "uq_digital_twin_profile_user", "digital_twin_profile", ["user_id"]
    )
    op.create_index(
        "ix_digital_twin_profile_user_id", "digital_twin_profile", ["user_id"]
    )
    op.create_index(
        "ix_digital_twin_memory_user_id", "digital_twin_memory", ["user_id"]
    )
    op.create_index(
        "ix_digital_twin_memory_user_active",
        "digital_twin_memory",
        ["user_id", "active", "importance"],
    )

    op.drop_table("users")
