# NFC-e v1 Processing Contract

## Input and Source

`POST /api/v1/nfce/process` accepts exactly one multipart field: a `url` or an `image`. Image type
is determined from file contents; supported formats are PNG, JPEG, and WebP. Size, dimensions, and
pixel count are bounded before QR decoding. The existing Pillow preprocessing and pyzbar QR reader
are retained.

The consultation URL must use HTTPS, host `www.nfce.fazenda.sp.gov.br`, and path `/qrcode` or
`/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx`. It must contain exactly one non-empty `p`
parameter and no fragment. The first 44 digits of its decoded value are validated as a Sao Paulo
Access Key with a valid check digit. The remaining payload is opaque. The URL and every redirect
are revalidated, DNS answers must be globally routable, and the TCP connection is pinned to a
validated address while TLS uses the official hostname.

Transient timeouts, DNS/transport failures, and HTTP 500/502/503/504 receive at most three attempts
with exponential backoff and jitter. CAPTCHA and access blocking do not retry inside the same
Processing. Portal responses and HTML size are bounded.

## Pipeline

1. Record a Processing and safe input metadata.
2. Validate the one supplied input; validate an image and decode exactly one QR when needed.
3. Validate the URL and retrieve the official portal page.
4. Parse the Sao Paulo page without reading the Consumer block.
5. Validate the page Access Key against the URL key, normalize values, and check line and monetary
   consistency with a maximum difference of R$ 0.01.
6. Recheck the portal before duplicate handling. Persist a new Fiscal Document, Purchase, Store,
   historical issued-store snapshot, items, and totals in one database transaction, or return the
   existing Purchase.

Cancelled documents are rejected without creating a first Fiscal Document or Purchase. If an
existing document is later reported cancelled, its Fiscal Document and Purchase are marked
cancelled and retained for traceability.

## Data and Privacy

- A Fiscal Document is unique by its 44-digit Access Key; a Purchase is unique by
  `fiscal_document_id`.
- A Store is unique by normalized CNPJ. Each Purchase retains the name and address shown on its
  Fiscal Document, while Store contains the latest details by issue timestamp.
- Purchase Items store line number, optional Store Product Code, original description, quantity,
  commercial unit, unit price, and line total. Payment data and consumer identity are excluded.
- Purchase Totals store declared line count, gross item amount, discount, payable amount, and
  optional total taxes.
- Processing history stores safe URL metadata (host, validated key when applicable, and SHA-256
  fingerprint) or image metadata (detected media type, size, dimensions, and checksum). Full URLs,
  query strings, opaque payloads, and original image bytes are never persisted.
- Failed post-consultation HTML is stripped of consumer sections, query payloads, scripts, hidden
  inputs, and CPF patterns, encrypted with Fernet, and stored in MinIO for no more than 30 days.
  Successful reprocessing deletes prior artifacts for the same Access Key.

## API Outcomes

`201` means a Purchase was created; `200` means an existing Purchase was returned or updated to
cancelled; `422` covers invalid or inconsistent input; `404` means the document was not found;
`409` means the first consultation found a cancelled NFC-e; `502` means an incompatible portal
response; `503` means portal blocking, exhausted transient retries, or persistence unavailability.
Each response contains `processamento_id`, final state, outcome, `already_processed`, Purchase when
available, and a stable structured error when rejected or failed.
