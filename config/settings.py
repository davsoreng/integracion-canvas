import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación cargada desde variables de entorno o archivo .env."""

    # Canvas LMS
    CANVAS_BASE_URL: str = ""
    CANVAS_API_TOKEN: str = ""

    # Google Auth - Opción 1 (Recomendada para ejecución desatendida): Cuenta de Servicio (JSON string o ruta a archivo .json)
    GOOGLE_SERVICE_ACCOUNT_JSON: Optional[str] = None

    # Google Auth - Opción 2 (Alternativa OAuth 2.0)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""

    # Google Drive & Calendar (Sin IDs personales codificados en el repositorio)
    DRIVE_ROOT_FOLDER_NAME: str = "INGENIERIA"
    DRIVE_ROOT_FOLDER_ID: Optional[str] = None
    CALENDAR_NAME: str = "Canvas - Universidad"
    CALENDAR_ID: Optional[str] = None
    CLASSES_CALENDAR_NAME: str = "CLASES"
    CLASSES_CALENDAR_ID: Optional[str] = None

    # Gemini IA (Opcional)
    GEMINI_API_KEY: Optional[str] = None

    # Logging y Privacidad
    LOG_LEVEL: str = "INFO"
    PUBLIC_LOG_MODE: bool = False  # En GitHub Actions se fuerza a True automáticamente para máxima privacidad

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def is_public_log_mode(self) -> bool:
        """Determina si los logs se están emitiendo en un entorno público como GitHub Actions."""
        return self.PUBLIC_LOG_MODE or os.environ.get("GITHUB_ACTIONS", "").lower() == "true"

    def is_canvas_configured(self) -> bool:
        return bool(self.CANVAS_BASE_URL and self.CANVAS_API_TOKEN)

    def is_google_configured(self) -> bool:
        has_service_account = bool(self.GOOGLE_SERVICE_ACCOUNT_JSON and self.GOOGLE_SERVICE_ACCOUNT_JSON.strip())
        has_oauth = bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET and self.GOOGLE_REFRESH_TOKEN)
        return has_service_account or has_oauth

    def is_gemini_configured(self) -> bool:
        return bool(self.GEMINI_API_KEY)


settings = Settings()
