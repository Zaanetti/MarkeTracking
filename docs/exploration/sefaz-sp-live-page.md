# SEFAZ-SP live page exploration

Exploration date: 2026-10-10. Three receipt photos were inspected. The original receipt images and
the fetched HTML were not copied into project documentation.

## QR Code and URL

The existing Pillow/pyzbar reader decoded the QR from photo 1. On a second pass, ZXing-C++ decoded
photo 3 after 2x upscaling and local contrast enhancement; the QR in photo 2 remained unreadable
after scaling, thresholding, rotation/inversion attempts, and geometric detection.

The decoded URL used HTTPS on `www.nfce.fazenda.sp.gov.br`, path `/qrcode`, and one `p` parameter.
The portal returned HTTP 302 to
`/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx`, followed by HTTP 200 HTML on the same host. The
`p` value had 91 characters: its first 44 were a valid Sao Paulo access key with a valid check digit
and matched the key displayed in the page; the remaining 47 characters were kept opaque and are not
included here. Photo 3's decoded QR used the canonical
`/NFCeConsultaPublica/Paginas/ConsultaQRCode.aspx` path directly and returned HTTP 200. Although
the printed receipt text shows `/NFCeConsultaPublica/Paginas/ConsultaPublica.aspx`, that text is
not the decoded QR target. Photo 2's payload remains unverified, so `ConsultaPublica.aspx` is not
confirmed as an accepted input.

## Fields observed

The live page exposes these groups:

- Store header: name, CNPJ, and address.
- General document details: issue type, document number and series, issue date/time, authorization
  protocol and date/time, consumer-view type, environment, XML version, and XSLT version.
- Fiscal-document access key.
- Consumer identification. This section is intentionally ignored by MarkeTracking.
- Taxpayer-provided additional information. Keep out of the normalized v1 model unless a specific
  field is later approved.
- Purchase item table: line, store-local product code, description, quantity, unit, unit price, and
  line total.
- Totals: declared line count, gross item amount, discount, amount payable, payment method and
  amounts, other amount, change, and total taxes. Payment, other amount, and change are outside the
  v1 model.

In the inspected page, the declared line count matched the number of table rows, while the sum of
item quantities was larger. The declared count therefore represents lines, not units.

## Legacy parser findings

- The store selectors `#u20` and `#conteudo .txtCenter > div.text` were present.
- The item selectors `#tabResult` and spans `txtTit`, `RCod`, `Rqtd`, `RUN`, `RvlUnit`, and `valor`
  were present, and the existing parser extracted all rows in this sample.
- The existing parser requires the `RCod` span before accepting a row, although the approved model
  makes Store Product Code optional. A missing local code must not cause an otherwise complete line
  to be dropped.
- The section lookup searches `#infos [data-role='collapsible']`; the current page uses
  `.jqm-collapsible`, so `find_section_content()` fails before extracting the general and access-key
  sections.
- The environment is present in the general-information section but is omitted from the legacy
  parser result. Its issue-type parser recognizes only `EMISSÃO NORMAL`, so it cannot classify a
  cancellation. The first photo's printed phrase "NÃO É DOCUMENTO FISCAL" coexists with an official
  page showing normal issuance, authorization protocol, and the matching access key; fiscal status
  must come from the SEFAZ page.
- The total parser recognizes the line count, amount payable, and taxes, but does not return the
  displayed gross item amount or discount. Its fallback also places gross amount, discount, other
  amount, and change into the payment list, misclassifying them as payments. Payment rows themselves
  are outside the v1 domain.
- The legacy collector extracts consumer text into its returned payload. That field must be removed
  from the new normalized flow.
