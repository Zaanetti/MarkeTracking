# Keep processing history with unique fiscal documents

MarkeTracking will retain detailed Processing history for traceability while keeping Fiscal
Documents unique by Access Key. This preserves diagnostic and user-facing history without allowing
repeated submissions of the same NFC-e to create duplicate Fiscal Documents or Purchases. When a
repeated submission resolves to an existing Fiscal Document, it still consults the portal to detect
a later cancellation and then returns or updates the existing Purchase.

Processing metadata is retained without automatic expiration in v1, but it excludes original image
bytes, complete Consultation URLs, query strings, and opaque QR payloads. Failed HTML is governed by
its separate short-term retention policy.
