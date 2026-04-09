"""initial schema

Revision ID: 20260408_0001
Revises: None
Create Date: 2026-04-08 23:59:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260408_0001"
down_revision = None
branch_labels = None
depends_on = None


submission_input_type = postgresql.ENUM(
    "image",
    "manual_url",
    "manual_key",
    name="submission_input_type",
    create_type=False,
)
processing_status = postgresql.ENUM(
    "pending",
    "processing",
    "completed",
    "failed",
    name="processing_status",
    create_type=False,
)
reference_type = postgresql.ENUM(
    "qr_url",
    "access_key",
    "manual_url",
    "manual_key",
    name="reference_type",
    create_type=False,
)
extraction_method = postgresql.ENUM(
    "qr",
    "ocr",
    "manual",
    name="extraction_method",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    submission_input_type.create(bind, checkfirst=True)
    processing_status.create(bind, checkfirst=True)
    reference_type.create(bind, checkfirst=True)
    extraction_method.create(bind, checkfirst=True)

    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_type", submission_input_type, nullable=False),
        sa.Column(
            "status",
            processing_status,
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("file_key", sa.String(length=255), nullable=True),
        sa.Column("manual_value", sa.String(length=512), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
    )

    op.create_table(
        "document_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_type", reference_type, nullable=False),
        sa.Column("reference_value", sa.String(length=1024), nullable=False),
        sa.Column("extraction_method", extraction_method, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_document_references_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_references")),
    )
    op.create_index(
        op.f("ix_document_references_submission_id"),
        "document_references",
        ["submission_id"],
        unique=False,
    )

    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_name", sa.String(length=255), nullable=True),
        sa.Column("store_tax_id", sa.String(length=32), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subtotal_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw_html_key", sa.String(length=255), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser_name", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["reference_id"],
            ["document_references.id"],
            name=op.f("fk_receipts_reference_id_document_references"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_receipts_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipts")),
        sa.UniqueConstraint("reference_id", name=op.f("uq_receipts_reference_id")),
        sa.UniqueConstraint("submission_id", name=op.f("uq_receipts_submission_id")),
    )

    op.create_table(
        "receipt_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("product_code", sa.String(length=120), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
        sa.Column("unit_code", sa.String(length=32), nullable=True),
        sa.Column("weight_kg", sa.Numeric(10, 3), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["receipts.id"],
            name=op.f("fk_receipt_items_receipt_id_receipts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipt_items")),
        sa.UniqueConstraint(
            "receipt_id",
            "line_number",
            name="uq_receipt_items_receipt_id_line",
        ),
    )
    op.create_index(
        op.f("ix_receipt_items_receipt_id"),
        "receipt_items",
        ["receipt_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_receipt_items_receipt_id"), table_name="receipt_items")
    op.drop_table("receipt_items")

    op.drop_table("receipts")

    op.drop_index(op.f("ix_document_references_submission_id"), table_name="document_references")
    op.drop_table("document_references")

    op.drop_table("submissions")

    bind = op.get_bind()
    extraction_method.drop(bind, checkfirst=True)
    reference_type.drop(bind, checkfirst=True)
    processing_status.drop(bind, checkfirst=True)
    submission_input_type.drop(bind, checkfirst=True)
