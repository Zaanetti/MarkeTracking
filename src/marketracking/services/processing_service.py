from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from marketracking.core.config import get_settings
from marketracking.parsers.sources.sefaz_sp import SefazSpNfceParser
from marketracking.schemas.nfce import (
    CaptureMethod,
    ErrorCode,
    FiscalDocumentStatus,
    ProcessingOutcome,
    ProcessingResult,
    ProcessingState,
)
from marketracking.services.consultation_url import (
    SEFAZ_SP_HOST,
    validate_consultation_url,
)
from marketracking.services.errors import ProcessingFailure
from marketracking.services.normalization_service import (
    normalize_nfce,
    validate_parsed_access_key,
)
from marketracking.services.persistence_service import (
    complete_existing_processing,
    find_failed_artifacts,
    finish_processing_failure,
    finish_step,
    mark_existing_purchase_cancelled,
    persist_authorized_purchase,
    persistence_failure,
    purchase_exists,
    record_step,
    remove_failed_artifact_record,
    start_processing,
    update_processing_input,
)
from marketracking.services.portal_client import SefazSpPortalClient
from marketracking.services.qr_service import decode_qr_bytes, inspect_image
from marketracking.services.storage_service import StorageService
from marketracking.db.models import StepStatus


logger = logging.getLogger(__name__)


def process_nfce(
    db: Session,
    *,
    url: str | None = None,
    image: bytes | None = None,
    image_size_bytes: int | None = None,
    portal_client: SefazSpPortalClient | None = None,
    parser: SefazSpNfceParser | None = None,
) -> ProcessingResult:
    processing_id = uuid4()
    capture_method = _capture_method(url, image)
    settings = get_settings()
    safe_host, fingerprint, image_digest = _initial_metadata(
        url,
        image,
        max_image_bytes=settings.image_max_bytes,
        declared_image_size=image_size_bytes,
    )
    image_size = image_size_bytes if image_size_bytes is not None else len(image or b"")
    try:
        start_processing(
            db,
            processing_id=processing_id,
            capture_method=capture_method,
            input_host=safe_host if capture_method == CaptureMethod.URL else None,
            input_fingerprint=fingerprint if capture_method == CaptureMethod.URL else None,
            image_size_bytes=image_size if capture_method == CaptureMethod.IMAGE else None,
            image_sha256=image_digest if capture_method == CaptureMethod.IMAGE else None,
        )
    except ProcessingFailure as failure:
        return _failure_result(processing_id, failure)

    portal = portal_client or SefazSpPortalClient()
    nfce_parser = parser or SefazSpNfceParser()
    active_stage = ProcessingState.VALIDATING_INPUT
    html: str | None = None
    access_key: str | None = None

    def run_stage(stage: ProcessingState, operation):
        nonlocal active_stage
        active_stage = stage
        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        step = record_step(
            db,
            processing_id,
            stage,
            1,
            StepStatus.STARTED,
            started_at=started_at,
        )
        try:
            result = operation()
        except ProcessingFailure as failure:
            finish_step(
                db,
                step,
                status=StepStatus.FAILED,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
                error_code=failure.code,
            )
            raise
        except DBAPIError as exc:
            failure = persistence_failure(exc, stage)
            finish_step(
                db,
                step,
                status=StepStatus.FAILED,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
                error_code=failure.code,
            )
            raise failure from exc
        except Exception as exc:
            failure = ProcessingFailure(
                ErrorCode.INTERNAL_ERROR,
                "O processamento da NFC-e não pôde ser concluído.",
                stage,
                technical_cause=type(exc).__name__,
            )
            finish_step(
                db,
                step,
                status=StepStatus.FAILED,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
                error_code=failure.code,
            )
            raise failure from exc
        finish_step(
            db,
            step,
            status=StepStatus.SUCCEEDED,
            finished_at=datetime.now(UTC),
            duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
        )
        return result

    try:
        run_stage(ProcessingState.VALIDATING_INPUT, lambda: _validate_capture(url, image))
        image_metadata = None
        consultation_url = url
        if image is not None:
            image_metadata = run_stage(
                ProcessingState.PROCESSING_IMAGE,
                lambda: inspect_image(
                    image,
                    max_bytes=settings.image_max_bytes,
                    max_dimension=settings.image_max_dimension,
                    max_pixels=settings.image_max_pixels,
                ),
            )
            update_processing_input(
                db,
                processing_id,
                image_media_type=image_metadata.media_type,
                image_size_bytes=image_metadata.byte_size,
                image_width=image_metadata.width,
                image_height=image_metadata.height,
                image_sha256=image_metadata.sha256,
            )
            decoded = run_stage(
                ProcessingState.DECODING_QR,
                lambda: decode_qr_bytes(
                    image,
                    max_bytes=settings.image_max_bytes,
                    max_dimension=settings.image_max_dimension,
                    max_pixels=settings.image_max_pixels,
                ),
            )
            consultation_url = decoded.url

        validated_url = run_stage(
            ProcessingState.VALIDATING_URL,
            lambda: validate_consultation_url(consultation_url or ""),
        )
        access_key = validated_url.access_key
        if capture_method == CaptureMethod.URL:
            update_processing_input(
                db,
                processing_id,
                input_host=SEFAZ_SP_HOST,
                input_access_key=access_key,
                input_fingerprint=validated_url.fingerprint,
            )

        started_attempts: dict[int, tuple[object, float]] = {}

        def on_attempt_start(attempt: int) -> None:
            started_at = datetime.now(UTC)
            started_clock = time.perf_counter()
            step = record_step(
                db,
                processing_id,
                ProcessingState.FETCHING_PORTAL,
                attempt,
                StepStatus.STARTED,
                started_at=started_at,
            )
            started_attempts[attempt] = (step, started_clock)

        def on_attempt_finish(attempt: int, failure: ProcessingFailure | None) -> None:
            step, started_clock = started_attempts[attempt]
            finish_step(
                db,
                step,
                status=StepStatus.FAILED if failure else StepStatus.SUCCEEDED,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((time.perf_counter() - started_clock) * 1000)),
                error_code=failure.code if failure else None,
            )

        html = portal.fetch(
            validated_url,
            on_attempt_start=on_attempt_start,
            on_attempt_finish=on_attempt_finish,
        )
        run_stage(ProcessingState.VALIDATING_RESPONSE, lambda: _validate_html_document(html))

        raw_document = run_stage(
            ProcessingState.EXTRACTING_DATA,
            lambda: nfce_parser.parse(html),
        )

        if raw_document.status == FiscalDocumentStatus.CANCELLED:
            run_stage(
                ProcessingState.VALIDATING_DATA,
                lambda: validate_parsed_access_key(raw_document.access_key, access_key),
            )
            exists = run_stage(
                ProcessingState.CHECKING_DUPLICATE,
                lambda: purchase_exists(db, access_key),
            )
            if not exists:
                raise ProcessingFailure(
                    ErrorCode.DOCUMENT_CANCELLED,
                    "A NFC-e consultada está cancelada e não pode gerar uma compra.",
                    ProcessingState.VALIDATING_DATA,
                )
            purchase = run_stage(
                ProcessingState.PERSISTING,
                lambda: mark_existing_purchase_cancelled(
                    db,
                    processing_id=processing_id,
                    access_key=access_key,
                ),
            )
            if purchase is None:
                raise ProcessingFailure(
                    ErrorCode.DOCUMENT_CANCELLED,
                    "A NFC-e consultada está cancelada e não pode gerar uma compra.",
                    ProcessingState.VALIDATING_DATA,
                )
            _delete_failed_artifacts(db, access_key)
            return ProcessingResult(
                processamento_id=processing_id,
                state=ProcessingState.COMPLETED,
                result=ProcessingOutcome.CANCELLED_EXISTING,
                already_processed=True,
                purchase=purchase,
            )

        purchase_data = run_stage(
            ProcessingState.VALIDATING_DATA,
            lambda: normalize_nfce(raw_document, access_key),
        )
        exists = run_stage(
            ProcessingState.CHECKING_DUPLICATE,
            lambda: purchase_exists(db, access_key),
        )
        if exists:
            purchase = run_stage(
                ProcessingState.PERSISTING,
                lambda: complete_existing_processing(
                    db,
                    processing_id=processing_id,
                    access_key=access_key,
                ),
            )
            if purchase is not None:
                _delete_failed_artifacts(db, access_key)
                return ProcessingResult(
                    processamento_id=processing_id,
                    state=ProcessingState.COMPLETED,
                    result=ProcessingOutcome.EXISTING,
                    already_processed=True,
                    purchase=purchase,
                )
            raise ProcessingFailure(
                ErrorCode.PERSISTENCE_UNAVAILABLE,
                "A compra existente não pôde ser recuperada.",
                ProcessingState.PERSISTING,
                transient=True,
                retryable=True,
            )

        persisted_purchase, already_processed = run_stage(
            ProcessingState.PERSISTING,
            lambda: persist_authorized_purchase(
                db,
                processing_id=processing_id,
                data=purchase_data,
            ),
        )
        _delete_failed_artifacts(db, access_key)
        return ProcessingResult(
            processamento_id=processing_id,
            state=ProcessingState.COMPLETED,
            result=(
                ProcessingOutcome.EXISTING if already_processed else ProcessingOutcome.CREATED
            ),
            already_processed=already_processed,
            purchase=persisted_purchase,
        )
    except ProcessingFailure as failure:
        if html is not None and failure.raw_html is None:
            failure.raw_html = html
        stored_html = None
        if failure.raw_html:
            try:
                stored_html = StorageService().store_failed_html(processing_id, failure.raw_html)
            except Exception as exc:
                logger.warning(
                    "failed_html_artifact_storage_failed",
                    extra={"processamento_id": str(processing_id), "cause": type(exc).__name__},
                )
        try:
            finish_processing_failure(
                db,
                processing_id=processing_id,
                failure=failure,
                stored_html=stored_html,
                access_key=access_key,
            )
        except ProcessingFailure as persistence_error:
            failure = persistence_error
        return _failure_result(processing_id, failure)


def _capture_method(url: str | None, image: bytes | None) -> CaptureMethod:
    if url is not None and image is None:
        return CaptureMethod.URL
    if image is not None and url is None:
        return CaptureMethod.IMAGE
    return CaptureMethod.INVALID


def _initial_metadata(
    url: str | None,
    image: bytes | None,
    *,
    max_image_bytes: int,
    declared_image_size: int | None,
) -> tuple[str | None, str | None, str | None]:
    if url is not None:
        fingerprint = hashlib.sha256(url.strip().encode("utf-8", errors="replace")).hexdigest()
        try:
            host = urlsplit(url.strip()).hostname
        except ValueError:
            host = None
        return (SEFAZ_SP_HOST if host and host.lower() == SEFAZ_SP_HOST else None, fingerprint, None)
    if image is not None:
        image_size = declared_image_size if declared_image_size is not None else len(image)
        checksum = (
            hashlib.sha256(image).hexdigest()
            if len(image) <= max_image_bytes and image_size <= max_image_bytes
            else None
        )
        return None, None, checksum
    return None, None, None


def _validate_capture(url: str | None, image: bytes | None) -> None:
    if (url is None) == (image is None):
        raise ProcessingFailure(
            ErrorCode.INVALID_INPUT,
            "Envie exatamente uma URL de consulta ou uma imagem.",
            ProcessingState.VALIDATING_INPUT,
        )
    if url is not None and not url.strip():
        raise ProcessingFailure(
            ErrorCode.INVALID_INPUT,
            "A URL de consulta não pode estar vazia.",
            ProcessingState.VALIDATING_INPUT,
        )


def _validate_html_document(html: str) -> None:
    if not html.strip() or "<html" not in html.lower():
        raise ProcessingFailure(
            ErrorCode.UNEXPECTED_PORTAL_RESPONSE,
            "O portal fiscal retornou conteúdo HTML inválido.",
            ProcessingState.VALIDATING_RESPONSE,
            raw_html=html,
        )


def _delete_failed_artifacts(db: Session, access_key: str) -> None:
    try:
        artifacts = find_failed_artifacts(db, access_key)
        if not artifacts:
            return
        storage = StorageService()
        for artifact in artifacts:
            storage.delete_failed_html(artifact.object_key)
            remove_failed_artifact_record(db, artifact)
    except Exception as exc:
        logger.warning(
            "failed_html_artifact_cleanup_failed",
            extra={"cause": type(exc).__name__},
        )


def _failure_result(processing_id: UUID, failure: ProcessingFailure) -> ProcessingResult:
    state = (
        ProcessingState.REJECTED
        if not failure.transient and failure.http_status < 500
        else ProcessingState.FAILED
    )
    outcome = (
        ProcessingOutcome.REJECTED if state == ProcessingState.REJECTED else ProcessingOutcome.FAILED
    )
    return ProcessingResult(
        processamento_id=processing_id,
        state=state,
        result=outcome,
        already_processed=False,
        error=failure.to_schema(),
    )
