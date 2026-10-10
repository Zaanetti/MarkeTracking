from __future__ import annotations

from dataclasses import dataclass, field

from marketracking.schemas.nfce import ErrorCode, ProcessingError, ProcessingState


HTTP_STATUS_BY_ERROR = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.INVALID_IMAGE: 422,
    ErrorCode.QR_NOT_FOUND: 422,
    ErrorCode.QR_UNDECODABLE: 422,
    ErrorCode.MULTIPLE_QR_CODES: 422,
    ErrorCode.INVALID_CONSULTATION_URL: 422,
    ErrorCode.UNSUPPORTED_STATE: 422,
    ErrorCode.INVALID_ACCESS_KEY: 422,
    ErrorCode.ACCESS_KEY_MISMATCH: 422,
    ErrorCode.DOCUMENT_NOT_FOUND: 404,
    ErrorCode.DOCUMENT_CANCELLED: 409,
    ErrorCode.PORTAL_BLOCKED: 503,
    ErrorCode.PORTAL_UNAVAILABLE: 503,
    ErrorCode.UNEXPECTED_PORTAL_RESPONSE: 502,
    ErrorCode.PARSER_LAYOUT_CHANGED: 502,
    ErrorCode.MISSING_REQUIRED_DATA: 422,
    ErrorCode.MONETARY_INCONSISTENCY: 422,
    ErrorCode.PERSISTENCE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}


@dataclass
class ProcessingFailure(Exception):
    code: ErrorCode
    message: str
    stage: ProcessingState
    transient: bool = False
    retryable: bool = False
    technical_cause: str | None = None
    raw_html: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS_BY_ERROR[self.code]

    def to_schema(self) -> ProcessingError:
        return ProcessingError(
            code=self.code,
            message=self.message,
            stage=self.stage,
            transient=self.transient,
            retryable=self.retryable,
            technical_cause=self.technical_cause,
        )
