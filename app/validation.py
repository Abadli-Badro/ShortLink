from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 4096


class InvalidUrlError(ValueError):
    """Raised when a candidate target URL is not acceptable."""


def validate_target_url(raw: str) -> str:
    """Return the normalized target URL or raise InvalidUrlError.

    Runs before anything is written to storage, so a rejected URL is never
    persisted.
    """
    if not isinstance(raw, str):
        raise InvalidUrlError("URL must be a string")

    value = raw.strip()
    if not value:
        raise InvalidUrlError("URL must not be empty")
    if len(value) > MAX_URL_LENGTH:
        raise InvalidUrlError(f"URL must be at most {MAX_URL_LENGTH} characters")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise InvalidUrlError("URL must not contain whitespace or control characters")

    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError as exc:
        raise InvalidUrlError("URL is malformed") from exc

    if parts.scheme not in ALLOWED_SCHEMES:
        raise InvalidUrlError("URL scheme must be http or https")
    if not parts.netloc or not hostname:
        raise InvalidUrlError("URL must include a host")

    return value
