from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración global de la aplicación cargada desde variables de entorno o archivo .env."""

    # Canvas
    CANVAS_BASE_URL: str = ""
    CANVAS_API_TOKEN: str = ""

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""

    # Google Drive & Calendar
    DRIVE_ROOT_FOLDER_NAME: str = "INGENIERIA"
    DRIVE_ROOT_FOLDER_ID: Optional[str] = "1lW4caVjGo5-NbQl7SCxwzB6nJAxOdZ1J"
    CALENDAR_NAME: str = "Canvas - Universidad"
    CLASSES_CALENDAR_NAME: str = "CLASES"

    # Gemini IA
    GEMINI_API_KEY: Optional[str] = None

    # Seguridad del servicio
    SYNC_SECRET_TOKEN: Optional[str] = None

    # Configuración de ejecución y automatización
    PORT: int = 8080
    LOG_LEVEL: str = "INFO"
    AUTO_SYNC_INTERVAL_MINUTES: int = 30  # Sincronización automática periódica en segundo plano
    AUTO_SYNC_ON_STARTUP: bool = True  # Dispara una sincronización al iniciar el servidor

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def is_canvas_configured(self) -> bool:
        return bool(self.CANVAS_BASE_URL and self.CANVAS_API_TOKEN)

    def is_google_configured(self) -> bool:
        return bool(self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET and self.GOOGLE_REFRESH_TOKEN)

    def is_gemini_configured(self) -> bool:
        return bool(self.GEMINI_API_KEY)


settings = Settings()
