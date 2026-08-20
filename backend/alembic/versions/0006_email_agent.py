"""Give the Email Agent somewhere to keep templates, drafts and judgements.

Four tables, all additive. Nothing existing is altered, no data is rewritten,
and every table is new — so this migration is safe to apply to a populated
database and its downgrade is a clean drop.

There is deliberately **no message table**. A mailbox is somebody else's system
of record, and copying it here would produce a mirror that silently drifts.
`email_assessment` stores what triage *concluded* about a message, keyed by the
provider and the provider's own message id, plus the subject, sender and
timestamp a person needs to recognise the row. Message bodies are not stored.

Provider identifiers are plain nullable strings with a `provider` column beside
them. That is what keeps the schema free of Microsoft Graph: an Outlook id and
a Gmail id are both just strings, and adding a second provider is neither a
migration nor a new column.

The status, category, priority and template-category columns are varchar plus a
CHECK constraint rather than a native enum, matching `document_status`,
`memory_type` and `sync_status`. Adding a value to a native Postgres enum is a
DDL statement that cannot run inside every transaction; adding one to a CHECK
constraint is an ordinary migration.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DRAFT_STATUSES = (
    "draft",
    "needs_review",
    "approved",
    "sending",
    "sent",
    "failed",
)
_CATEGORIES = ("urgent", "needs_reply", "fyi", "follow_up", "low_priority")
_PRIORITIES = ("high", "normal", "low")
_TEMPLATE_CATEGORIES = (
    "introduction",
    "follow_up",
    "meeting_request",
    "proposal_follow_up",
    "thank_you",
    "outreach",
    "custom",
)


def upgrade() -> None:
    op.create_table(
        "email_template",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                *_TEMPLATE_CATEGORIES,
                name="email_template_category",
                native_enum=False,
            ),
            nullable=False,
            server_default="custom",
        ),
        sa.Column("subject_template", sa.Text(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_email_template_user_name"),
    )
    op.create_index(
        "ix_email_template_user_id", "email_template", ["user_id"], unique=False
    )

    op.create_table(
        "email_draft",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("provider_message_id", sa.String(length=512), nullable=True),
        sa.Column("provider_thread_id", sa.String(length=512), nullable=True),
        sa.Column("provider_draft_id", sa.String(length=512), nullable=True),
        sa.Column("in_reply_to_message_id", sa.String(length=512), nullable=True),
        sa.Column("to_recipients", sa.JSON(), nullable=False),
        sa.Column("cc_recipients", sa.JSON(), nullable=False),
        sa.Column("bcc_recipients", sa.JSON(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum(*_DRAFT_STATUSES, name="email_draft_status", native_enum=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "generated_by_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("send_error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # SET NULL rather than CASCADE: deleting the template a draft started
        # from must not delete the draft. The link records provenance.
        sa.ForeignKeyConstraint(
            ["template_id"], ["email_template.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_draft_user_id", "email_draft", ["user_id"], unique=False)
    op.create_index("ix_email_draft_status", "email_draft", ["status"], unique=False)
    op.create_index(
        "ix_email_draft_user_status", "email_draft", ["user_id", "status"], unique=False
    )

    op.create_table(
        "email_attachment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["draft_id"], ["email_draft.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_attachment_draft_id", "email_attachment", ["draft_id"], unique=False
    )

    op.create_table(
        "email_assessment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_message_id", sa.String(length=512), nullable=False),
        sa.Column("provider_thread_id", sa.String(length=512), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("sender", sa.String(length=512), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "category",
            sa.Enum(*_CATEGORIES, name="email_category", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Enum(*_PRIORITIES, name="email_priority", native_enum=False),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("suggested_action", sa.Text(), nullable=True),
        sa.Column("action_items", sa.JSON(), nullable=False),
        sa.Column(
            "follow_up_recommended",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("follow_up_reason", sa.Text(), nullable=True),
        sa.Column("follow_up_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "handled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "assessed_at",
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "provider_message_id",
            name="uq_email_assessment_message",
        ),
    )
    op.create_index(
        "ix_email_assessment_user_id", "email_assessment", ["user_id"], unique=False
    )
    op.create_index(
        "ix_email_assessment_follow_up",
        "email_assessment",
        ["user_id", "follow_up_recommended", "handled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_email_assessment_follow_up", table_name="email_assessment")
    op.drop_index("ix_email_assessment_user_id", table_name="email_assessment")
    op.drop_table("email_assessment")

    op.drop_index("ix_email_attachment_draft_id", table_name="email_attachment")
    op.drop_table("email_attachment")

    op.drop_index("ix_email_draft_user_status", table_name="email_draft")
    op.drop_index("ix_email_draft_status", table_name="email_draft")
    op.drop_index("ix_email_draft_user_id", table_name="email_draft")
    op.drop_table("email_draft")

    op.drop_index("ix_email_template_user_id", table_name="email_template")
    op.drop_table("email_template")
