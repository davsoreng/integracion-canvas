import logging
import re
import sys
from typing import List
from config.settings import settings


class SecretScrubberFilter(logging.Filter):
    """
    Filtro de seguridad que intercepta todos los mensajes de log y enmascara cualquier
    token, clave privada, correo de cuenta de servicio o identificador sensible
    para garantizar que NUNCA se filtre un secreto en logs públicos (ej. GitHub Actions).
    """

    # Patrones regex de credenciales comunes
    _REGEX_PATTERNS = [
        re.compile(r"Bearer\s+[A-Za-z0-9~_\-\.]+", re.IGNORECASE),
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        re.compile(r'"private_key"\s*:\s*"[^"]+"'),
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.iam\.gserviceaccount\.com"),
        re.compile(r"[a-zA-Z0-9._%+-]+@group\.calendar\.google\.com"),
        re.compile(r"(token|key|verifier|access_token|refresh_token|client_secret)=([^&\s'\"]+)", re.IGNORECASE),
    ]

    @staticmethod
    def _get_literal_secrets() -> List[str]:
        candidates = [
            settings.CANVAS_API_TOKEN,
            settings.GEMINI_API_KEY,
            settings.GOOGLE_REFRESH_TOKEN,
            settings.GOOGLE_CLIENT_SECRET,
            settings.GOOGLE_CLIENT_ID,
            settings.GOOGLE_SERVICE_ACCOUNT_JSON,
            settings.DRIVE_ROOT_FOLDER_ID,
            settings.CALENDAR_ID,
            settings.CLASSES_CALENDAR_ID,
        ]
        # Si estamos en modo público (GitHub Actions), ocultar también la URL base del campus
        if settings.is_public_log_mode() and settings.CANVAS_BASE_URL:
            candidates.append(settings.CANVAS_BASE_URL)

        return [s.strip() for s in candidates if s and isinstance(s, str) and len(s.strip()) >= 6]

    @classmethod
    def sanitize(cls, text: str) -> str:
        if not text or not isinstance(text, str):
            return text

        sanitized = text
        for secret in cls._get_literal_secrets():
            if secret in sanitized:
                sanitized = sanitized.replace(secret, "[REDACTED]")

        for pattern in cls._REGEX_PATTERNS:
            sanitized = pattern.sub("[REDACTED]", sanitized)

        return sanitized

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.sanitize(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.sanitize(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        return True


def setup_logger(name: str = "canvas_sync") -> logging.Logger:
    """Configura y devuelve un logger estructurado con sanitización de secretos integrada."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.addFilter(SecretScrubberFilter())

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addFilter(SecretScrubberFilter())

    return logger


logger = setup_logger()
