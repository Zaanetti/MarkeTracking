from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID as PyUUID, uuid4

from sqlalchemy import DateTime, Enum as SqlEnum, Float, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketracking.db.base import Base


def sql_enum(enum_cls: type[Enum], name: str) -> SqlEnum:
    return SqlEnum(
        enum_cls,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


class SubmissionInputType(str, Enum):
    IMAGE = "image"
    MANUAL_URL = "manual_url"
    MANUAL_KEY = "manual_key"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ReferenceType(str, Enum):
    QR_URL = "qr_url"
    ACCESS_KEY = "access_key"
    MANUAL_URL = "manual_url"
    MANUAL_KEY = "manual_key"


class ExtractionMethod(str, Enum):
    QR = "qr"
    OCR = "ocr"
    MANUAL = "manual"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Submission(TimestampMixin, Base):
    __tablename__ = "submissions"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    input_type: Mapped[SubmissionInputType] = mapped_column(
        sql_enum(SubmissionInputType, name="submission_input_type"),
        nullable=False,
    )
    status: Mapped[ProcessingStatus] = mapped_column(
        sql_enum(ProcessingStatus, name="processing_status"),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
    )
    file_key: Mapped[str | None] = mapped_column(String(255))
    manual_value: Mapped[str | None] = mapped_column(String(512))
    error_message: Mapped[str | None] = mapped_column(Text)

    references: Mapped[list[DocumentReference]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
    )
    receipt: Mapped[Receipt | None] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DocumentReference(TimestampMixin, Base):
    __tablename__ = "document_references"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    submission_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference_type: Mapped[ReferenceType] = mapped_column(
        sql_enum(ReferenceType, name="reference_type"),
        nullable=False,
    )
    reference_value: Mapped[str] = mapped_column(String(1024), nullable=False)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        sql_enum(ExtractionMethod, name="extraction_method"),
        nullable=False,
    )
    confidence: Mapped[float | None] = mapped_column(Float)

    submission: Mapped[Submission] = relationship(back_populates="references")
    receipt: Mapped[Receipt | None] = relationship(back_populates="reference", uselist=False)


class Receipt(TimestampMixin, Base):
    __tablename__ = "receipts"

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    submission_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    reference_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_references.id", ondelete="SET NULL"),
        unique=True,
    )
    store_name: Mapped[str | None] = mapped_column(String(255))
    store_tax_id: Mapped[str | None] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subtotal_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    raw_html_key: Mapped[str | None] = mapped_column(String(255))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parser_name: Mapped[str | None] = mapped_column(String(120))

    submission: Mapped[Submission] = relationship(back_populates="receipt")
    reference: Mapped[DocumentReference | None] = relationship(back_populates="receipt")
    items: Mapped[list[ReceiptItem]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
    )


class ReceiptItem(TimestampMixin, Base):
    __tablename__ = "receipt_items"
    __table_args__ = (
        UniqueConstraint("receipt_id", "line_number", name="uq_receipt_items_receipt_id_line"),
    )

    id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    receipt_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    product_code: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit_code: Mapped[str | None] = mapped_column(String(32))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    receipt: Mapped[Receipt] = relationship(back_populates="items")
