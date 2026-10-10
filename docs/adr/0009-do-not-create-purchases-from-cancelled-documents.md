# Do not create Purchases from cancelled Fiscal Documents

An NFC-e marked as cancelled by the consultation portal does not represent a Purchase in
MarkeTracking. Its Processing is retained as a definitive rejection, but no Fiscal Document or
Purchase is created, preventing cancelled transactions from affecting purchase and price history.

If the NFC-e was previously processed while authorized and a later Processing reports it as
cancelled, the existing Fiscal Document and Purchase are retained and marked as cancelled. The
Cancelled Purchase remains available for historical traceability but is excluded from price
tracking and purchase statistics.

The v1 does not proactively scan or revalidate persisted Fiscal Documents. A later cancellation is
discovered only when the same NFC-e is submitted and consulted again.
