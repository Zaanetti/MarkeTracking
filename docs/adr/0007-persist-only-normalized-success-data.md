# Persist only normalized success data

Successful MarkeTracking Processings will persist only normalized Fiscal Document, Purchase, Store,
Issued Store Details, Purchase Item, and Purchase Total data. Raw parser payloads from successful
runs will not be stored, which keeps the durable model focused on product data and avoids
accumulating diagnostic data as permanent records.
