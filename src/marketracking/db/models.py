from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketracking.db.base import Base
from marketracking.schemas.nfce import (
    CaptureMethod,
    FiscalDocumentStatus,
    ProcessingOutcome,
    ProcessingState,
)


class StepStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum(enum_type: type[StrEnum], name: str) -> SqlEnum:
    return SqlEnum(
        enum_type,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


CAPTURE_METHOD_ENUM = _enum(CaptureMethod, "capture_method")
PROCESSING_STATE_ENUM = _enum(ProcessingState, "processing_state")
PROCESSING_OUTCOME_ENUM = _enum(ProcessingOutcome, "processing_outcome")
FISCAL_DOCUMENT_STATUS_ENUM = _enum(FiscalDocumentStatus, "fiscal_document_status")
STEP_STATUS_ENUM = _enum(StepStatus, "processing_step_status")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Processing(TimestampMixin, Base):
    __tablename__ = "processing_records"
    __table_args__ = (Index("ix_processing_records_created_at_id", "created_at", "id"),)

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    capture_method: Mapped[CaptureMethod] = mapped_column(CAPTURE_METHOD_ENUM, nullable=False)
    state: Mapped[ProcessingState] = mapped_column(
        PROCESSING_STATE_ENUM,
        nullable=False,
        default=ProcessingState.RECEIVED,
        server_default=ProcessingState.RECEIVED.value,
    )
    outcome: Mapped[ProcessingOutcome | None] = mapped_column(PROCESSING_OUTCOME_ENUM)
    input_host: Mapped[str | None] = mapped_column(String(255))
    input_access_key: Mapped[str | None] = mapped_column(String(44))
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    purchase_id: Mapped[PyUUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchases.id", ondelete="SET NULL"), index=True
    )
    image_media_type: Mapped[str | None] = mapped_column(String(32))
    image_size_bytes: Mapped[int | None] = mapped_column(Integer)
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    error_stage: Mapped[ProcessingState | None] = mapped_column(PROCESSING_STATE_ENUM)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_transient: Mapped[bool | None]
    error_retryable: Mapped[bool | None]
    error_technical_cause: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    steps: Mapped[list[ProcessingStep]] = relationship(
        back_populates="processing", cascade="all, delete-orphan", order_by="ProcessingStep.id"
    )
    failed_html: Mapped[FailedHtmlArtifact | None] = relationship(
        back_populates="processing", cascade="all, delete-orphan", uselist=False
    )
    purchase: Mapped[Purchase | None] = relationship(back_populates="processings")


class ProcessingStep(Base):
    __tablename__ = "processing_steps"
    __table_args__ = (
        UniqueConstraint(
            "processing_id", "stage", "attempt", name="uq_processing_steps_stage_attempt"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    processing_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("processing_records.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[ProcessingState] = mapped_column(PROCESSING_STATE_ENUM, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(STEP_STATUS_ENUM, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))

    processing: Mapped[Processing] = relationship(back_populates="steps")


class Store(TimestampMixin, Base):
    __tablename__ = "stores"

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    cnpj: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(512), nullable=False)
    details_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    purchases: Mapped[list[Purchase]] = relationship(back_populates="store")
    fiscal_documents: Mapped[list[FiscalDocument]] = relationship(back_populates="store")


class FiscalDocument(TimestampMixin, Base):
    __tablename__ = "fiscal_documents"
    __table_args__ = (Index("ix_fiscal_documents_issued_at_id", "issued_at", "id"),)

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    access_key: Mapped[str] = mapped_column(String(44), nullable=False, unique=True)
    store_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    series: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorization_protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[FiscalDocumentStatus] = mapped_column(
        FISCAL_DOCUMENT_STATUS_ENUM,
        nullable=False,
        default=FiscalDocumentStatus.AUTHORIZED,
        server_default=FiscalDocumentStatus.AUTHORIZED.value,
    )
    environment: Mapped[str | None] = mapped_column(String(32))
    xml_version: Mapped[str | None] = mapped_column(String(32))
    xslt_version: Mapped[str | None] = mapped_column(String(32))

    store: Mapped[Store] = relationship(back_populates="fiscal_documents")
    purchase: Mapped[Purchase | None] = relationship(
        back_populates="fiscal_document", cascade="all, delete-orphan", uselist=False
    )


class Purchase(TimestampMixin, Base):
    __tablename__ = "purchases"

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    fiscal_document_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fiscal_documents.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    store_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("stores.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    status: Mapped[FiscalDocumentStatus] = mapped_column(
        FISCAL_DOCUMENT_STATUS_ENUM,
        nullable=False,
        default=FiscalDocumentStatus.AUTHORIZED,
        server_default=FiscalDocumentStatus.AUTHORIZED.value,
    )
    issued_store_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_store_address: Mapped[str] = mapped_column(String(512), nullable=False)

    fiscal_document: Mapped[FiscalDocument] = relationship(back_populates="purchase")
    store: Mapped[Store] = relationship(back_populates="purchases")
    items: Mapped[list[PurchaseItem]] = relationship(
        back_populates="purchase", cascade="all, delete-orphan", order_by="PurchaseItem.line_number"
    )
    total: Mapped[PurchaseTotal] = relationship(
        back_populates="purchase", cascade="all, delete-orphan", uselist=False
    )
    processings: Mapped[list[Processing]] = relationship(back_populates="purchase")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        UniqueConstraint("purchase_id", "line_number", name="uq_purchase_items_line_number"),
        Index(
            "ix_purchase_items_code_unit_purchase",
            "store_product_code",
            "commercial_unit",
            "purchase_id",
        ),
    )

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    store_product_code: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    commercial_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(16, 6), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)

    purchase: Mapped[Purchase] = relationship(back_populates="items")


class PurchaseTotal(Base):
    __tablename__ = "purchase_totals"

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    purchase_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    declared_line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gross_items_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    total_discount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    payable_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    total_taxes: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))

    purchase: Mapped[Purchase] = relationship(back_populates="total")


class FailedHtmlArtifact(Base):
    __tablename__ = "failed_html_artifacts"

    id: Mapped[PyUUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    processing_id: Mapped[PyUUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("processing_records.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    access_key: Mapped[str | None] = mapped_column(String(44))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    processing: Mapped[Processing] = relationship(back_populates="failed_html")
