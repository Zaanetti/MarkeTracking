"""Initial NFC-e processing schema.

Revision ID: 20260408_0001
Revises: None
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260408_0001"
down_revision = None
branch_labels = None
depends_on = None


capture_method = postgresql.ENUM("image", "url", "invalid", name="capture_method", create_type=False)
processing_state = postgresql.ENUM(
    "RECEBIDO",
    "VALIDANDO_ENTRADA",
    "PROCESSANDO_IMAGEM",
    "DECODIFICANDO_QRCODE",
    "VALIDANDO_URL",
    "CONSULTANDO_PORTAL",
    "VALIDANDO_RESPOSTA",
    "EXTRAINDO_DADOS",
    "VALIDANDO_DADOS",
    "VERIFICANDO_DUPLICIDADE",
    "PERSISTINDO",
    "CONCLUIDO",
    "REJEITADO",
    "FALHA",
    name="processing_state",
    create_type=False,
)
processing_outcome = postgresql.ENUM(
    "created", "existing", "cancelled_existing", "rejected", "failed",
    name="processing_outcome", create_type=False
)
fiscal_document_status = postgresql.ENUM(
    "authorized", "cancelled", name="fiscal_document_status", create_type=False
)
processing_step_status = postgresql.ENUM(
    "started", "succeeded", "failed", name="processing_step_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        capture_method,
        processing_state,
        processing_outcome,
        fiscal_document_status,
        processing_step_status,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "processing_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capture_method", capture_method, nullable=False),
        sa.Column(
            "state", processing_state, nullable=False, server_default="RECEBIDO"
        ),
        sa.Column("outcome", processing_outcome, nullable=True),
        sa.Column("input_host", sa.String(255), nullable=True),
        sa.Column("input_access_key", sa.String(44), nullable=True),
        sa.Column("input_fingerprint", sa.String(64), nullable=True),
        sa.Column("image_media_type", sa.String(32), nullable=True),
        sa.Column("image_size_bytes", sa.Integer(), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        sa.Column("image_sha256", sa.String(64), nullable=True),
        sa.Column("error_stage", processing_state, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_transient", sa.Boolean(), nullable=True),
        sa.Column("error_retryable", sa.Boolean(), nullable=True),
        sa.Column("error_technical_cause", sa.String(128), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_records")),
    )

    op.create_table(
        "stores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cnpj", sa.String(14), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(512), nullable=False),
        sa.Column("details_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stores")),
        sa.UniqueConstraint("cnpj", name=op.f("uq_stores_cnpj")),
    )

    op.create_table(
        "processing_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("processing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", processing_state, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", processing_step_status, nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["processing_id"],
            ["processing_records.id"],
            name=op.f("fk_processing_steps_processing_id_processing_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processing_steps")),
        sa.UniqueConstraint(
            "processing_id", "stage", "attempt", name="uq_processing_steps_stage_attempt"
        ),
    )
    op.create_index(
        op.f("ix_processing_steps_processing_id"), "processing_steps", ["processing_id"]
    )

    op.create_table(
        "fiscal_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("access_key", sa.String(44), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("series", sa.String(16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorization_protocol", sa.String(64), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", fiscal_document_status, nullable=False, server_default="authorized"
        ),
        sa.Column("environment", sa.String(32), nullable=True),
        sa.Column("xml_version", sa.String(32), nullable=True),
        sa.Column("xslt_version", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_fiscal_documents_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fiscal_documents")),
        sa.UniqueConstraint("access_key", name=op.f("uq_fiscal_documents_access_key")),
    )
    op.create_index(op.f("ix_fiscal_documents_store_id"), "fiscal_documents", ["store_id"])

    op.create_table(
        "purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fiscal_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", fiscal_document_status, nullable=False, server_default="authorized"),
        sa.Column("issued_store_name", sa.String(255), nullable=False),
        sa.Column("issued_store_address", sa.String(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["fiscal_document_id"],
            ["fiscal_documents.id"],
            name=op.f("fk_purchases_fiscal_document_id_fiscal_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["store_id"], ["stores.id"], name=op.f("fk_purchases_store_id_stores"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchases")),
        sa.UniqueConstraint("fiscal_document_id", name=op.f("uq_purchases_fiscal_document_id")),
    )
    op.create_index(op.f("ix_purchases_store_id"), "purchases", ["store_id"])

    op.create_table(
        "purchase_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("store_product_code", sa.String(120), nullable=True),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Numeric(16, 6), nullable=False),
        sa.Column("commercial_unit", sa.String(32), nullable=False),
        sa.Column("unit_price", sa.Numeric(16, 6), nullable=False),
        sa.Column("line_total", sa.Numeric(16, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["purchases.id"],
            name=op.f("fk_purchase_items_purchase_id_purchases"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_items")),
        sa.UniqueConstraint("purchase_id", "line_number", name="uq_purchase_items_line_number"),
    )
    op.create_index(op.f("ix_purchase_items_purchase_id"), "purchase_items", ["purchase_id"])

    op.create_table(
        "purchase_totals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("declared_line_count", sa.Integer(), nullable=False),
        sa.Column("gross_items_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_discount", sa.Numeric(16, 2), nullable=False),
        sa.Column("payable_amount", sa.Numeric(16, 2), nullable=False),
        sa.Column("total_taxes", sa.Numeric(16, 2), nullable=True),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["purchases.id"],
            name=op.f("fk_purchase_totals_purchase_id_purchases"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_totals")),
        sa.UniqueConstraint("purchase_id", name=op.f("uq_purchase_totals_purchase_id")),
    )

    op.create_table(
        "failed_html_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("processing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("access_key", sa.String(44), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_id"], ["processing_records.id"],
            name=op.f("fk_failed_html_artifacts_processing_id_processing_records"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failed_html_artifacts")),
        sa.UniqueConstraint("object_key", name=op.f("uq_failed_html_artifacts_object_key")),
        sa.UniqueConstraint("processing_id", name=op.f("uq_failed_html_artifacts_processing_id")),
    )


def downgrade() -> None:
    op.drop_table("failed_html_artifacts")
    op.drop_table("purchase_totals")
    op.drop_index(op.f("ix_purchase_items_purchase_id"), table_name="purchase_items")
    op.drop_table("purchase_items")
    op.drop_index(op.f("ix_purchases_store_id"), table_name="purchases")
    op.drop_table("purchases")
    op.drop_index(op.f("ix_fiscal_documents_store_id"), table_name="fiscal_documents")
    op.drop_table("fiscal_documents")
    op.drop_index(op.f("ix_processing_steps_processing_id"), table_name="processing_steps")
    op.drop_table("processing_steps")
    op.drop_table("stores")
    op.drop_table("processing_records")

    bind = op.get_bind()
    for enum in (
        processing_step_status,
        fiscal_document_status,
        processing_outcome,
        processing_state,
        capture_method,
    ):
        enum.drop(bind, checkfirst=True)
