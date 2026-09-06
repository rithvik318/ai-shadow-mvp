"""Split the assessment's flattened sender into name and address.

`email_assessment.sender` held `str(EmailAddress)` — "Robert Keenan
<Robert.Keenan@sunradia.com>". Every consumer that needed a recipient had to
parse a display string back into an address, and the frontend was one such
consumer. The provider has carried `EmailAddress(name, address)` all along;
this migration stops throwing that structure away at the point of storage.

The backfill splits existing rows on the trailing `<...>` because that is the
exact shape `EmailAddress.__str__` produces, and nothing else ever wrote this
column. A value with no angle brackets was a bare address and moves across
whole. Anything that parses to an empty address keeps the original text in
`sender_name` so that no row silently loses the only sender information it had.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "email_assessment",
        sa.Column("sender_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "email_assessment",
        sa.Column("sender_address", sa.String(length=320), nullable=True),
    )

    # "Name <addr>" -> name, addr. A bare address has no '<' and is copied
    # whole. Written as two statements rather than one CASE so the intent of
    # each is readable in a migration log.
    op.execute(
        """
        UPDATE email_assessment
           SET sender_address = TRIM(
                   SUBSTR(
                       sender,
                       INSTR(sender, '<') + 1,
                       INSTR(sender, '>') - INSTR(sender, '<') - 1
                   )
               ),
               sender_name = TRIM(SUBSTR(sender, 1, INSTR(sender, '<') - 1))
         WHERE sender IS NOT NULL
           AND INSTR(sender, '<') > 0
           AND INSTR(sender, '>') > INSTR(sender, '<')
        """
        if op.get_bind().dialect.name == "sqlite"
        else """
        UPDATE email_assessment
           SET sender_address = TRIM(
                   SUBSTRING(
                       sender FROM POSITION('<' IN sender) + 1
                              FOR POSITION('>' IN sender)
                                  - POSITION('<' IN sender) - 1
                   )
               ),
               sender_name = TRIM(
                   SUBSTRING(sender FROM 1 FOR POSITION('<' IN sender) - 1)
               )
         WHERE sender IS NOT NULL
           AND POSITION('<' IN sender) > 0
           AND POSITION('>' IN sender) > POSITION('<' IN sender)
        """
    )

    op.execute(
        """
        UPDATE email_assessment
           SET sender_address = TRIM(sender)
         WHERE sender IS NOT NULL
           AND sender_address IS NULL
           AND TRIM(sender) <> ''
        """
    )

    # A name that ended up empty is not a name. Keeping '' would make
    # "has a display name" false-positive everywhere downstream.
    op.execute("UPDATE email_assessment SET sender_name = NULL WHERE sender_name = ''")

    op.create_index(
        "ix_email_assessment_sender_address",
        "email_assessment",
        ["user_id", "sender_address"],
    )

    op.drop_column("email_assessment", "sender")


def downgrade() -> None:
    op.add_column(
        "email_assessment", sa.Column("sender", sa.String(length=512), nullable=True)
    )

    op.execute(
        """
        UPDATE email_assessment
           SET sender = CASE
                   WHEN sender_name IS NOT NULL AND sender_address IS NOT NULL
                       THEN sender_name || ' <' || sender_address || '>'
                   ELSE COALESCE(sender_address, sender_name)
               END
        """
    )

    op.drop_index("ix_email_assessment_sender_address", table_name="email_assessment")
    op.drop_column("email_assessment", "sender_address")
    op.drop_column("email_assessment", "sender_name")
