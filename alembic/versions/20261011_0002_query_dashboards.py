"""Add query indexes and link successful processings to purchases.

Revision ID: 20261011_0002
Revises: 20260408_0001
Create Date: 2026-10-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261011_0002"
down_revision = "20260408_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "processing_records",
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_processing_records_purchase_id_purchases",
        "processing_records",
        "purchases",
        ["purchase_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Only exact NFC-e access-key matches can be backfilled. Historical image runs
    # without a stored key intentionally remain unlinked.
    op.execute(
        sa.text(
            """
            UPDATE processing_records AS pr
            SET purchase_id = p.id
            FROM fiscal_documents AS fd
            JOIN purchases AS p ON p.fiscal_document_id = fd.id
            WHERE pr.input_access_key = fd.access_key
              AND pr.state = 'CONCLUIDO'
              AND pr.outcome IN ('created', 'existing', 'cancelled_existing')
            """
        )
    )
    op.create_index(
        "ix_processing_records_purchase_id", "processing_records", ["purchase_id"]
    )
    op.create_index(
        "ix_processing_records_created_at_id", "processing_records", ["created_at", "id"]
    )
    op.create_index("ix_fiscal_documents_issued_at_id", "fiscal_documents", ["issued_at", "id"])
    op.create_index(
        "ix_purchase_items_code_unit_purchase",
        "purchase_items",
        ["store_product_code", "commercial_unit", "purchase_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_purchase_items_code_unit_purchase", table_name="purchase_items")
    op.drop_index("ix_fiscal_documents_issued_at_id", table_name="fiscal_documents")
    op.drop_index("ix_processing_records_created_at_id", table_name="processing_records")
    op.drop_index("ix_processing_records_purchase_id", table_name="processing_records")
    op.drop_constraint(
        "fk_processing_records_purchase_id_purchases", "processing_records", type_="foreignkey"
    )
    op.drop_column("processing_records", "purchase_id")
