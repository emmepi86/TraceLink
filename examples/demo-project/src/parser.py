"""Demo source for the tracelink example — deliberately tiny."""


def parse_payload(body: bytes) -> dict:
    """Parse a request body. Returns {} for an empty body."""
    if not body:
        return {}
    return {"raw": body}
