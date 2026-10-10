# Limit v1 to Sao Paulo NFC-e

MarkeTracking v1 will reject NFC-e inputs from Brazilian states outside Sao Paulo. Other states may
use different public portals, URL formats, page layouts, or access rules, so supporting them before
researching those flows would make the parser and validation model appear more portable than they
really are. Inputs outside the Sao Paulo URL allowlist fail during URL validation, before retrieval
or parsing. The initial production allowlist is the HTTPS QR Code consultation endpoint
`https://www.nfce.fazenda.sp.gov.br/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx?p=...`, and
the observed HTTPS shortcut `/qrcode?p=...` on the same host, which redirects to that endpoint.
Both inputs require exactly one non-empty `p` parameter; redirects must remain on the official host
and resolve to the canonical HTTPS endpoint. Other printed paths remain unsupported until their QR
payloads are verified.
