"""Demo source for the tracelink example — two writers, one table."""


def ingest_batch(rows):
    """Validated batch path."""
    return [r for r in rows if r]


def ingest_stream(rows):
    """Streaming path, historically with weaker validation."""
    return list(rows)
