# Findings register (example input)

## RES-01 — the parser accepts an empty payload [HIGH]
`parse_payload` returns a record for an empty body instead of raising.
Measured: 412 of 9000 requests produced an empty record downstream.

## RES-02 — two writers, one structure [MEDIUM]
Both `ingest_batch` and `ingest_stream` write into the same table with
different validation. See RES-01 for how the empty payload gets in.

### RES-01 — CLOSED
Fixed by rejecting an empty body at `parse_payload`. Note that RES-02 remains
open; this note mentioning a closed finding must not make RES-02 look closed.
