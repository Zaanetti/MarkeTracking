from __future__ import annotations

import logging
import random
import sqlite3
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, selectinload

from marketracking.db.models import (
    FailedHtmlArtifact,
    FiscalDocument,
    Processing,
    ProcessingStep,
    Purchase,
    PurchaseItem,
    PurchaseTotal,
    StepStatus,
    Store,
)
from marketracking.schemas.nfce import (
    CaptureMethod,
    ErrorCode,
    FiscalDocumentData,
    FiscalDocumentStatus,
    IssuedStoreDetailsData,
    ProcessingError,
    ProcessingOutcome,
    ProcessingState,
    ProcessingStepData,
    ProcessingTrace,
    PurchaseData,
    PurchaseItemData,
    PurchaseTotalData,
    StoreData,
)
from marketracking.services.errors import ProcessingFailure
from marketracking.services.storage_service import StoredHtmlArtifact


logger = logging.getLogger(__name__)
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def start_processing(
    db: Session,
    *,
    processing_id: UUID | None = None,
    capture_method: CaptureMethod,
    input_host: str | None = None,
    input_access_key: str | None = None,
    input_fingerprint: str | None = None,
    image_media_type: str | None = None,
    image_size_bytes: int | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    image_sha256: str | None = None,
) -> Processing:
    processing = Processing(
        id=processing_id or uuid4(),
        capture_method=capture_method,
        state=ProcessingState.RECEIVED,
        input_host=input_host,
        input_access_key=input_access_key,
        input_fingerprint=input_fingerprint,
        image_media_type=image_media_type,
        image_size_bytes=image_size_bytes,
        image_width=image_width,
        image_height=image_height,
        image_sha256=image_sha256,
    )
    try:
        db.add(processing)
        db.commit()
        db.refresh(processing)
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc) from exc
    return processing


def update_processing_input(
    db: Session,
    processing_id: UUID,
    **values: object,
) -> None:
    processing = db.get(Processing, processing_id)
    if processing is None:
        raise _persistence_failure(None)
    for name, value in values.items():
        if name not in {
            "input_host",
            "input_access_key",
            "input_fingerprint",
            "image_media_type",
            "image_size_bytes",
            "image_width",
            "image_height",
            "image_sha256",
        }:
            raise ValueError(f"Unsupported processing metadata field: {name}")
        setattr(processing, name, value)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc) from exc


def record_step(
    db: Session,
    processing_id: UUID,
    stage: ProcessingState,
    attempt: int,
    status: StepStatus,
    *,
    started_at: datetime,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
    error_code: ErrorCode | None = None,
    commit: bool = True,
) -> ProcessingStep:
    step = ProcessingStep(
        processing_id=processing_id,
        stage=stage,
        attempt=attempt,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        error_code=error_code.value if error_code else None,
    )
    processing = db.get(Processing, processing_id)
    if processing is None:
        raise ProcessingFailure(
            ErrorCode.PERSISTENCE_UNAVAILABLE,
            "Não foi possível registrar a execução.",
            stage,
            transient=True,
            retryable=True,
        )
    processing.state = stage
    db.add(step)
    if commit:
        try:
            db.commit()
        except DBAPIError as exc:
            db.rollback()
            raise _persistence_failure(exc, stage) from exc
    return step


def finish_step(
    db: Session,
    step: ProcessingStep,
    *,
    status: StepStatus,
    finished_at: datetime,
    duration_ms: int,
    error_code: ErrorCode | None = None,
) -> None:
    step.status = status
    step.finished_at = finished_at
    step.duration_ms = duration_ms
    step.error_code = error_code.value if error_code else None
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc, step.stage) from exc


def persist_authorized_purchase(
    db: Session,
    *,
    processing_id: UUID,
    data: PurchaseData,
) -> tuple[PurchaseData, bool]:
    key = data.fiscal_document.access_key
    for attempt in range(3):
        try:
            existing = _purchase_for_key(db, key)
            if existing is not None:
                processing = db.get(Processing, processing_id)
                if processing is None:
                    raise _persistence_failure(None)
                processing.state = ProcessingState.COMPLETED
                processing.outcome = ProcessingOutcome.EXISTING
                processing.purchase_id = existing.id
                _finish_processing(processing)
                db.commit()
                return _purchase_data(existing), True

            result = _insert_purchase(db, processing_id, data)
            db.commit()
            db.refresh(result)
            return _purchase_data(result), False
        except IntegrityError as exc:
            db.rollback()
            try:
                winner = _purchase_for_key(db, key)
            except DBAPIError as lookup_error:
                db.rollback()
                if _is_transient_db_error(lookup_error) and attempt < 2:
                    time.sleep(0.05 * (2**attempt) + random.uniform(0, 0.05))
                    continue
                raise _persistence_failure(lookup_error) from lookup_error
            if winner is not None:
                processing = db.get(Processing, processing_id)
                if processing is None:
                    raise _persistence_failure(exc) from exc
                processing.state = ProcessingState.COMPLETED
                processing.outcome = ProcessingOutcome.EXISTING
                processing.purchase_id = winner.id
                _finish_processing(processing)
                try:
                    db.commit()
                except DBAPIError as commit_error:
                    db.rollback()
                    if _is_transient_db_error(commit_error) and attempt < 2:
                        time.sleep(0.05 * (2**attempt) + random.uniform(0, 0.05))
                        continue
                    raise _persistence_failure(commit_error) from commit_error
                return _purchase_data(winner), True
            if _is_store_unique_conflict(exc) and attempt < 2:
                continue
            raise _persistence_failure(exc) from exc
        except DBAPIError as exc:
            db.rollback()
            if _is_transient_db_error(exc) and attempt < 2:
                time.sleep(0.05 * (2**attempt) + random.uniform(0, 0.05))
                continue
            raise _persistence_failure(exc) from exc
    raise AssertionError("purchase persistence retry loop exited unexpectedly")


def complete_existing_processing(
    db: Session,
    *,
    processing_id: UUID,
    access_key: str,
) -> PurchaseData | None:
    purchase = _purchase_for_key(db, access_key)
    if purchase is None:
        return None
    processing = db.get(Processing, processing_id)
    if processing is None:
        raise _persistence_failure(None)
    processing.state = ProcessingState.COMPLETED
    processing.outcome = ProcessingOutcome.EXISTING
    processing.purchase_id = purchase.id
    _finish_processing(processing)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc) from exc
    return _purchase_data(purchase)


def purchase_exists(db: Session, access_key: str) -> bool:
    return _purchase_for_key(db, access_key) is not None


def mark_existing_purchase_cancelled(
    db: Session,
    *,
    processing_id: UUID,
    access_key: str,
) -> PurchaseData | None:
    document = db.scalar(
        select(FiscalDocument)
        .where(FiscalDocument.access_key == access_key)
        .options(
            selectinload(FiscalDocument.store),
            selectinload(FiscalDocument.purchase)
            .selectinload(Purchase.items),
            selectinload(FiscalDocument.purchase)
            .selectinload(Purchase.total),
            selectinload(FiscalDocument.purchase)
            .selectinload(Purchase.store),
        )
    )
    if document is None:
        return None
    if document.purchase is None:
        raise _persistence_failure(None)

    document.status = FiscalDocumentStatus.CANCELLED
    document.purchase.status = FiscalDocumentStatus.CANCELLED
    processing = db.get(Processing, processing_id)
    if processing is None:
        raise _persistence_failure(None)
    processing.state = ProcessingState.COMPLETED
    processing.outcome = ProcessingOutcome.CANCELLED_EXISTING
    processing.purchase_id = document.purchase.id
    _finish_processing(processing)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc) from exc
    return _purchase_data(document.purchase)


def finish_processing_failure(
    db: Session,
    *,
    processing_id: UUID,
    failure: ProcessingFailure,
    stored_html: StoredHtmlArtifact | None = None,
    access_key: str | None = None,
) -> None:
    processing = db.get(Processing, processing_id)
    if processing is None:
        return
    processing.state = (
        ProcessingState.REJECTED
        if not failure.transient and failure.http_status < 500
        else ProcessingState.FAILED
    )
    processing.outcome = (
        ProcessingOutcome.REJECTED
        if processing.state == ProcessingState.REJECTED
        else ProcessingOutcome.FAILED
    )
    processing.error_code = failure.code.value
    processing.error_stage = failure.stage
    processing.error_message = failure.message
    processing.error_transient = failure.transient
    processing.error_retryable = failure.retryable
    processing.error_technical_cause = failure.technical_cause
    _finish_processing(processing)

    if stored_html is not None:
        artifact = FailedHtmlArtifact(
            processing_id=processing_id,
            object_key=stored_html.object_key,
            sha256=stored_html.sha256,
            access_key=access_key or processing.input_access_key,
            expires_at=stored_html.expires_at,
        )
        db.add(artifact)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise _persistence_failure(exc, failure.stage) from exc


def get_processing_trace(db: Session, processing_id: UUID) -> ProcessingTrace | None:
    processing = db.scalar(
        select(Processing)
        .where(Processing.id == processing_id)
        .options(selectinload(Processing.steps))
    )
    if processing is None:
        return None
    metadata: dict[str, object] = {}
    if processing.capture_method == CaptureMethod.URL:
        metadata = {
            "host": processing.input_host,
            "access_key": processing.input_access_key,
            "fingerprint": processing.input_fingerprint,
        }
    elif processing.capture_method == CaptureMethod.IMAGE:
        metadata = {
            "media_type": processing.image_media_type,
            "size_bytes": processing.image_size_bytes,
            "width": processing.image_width,
            "height": processing.image_height,
            "sha256": processing.image_sha256,
        }
    error = None
    if processing.error_code:
        try:
            code = ErrorCode(processing.error_code)
        except ValueError:
            code = ErrorCode.INTERNAL_ERROR
        error = ProcessingError(
            code=code,
            message=processing.error_message or "A execução não foi concluída.",
            stage=processing.error_stage or processing.state,
            transient=bool(processing.error_transient),
            retryable=bool(processing.error_retryable),
            technical_cause=processing.error_technical_cause,
        )
    return ProcessingTrace(
        processamento_id=processing.id,
        purchase_id=processing.purchase_id,
        capture_method=processing.capture_method,
        state=processing.state,
        result=processing.outcome,
        created_at=processing.started_at,
        finished_at=processing.finished_at,
        duration_ms=processing.duration_ms,
        input_metadata=metadata,
        steps=[
            ProcessingStepData(
                stage=step.stage,
                attempt=step.attempt,
                status=step.status.value,
                started_at=step.started_at,
                finished_at=step.finished_at,
                duration_ms=step.duration_ms,
                error_code=ErrorCode(step.error_code) if step.error_code else None,
            )
            for step in processing.steps
        ],
        error=error,
    )


def find_failed_artifacts(db: Session, access_key: str) -> list[FailedHtmlArtifact]:
    return list(
        db.scalars(
            select(FailedHtmlArtifact).where(FailedHtmlArtifact.access_key == access_key)
        ).all()
    )


def remove_failed_artifact_record(db: Session, artifact: FailedHtmlArtifact) -> None:
    db.delete(artifact)
    db.commit()


def _insert_purchase(db: Session, processing_id: UUID, data: PurchaseData) -> Purchase:
    processing = db.get(Processing, processing_id)
    if processing is None:
        raise _persistence_failure(None)
    store = db.scalar(select(Store).where(Store.cnpj == data.store.cnpj))
    if store is None:
        store = Store(
            cnpj=data.store.cnpj,
            name=data.store.name,
            address=data.store.address,
            details_as_of=data.store.details_as_of,
        )
        db.add(store)
        db.flush()
    else:
        current_date = _as_aware(store.details_as_of)
        incoming_date = _as_aware(data.store.details_as_of)
        if incoming_date > current_date:
            store.name = data.store.name
            store.address = data.store.address
            store.details_as_of = data.store.details_as_of
        elif incoming_date == current_date and (
            store.name != data.store.name or store.address != data.store.address
        ):
            logger.warning("store_details_conflict_same_issue_timestamp")

    fiscal = data.fiscal_document
    document = FiscalDocument(
        access_key=fiscal.access_key,
        store=store,
        number=fiscal.number,
        series=fiscal.series,
        issued_at=fiscal.issued_at,
        authorization_protocol=fiscal.authorization_protocol,
        authorized_at=fiscal.authorized_at,
        status=fiscal.status,
        environment=fiscal.environment,
        xml_version=fiscal.xml_version,
        xslt_version=fiscal.xslt_version,
    )
    purchase = Purchase(
        fiscal_document=document,
        store=store,
        status=fiscal.status,
        issued_store_name=data.issued_store_details.name,
        issued_store_address=data.issued_store_details.address,
        items=[
            PurchaseItem(
                line_number=item.line_number,
                store_product_code=item.store_product_code,
                description=item.description,
                quantity=item.quantity,
                commercial_unit=item.commercial_unit,
                unit_price=item.unit_price,
                line_total=item.line_total,
            )
            for item in data.items
        ],
        total=PurchaseTotal(
            declared_line_count=data.total.declared_line_count,
            gross_items_amount=data.total.gross_items_amount,
            total_discount=data.total.total_discount,
            payable_amount=data.total.payable_amount,
            total_taxes=data.total.total_taxes,
        ),
    )
    processing.state = ProcessingState.COMPLETED
    processing.outcome = ProcessingOutcome.CREATED
    _finish_processing(processing)
    db.add(purchase)
    db.flush()
    processing.purchase_id = purchase.id
    return purchase


def _purchase_for_key(db: Session, access_key: str) -> Purchase | None:
    return db.scalar(
        select(Purchase)
        .join(Purchase.fiscal_document)
        .where(FiscalDocument.access_key == access_key)
        .options(
            selectinload(Purchase.fiscal_document),
            selectinload(Purchase.store),
            selectinload(Purchase.items),
            selectinload(Purchase.total),
        )
    )


def _purchase_data(purchase: Purchase) -> PurchaseData:
    document = purchase.fiscal_document
    store = purchase.store
    if document is None or store is None or purchase.total is None:
        raise _persistence_failure(None)
    return PurchaseData(
        purchase_id=purchase.id,
        fiscal_document=FiscalDocumentData(
            fiscal_document_id=document.id,
            access_key=document.access_key,
            number=document.number,
            series=document.series,
            issued_at=_as_aware(document.issued_at),
            authorization_protocol=document.authorization_protocol,
            authorized_at=_as_aware(document.authorized_at),
            status=document.status,
            environment=document.environment,
            xml_version=document.xml_version,
            xslt_version=document.xslt_version,
        ),
        store=StoreData(
            cnpj=store.cnpj,
            name=store.name,
            address=store.address,
            details_as_of=_as_aware(store.details_as_of),
        ),
        issued_store_details=IssuedStoreDetailsData(
            name=purchase.issued_store_name,
            address=purchase.issued_store_address,
        ),
        items=[
            PurchaseItemData(
                item_id=item.id,
                line_number=item.line_number,
                store_product_code=item.store_product_code,
                description=item.description,
                quantity=item.quantity,
                commercial_unit=item.commercial_unit,
                unit_price=item.unit_price,
                line_total=item.line_total,
            )
            for item in purchase.items
        ],
        total=PurchaseTotalData(
            declared_line_count=purchase.total.declared_line_count,
            gross_items_amount=purchase.total.gross_items_amount,
            total_discount=purchase.total.total_discount,
            payable_amount=purchase.total.payable_amount,
            total_taxes=purchase.total.total_taxes,
        ),
    )


def _finish_processing(processing: Processing) -> None:
    processing.finished_at = datetime.now(UTC)
    started_at = _as_aware(processing.started_at)
    processing.duration_ms = max(0, int((processing.finished_at - started_at).total_seconds() * 1000))


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=SAO_PAULO)


def persistence_failure(
    exc: Exception | None,
    stage: ProcessingState = ProcessingState.PERSISTING,
) -> ProcessingFailure:
    return _persistence_failure(exc, stage)


def _persistence_failure(
    exc: Exception | None,
    stage: ProcessingState = ProcessingState.PERSISTING,
) -> ProcessingFailure:
    transient = isinstance(exc, DBAPIError) and _is_transient_db_error(exc)
    return ProcessingFailure(
        ErrorCode.PERSISTENCE_UNAVAILABLE,
        "Não foi possível persistir o resultado da NFC-e.",
        stage,
        transient=transient,
        retryable=transient,
        technical_cause=type(exc).__name__ if exc else None,
    )


def _is_transient_db_error(exc: DBAPIError) -> bool:
    if exc.connection_invalidated:
        return True
    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if isinstance(sqlstate, str) and (
        sqlstate.startswith("08") or sqlstate.startswith("40") or sqlstate in {"53300", "57P01"}
    ):
        return True
    return isinstance(original, sqlite3.OperationalError) and any(
        word in str(original).lower() for word in ("locked", "busy", "temporarily unavailable")
    )


def _is_store_unique_conflict(exc: IntegrityError) -> bool:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_stores_cnpj":
        return True
    message = str(exc.orig).lower()
    return "unique constraint failed: stores.cnpj" in message
