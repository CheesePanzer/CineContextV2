import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .settings import LoggingOptions

_HANDLER_MARKER = "_cinecontext_handler"
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|password|passwd|token|access[_-]?token|secret)\b\s*[:=]\s*)"
    r"(?:['\"])?[^\s,;}&'\"]+"
)
_AUTHORIZATION = re.compile(r"(?i)(\bauthorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_SHAPES = re.compile(r"\b(?:sk|pk|api)[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


class ContextDefaultsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name in ("run_id", "state", "operation", "diagnostic_id"):
            if not hasattr(record, name):
                setattr(record, name, "-")
        return True


def sanitize_message(value: object, max_length: int = 500) -> str:
    """Return bounded diagnostic text with common credential forms removed."""
    text = " ".join(str(value).split())
    text = _AUTHORIZATION.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET_VALUE.sub(r"\1[REDACTED]", text)
    text = _KEY_SHAPES.sub("[REDACTED]", text)
    text = re.sub(
        r"(?i)\b(https?://)(?:[^/@\s]+@)([^\s]+)",
        lambda match: match.group(1) + match.group(2),
        text,
    )
    if len(text) > max_length:
        return text[: max(0, max_length - 3)] + "..."
    return text


def safe_url(value: str) -> str:
    """Keep only scheme, hostname, and port for logging."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, hostname + port, "", "", "")) or hostname or "invalid"
    except (TypeError, ValueError):
        return "invalid"


def configure_logging(options: LoggingOptions, base_path: str | Path) -> logging.Logger:
    logger = logging.getLogger("cinecontext")
    logger.setLevel(options.level.value)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s run_id=%(run_id)s state=%(state)s "
        "operation=%(operation)s diagnostic_id=%(diagnostic_id)s %(message)s"
    )
    context_filter = ContextDefaultsFilter()

    def add(handler: logging.Handler) -> None:
        setattr(handler, _HANDLER_MARKER, True)
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        logger.addHandler(handler)

    if options.console_enabled:
        add(logging.StreamHandler())
    if options.file_enabled:
        path = Path(options.path)
        if not path.is_absolute():
            path = Path(base_path).resolve() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        add(RotatingFileHandler(
            path, maxBytes=options.max_bytes, backupCount=options.backup_count,
            encoding="utf-8",
        ))
    return logger
