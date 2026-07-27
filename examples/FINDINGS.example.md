# Findings register (example input)

## RES-01 — the parser accepts an empty payload [HIGH]
`parse_payload` returns a record for an empty body instead of raising.
Measured: 412 of 9000 requests produced an empty record downstream.

## RES-02 — two writers, one structure [MEDIUM]
Both `ingest_batch` and `ingest_stream` write into the same table with
different validation. The earlier RES-01 finding is CLOSED, but RES-02 remains
open — and this sentence is the point of the example: a status must never be
read from a body that merely mentions another finding's outcome.

### RES-01 — resolution
### STATUS: CLOSED
### SEVERITY: LOW
Fixed by rejecting an empty body at `parse_payload`.
